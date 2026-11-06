"""KPI engine service: the ONLY entry point through which numbers reach the API, reports and the assistant."""
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.analytics import coverage
from app.analytics.calculators import ALERTS, BY_ENTITY_BATCH, CALCULATORS, SERIES_CALCULATORS, Scope
from app.analytics.domain import OK, SHORT_WINDOW, SUPPRESSED, CalcResult, month_end, month_starts
from app.core.cache import cache
from app.core.config import get_settings
from app.models import (
    FEED_TYPES,
    LEAVE_RECLASSIFY_ACTION,
    LEAVE_TYPES,
    SAFETY_COVERAGE_ATTRIBUTES,
    SAFETY_EVENT_TYPES,
    SAFETY_RECLASSIFY_ACTION,
    AuditLog,
    KpiCalculationRun,
    KpiDefinition,
    KpiFormulaVersion,
    KpiResult,
    LegalEntity,
    PeriodClose,
    Tenant,
)
from app.security.access import AccessContext

# bump whenever calculator output semantics or privacy post-processing change: old cache entries become unreachable
# v3: CalcResult.status_reason (ADR-018); v4: data-feed coverage gate (ADR-020); v5: case-level safety rates,
# out-of-rate cases, fatality alert categories / reclassification marker, required_feed_attributes (ADR-021);
# v6: work patterns - leave-days KPI-055/056, planned-capacity absence denominator (ADR-024)
CACHE_SCHEMA = "v6"


class KpiError(Exception):
    status_code = 400


class KpiNotFound(KpiError):
    status_code = 404


class KpiNotActive(KpiError):
    status_code = 409


class ScopeDenied(KpiError):
    status_code = 403


class PeriodClosed(KpiError):
    status_code = 409


def _memo(db: Session, key: tuple, fn):
    """Request-scoped memo (lives on the Session): a dashboard evaluates ~75 KPIs that share definitions,
    formula versions and the tenant data version. Scoped to one request, so it can never serve stale data
    across requests or workers."""
    store = db.info.setdefault("kpi_memo", {})
    if key not in store:
        store[key] = fn()
    return store[key]


def get_definition(db: Session, code: str) -> KpiDefinition:
    d = _memo(db, ("def", code), lambda: db.execute(
        select(KpiDefinition).where(KpiDefinition.code == code)).scalar_one_or_none())
    if d is None:
        raise KpiNotFound(f"Unknown KPI {code}")
    return d


def get_formula(db: Session, kpi: KpiDefinition, version: int | None = None) -> KpiFormulaVersion:
    q = select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == kpi.id)
    q = q.where(KpiFormulaVersion.version == version) if version else q.where(KpiFormulaVersion.is_current.is_(True))
    f = _memo(db, ("formula", kpi.id, version), lambda: db.execute(q).scalar_one_or_none())
    if f is None:
        raise KpiNotFound(f"No formula version for {kpi.code}" + (f" v{version}" if version else ""))
    return f


def resolve_scope(
    ctx: AccessContext,
    kpi: KpiDefinition,
    entity_ids: set[int] | None,
    project_id: int | None,
    occupation_group: str | None,
) -> Scope:
    """Fold authorization into the calculation scope. Requests outside the user's scope are denied, not
    silently widened."""
    perms = _kpi_perms(kpi)
    for p in perms:
        if not ctx.has(p):
            raise ScopeDenied(f"Missing permission {p} for {kpi.code}")
    # ADR-017: project-limited entities travel with the scope - and therefore with every cache / frozen-result key
    allowed, limits = ctx.scope_for(perms)
    if entity_ids:
        if allowed is not None and not set(entity_ids) <= allowed:
            raise ScopeDenied("Requested legal entity is outside your access scope")
        eff = frozenset(entity_ids)
    else:
        eff = frozenset(allowed) if allowed is not None else None
    return Scope(ctx.tenant_id, eff, project_id, occupation_group, Scope.limits_of(limits, eff))


def _kpi_perms(kpi: KpiDefinition) -> list[str]:
    return ["kpi.read"] + (["cost.read"] if kpi.sensitive else [])


def _threshold_status(kpi: KpiDefinition, v: float | None) -> str | None:
    """Server-side traffic light so that every client applies the same rule."""
    if v is None or kpi.warning_threshold is None or kpi.critical_threshold is None:
        return None
    w, c = float(kpi.warning_threshold), float(kpi.critical_threshold)
    if kpi.direction == "lower_better":
        return "critical" if v >= c else "warning" if v >= w else "good"
    if kpi.direction == "higher_better":
        return "critical" if v <= c else "warning" if v <= w else "good"
    return None


def _status_reason(f: KpiFormulaVersion, days: int, d: dict) -> str | None:
    """ADR-018: the single colour-suppression mechanism. A value that is shown but must not be coloured gets
    threshold_status = null and one reason. Reasons set on the result (the coverage gate's low_coverage - ADR-020 -
    over a calculator reason such as low_exposure) take precedence over the window rule; a result without a value has
    no reason (its status already explains it)."""
    if d["value"] is None:
        return None
    if d.get("status_reason"):
        return d["status_reason"]
    if days < (f.parameters or {}).get("threshold_min_days", 0):
        return SHORT_WINDOW
    return None


def _payload(kpi: KpiDefinition, f: KpiFormulaVersion, scope: Scope, ps: date, pe: date, res, locale: str,
             alerts: list[dict] | None = None) -> dict:
    d = res.to_dict()
    days = (pe - ps).days + 1
    annualized = None
    if (f.parameters or {}).get("annualize") and d["value"] is not None:
        annualized = round(d["value"] * 365 / days, 4)
    judged = annualized if annualized is not None else d["value"]
    d["status_reason"] = _status_reason(f, days, d)
    return {
        "code": kpi.code,
        "name": kpi.name.get(locale) or kpi.name["en"],
        "unit": kpi.unit,
        "direction": kpi.direction,
        "period_start": ps.isoformat(),
        "period_end": pe.isoformat(),
        "scope": scope.as_dict(),
        "kind": "actual",
        **d,
        "thresholds": {
            "target": float(kpi.target_value) if kpi.target_value is not None else None,
            "warning": float(kpi.warning_threshold) if kpi.warning_threshold is not None else None,
            "critical": float(kpi.critical_threshold) if kpi.critical_threshold is not None else None,
        },
        "annualized_value": annualized,
        "threshold_status": None if d["status_reason"] else _threshold_status(kpi, judged),
        "alerts": alerts or [],
        "formula": {"version": f.version, "calculator": f.calculator_key, "text": f.formula_text,
                    "parameters": f.parameters},
        "calculated_at": datetime.now(UTC).isoformat(),
    }


def evaluate(
    db: Session,
    ctx: AccessContext,
    code: str,
    ps: date,
    pe: date,
    *,
    entity_ids: set[int] | None = None,
    project_id: int | None = None,
    occupation_group: str | None = None,
    version: int | None = None,
    precomputed: Callable[[], CalcResult] | None = None,
) -> dict:
    """`precomputed` (internal, B-13): supplies the raw calculator result from a batched series pass; it is only
    consulted where the calculator would run (not frozen, cache miss), so frozen/cache/privacy rules are unchanged."""
    kpi = get_definition(db, code)
    if kpi.status != "active" or not kpi.validated:
        raise KpiNotActive(f"{code} is '{kpi.status}' and not validated for production calculation")
    if pe < ps:
        raise KpiError("period_end must be >= period_start")
    f = get_formula(db, kpi, version)
    calc = CALCULATORS.get(f.calculator_key)
    if calc is None:
        raise KpiNotActive(f"Calculator {f.calculator_key} is not registered")
    scope = resolve_scope(ctx, kpi, entity_ids, project_id, occupation_group)
    dv = _memo(db, ("dv", ctx.tenant_id), lambda: db.execute(
        select(Tenant.data_version).where(Tenant.id == ctx.tenant_id)).scalar() or 0)
    # ADR-020: the coverage gate mode (PO-6 configuration) is part of the key - a mode change never serves old entries
    key = (f"kpi:{CACHE_SCHEMA}:{ctx.tenant_id}:{dv}:{f.id}:{scope.key()}:{ps.isoformat()}:{pe.isoformat()}"
           f":cov={coverage.mode_signature()}")
    frozen = _frozen_result(db, ctx.tenant_id, kpi, f, scope, ps, pe)
    if frozen is not None:
        res, meta = frozen
    else:
        meta = None
        def compute() -> dict:
            raw = precomputed() if precomputed is not None else calc(db, scope, ps, pe, f.parameters or {})
            # B-20 / ADR-020: a missing feed is never shown as a real zero (before privacy, which may still hide it)
            raw = coverage.apply_gate(db, scope, ps, pe, f.parameters or {}, raw)
            return enforce_privacy(raw).to_dict()

        res = CalcResult(**cache.compute_once(key, compute))
    alerts = None
    if f.calculator_key in ALERTS:
        # live even for frozen periods and independent of privacy suppression (ADR-018, WP-2 R2)
        a_scope = Scope(scope.tenant_id, scope.entity_ids, scope.project_id, None)
        a_key = f"kpi:{CACHE_SCHEMA}:alerts:{f.calculator_key}:{ctx.tenant_id}:{dv}:{a_scope.key()}:{ps}:{pe}"
        alerts = cache.compute_once(a_key, lambda: {"alerts": ALERTS[f.calculator_key](db, a_scope, ps, pe)})["alerts"]
    out = _payload(kpi, f, scope, ps, pe, res, ctx.locale, alerts)
    if meta:
        out.update(meta)
    return out


_LINEAGE_SAFE_KEYS = {"tables", "method", "transfer_gap_days", "proration", "excluded_categories", "event_types_counted",
                      "window_days", "reporting_currency", "fx_source", "units", "as_of", "average_headcount",
                      "included_categories", "planned_end_exclusion", "same_employer_basis"}


def enforce_privacy(res: CalcResult) -> CalcResult:
    """Single post-processing step (audit finding #2): a suppressed result - or a result whose population is below
    the publishable minimum - exposes no counts anywhere (value, numerator, denominator, breakdown, lineage)."""
    min_n = get_settings().min_group_size
    persons = (res.lineage or {}).get("persons")
    small = res.status == SUPPRESSED or (persons is not None and persons < min_n)
    if small:
        res.value = res.numerator = res.denominator = res.sample_size = None
        res.breakdown = {}
        res.lineage = {k: v for k, v in (res.lineage or {}).items() if k in _LINEAGE_SAFE_KEYS}
        res.status_reason = None
        if res.status == OK:
            res.status = SUPPRESSED
            res.note = f"Group smaller than the minimum publishable size ({min_n})"
    return res


def _months_all_closed(db: Session, tenant_id: int, ps: date, pe: date) -> bool:
    months = month_starts(ps, pe)
    n = db.execute(select(func.count()).select_from(PeriodClose).where(
        PeriodClose.tenant_id == tenant_id, PeriodClose.period_month.in_(months), PeriodClose.status == "closed")).scalar()
    return n == len(months)


# kpi_results has no status_reason column: a calculator's colour-suppression reason is persisted inside lineage
# under this key (ADR-018) so a frozen result is never coloured when the live one would not be.
_PERSISTED_REASON = "_status_reason"


def _frozen_result(db, tenant_id, kpi, f, scope, ps, pe):
    """Closed periods are frozen (audit finding #6): serve the persisted result of the latest run instead of a live
    recalculation. Data arriving later only flags it stale; changing it requires a reopen + recalculation."""
    if not _memo(db, ("closed", tenant_id, ps, pe), lambda: _months_all_closed(db, tenant_id, ps, pe)):
        return None
    r = db.execute(select(KpiResult).where(
        KpiResult.tenant_id == tenant_id, KpiResult.kpi_id == kpi.id, KpiResult.formula_version_id == f.id,
        KpiResult.period_start == ps, KpiResult.period_end == pe, KpiResult.scope_key == scope.key(),
    ).order_by(KpiResult.id.desc()).limit(1)).scalar()
    if r is None:
        return None
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    lineage = dict(r.lineage or {})
    reason = lineage.pop(_PERSISTED_REASON, None)
    note = "Closed period: frozen result" + (" (newer source data exists - reopen to recalculate)" if r.is_stale else "")
    if r.is_stale and f.calculator_key in _LEAVE_CALCULATORS:
        n = _reclassified_after_close(db, tenant_id, scope, ps, pe)
        if n:
            note += f"; {n} leave record(s) reclassified after closing"
    if r.is_stale and f.calculator_key in _SAFETY_CALCULATORS:
        n = _reclassified_after_close(db, tenant_id, scope, ps, pe, action=SAFETY_RECLASSIFY_ACTION)
        if n:
            note += f"; {n} safety case(s) reclassified after closing"
    res = enforce_privacy(CalcResult(r.status, num(r.value), num(r.numerator), num(r.denominator), r.sample_size,
                                     num(r.quality_score), r.breakdown or {}, lineage, note, reason))
    return res, {"frozen": True, "is_stale": r.is_stale, "run_id": r.run_id}


_LEAVE_CALCULATORS = {"absenteeism_rate", "protected_leave_share", "annual_leave_usage", "annual_leave_nonuse"}
_SAFETY_CALCULATORS = {"ltifr", "trir", "near_miss_rate"}


def _reclassified_after_close(db: Session, tenant_id: int, scope, ps: date, pe: date,
                              action: str = LEAVE_RECLASSIFY_ACTION) -> int:
    """WP-1 3.4 step 5 / ADR-019 (leave), WP-6 HSE 4.3 / ADR-021 (safety cases): a closed period keeps its frozen
    value; the view only says how many records overlapping the period were reclassified after it was closed (a count
    - no person, type or date). Whole entities of the scope only (project-limited entities contribute nothing,
    ADR-017). Safety audit rows carry event_date; leave rows start_date / end_date."""
    closed_at = db.execute(select(func.max(PeriodClose.closed_at)).where(
        PeriodClose.tenant_id == tenant_id, PeriodClose.period_month.in_(month_starts(ps, pe)),
        PeriodClose.status == "closed")).scalar()
    if closed_at is None:
        return 0
    ents = None
    if scope.entity_ids is not None:
        ents = set(scope.entity_ids) - {e for e, _ in scope.project_limits}
    n = 0
    for (d,) in db.execute(select(AuditLog.details).where(
            AuditLog.tenant_id == tenant_id, AuditLog.action == action, AuditLog.at > closed_at)):
        d = d or {}
        start, end = d.get("start_date") or d.get("event_date") or "9999", d.get("end_date") or d.get("event_date")
        if (start <= pe.isoformat() and (end or "0000") >= ps.isoformat()
                and (ents is None or d.get("legal_entity_id") in ents)):
            n += 1
    return n


def _series_batch(db: Session, ctx: AccessContext, code: str, windows: list[tuple[date, date]], kw: dict):
    """B-13: for calculators in SERIES_CALCULATORS, compute every month in one SQL pass - lazily, on the first month
    that actually needs a live calculation. Returns None when no batched path applies (evaluate then does all checks
    and raises its usual errors)."""
    try:
        kpi = get_definition(db, code)
        if kpi.status != "active" or not kpi.validated:
            return None
        f = get_formula(db, kpi, kw.get("version"))
        fn = SERIES_CALCULATORS.get(f.calculator_key)
        if fn is None or CALCULATORS.get(f.calculator_key) is None:
            return None
        scope = resolve_scope(ctx, kpi, kw.get("entity_ids"), kw.get("project_id"), kw.get("occupation_group"))
    except KpiError:
        return None
    results: dict = {}

    def get(w: tuple[date, date]) -> CalcResult:
        if not results:
            results.update(fn(db, scope, windows, f.parameters or {}))
        return results[w]
    return get


def monthly_series(db: Session, ctx: AccessContext, code: str, ps: date, pe: date, **kw) -> list[dict]:
    windows = [(max(m, ps), min(month_end(m), pe)) for m in month_starts(ps, pe)]
    batch = _series_batch(db, ctx, code, windows, kw)
    return [evaluate(db, ctx, code, a, b, precomputed=(lambda w=(a, b): batch(w)) if batch else None, **kw)
            for a, b in windows]


def visible_entities(db: Session, ctx: AccessContext, perm: str = "kpi.read") -> list[LegalEntity]:
    q = select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id).order_by(LegalEntity.name)
    allowed = ctx.entity_filter(perm)
    if allowed is not None:
        q = q.where(LegalEntity.id.in_(allowed or {-1}))
    return list(db.execute(q).scalars())


def by_entity(db: Session, ctx: AccessContext, code: str, ps: date, pe: date, **kw) -> list[dict]:
    kpi = get_definition(db, code)
    ents = visible_entities(db, ctx, "cost.read" if kpi.sensitive else "kpi.read")
    batch_fn = None
    if kpi.status == "active" and not kw.get("version"):
        batch_fn = BY_ENTITY_BATCH.get(get_formula(db, kpi).calculator_key)
    batch: dict[int, CalcResult] = {}

    def from_batch(eid: int) -> CalcResult:
        # computed lazily: only when at least one entity misses the cache, then once for all entities
        if not batch:
            f = get_formula(db, kpi)
            eids = frozenset(e.id for e in ents)
            scope = Scope(ctx.tenant_id, eids, kw.get("project_id"), kw.get("occupation_group"),
                          Scope.limits_of(ctx.scope_for(_kpi_perms(kpi))[1], eids))
            batch.update(batch_fn(db, scope, ps, pe, f.parameters or {}))
        return batch[eid]

    rows = []
    for le in ents:
        pre = (lambda eid=le.id: from_batch(eid)) if batch_fn else None
        r = evaluate(db, ctx, code, ps, pe, entity_ids={le.id}, precomputed=pre, **kw)
        r["legal_entity"] = {"id": le.id, "code": le.code, "name": le.name, "type": le.entity_type}
        rows.append(r)
    return rows


# ------------------------------------------------------------------ persistence, versions, periods


def is_period_closed(db: Session, tenant_id: int, ps: date, pe: date) -> bool:
    months = month_starts(ps, pe)
    return db.execute(
        select(PeriodClose.id).where(PeriodClose.tenant_id == tenant_id, PeriodClose.period_month.in_(months),
                                     PeriodClose.status == "closed")
    ).first() is not None


def run_calculation(db: Session, ctx: AccessContext, codes: list[str], ps: date, pe: date, *,
                    entity_ids: set[int] | None = None, project_id: int | None = None,
                    trigger: str = "on_demand") -> KpiCalculationRun:
    if is_period_closed(db, ctx.tenant_id, ps, pe):
        raise PeriodClosed("Period is closed; reopen it (period.close permission) before recalculating")
    run = KpiCalculationRun(tenant_id=ctx.tenant_id, triggered_by=ctx.user_id, trigger=trigger, period_start=ps,
                            period_end=pe, scope={"entity_ids": sorted(entity_ids) if entity_ids else None,
                                                  "project_id": project_id, "codes": codes})
    db.add(run)
    db.flush()
    try:
        for code in codes:
            kpi = get_definition(db, code)
            f = get_formula(db, kpi)
            r = evaluate(db, ctx, code, ps, pe, entity_ids=entity_ids, project_id=project_id)
            scope = resolve_scope(ctx, kpi, entity_ids, project_id, None)
            # supersede earlier results for the same kpi/period/scope (kept for audit, flagged stale)
            db.execute(update(KpiResult).where(KpiResult.tenant_id == ctx.tenant_id, KpiResult.kpi_id == kpi.id,
                                               KpiResult.period_start == ps, KpiResult.period_end == pe,
                                               KpiResult.scope_key == scope.key()).values(is_stale=True))
            db.add(KpiResult(tenant_id=ctx.tenant_id, run_id=run.id, kpi_id=kpi.id, formula_version_id=f.id,
                             period_start=ps, period_end=pe, scope_key=scope.key(), value=r["value"],
                             numerator=r["numerator"], denominator=r["denominator"], status=r["status"],
                             quality_score=r["quality_score"], sample_size=r["sample_size"],
                             breakdown=r["breakdown"],
                             lineage={**r["lineage"], **({_PERSISTED_REASON: r["status_reason"]}
                                                         if r["status_reason"] else {})}))
        run.status = "succeeded"
    except Exception as exc:  # recorded, then re-raised to the caller
        run.status = "failed"
        run.error = str(exc)[:2000]
        raise
    finally:
        run.finished_at = datetime.now(UTC)
        db.commit()
    return run


def can_close_periods(db: Session, ctx: AccessContext) -> bool:
    """Product decision (2026-11-05): closing/reopening reporting periods belongs to the MAIN CONTRACTOR.
    Allowed for tenant-wide period.close holders, or when the period.close scope contains a main_contractor entity;
    users scoped only to subcontractors cannot close or reopen."""
    if not ctx.has("period.close"):
        return False
    scope = ctx.entity_filter("period.close")
    if scope is None:
        return True
    scope = {e for e in scope if ctx.covers_entity("period.close", e)}  # ADR-017: whole-entity grants only
    return db.execute(select(LegalEntity.id).where(
        LegalEntity.tenant_id == ctx.tenant_id, LegalEntity.id.in_(scope or {-1}),
        LegalEntity.entity_type == "main_contractor")).first() is not None


def freeze_period(db: Session, ctx: AccessContext, period_month: date) -> int:
    """B-18: persist results for a month that is about to be closed, so a closed period always serves frozen values
    (see `_frozen_result`). The closer is the main contractor (can_close_periods), so the month is frozen for EVERY
    scope readers can use: tenant-wide and each legal entity, cost KPIs included. Freezing stores results; it never
    returns them - reading still goes through each reader's own scope (resolve_scope / kpi_run_get). The runs are
    recorded as system runs (triggered_by NULL), so the closer cannot read wider data through GET /kpi/runs/{id}.
    Returns the number of persisted results."""
    ps = date(period_month.year, period_month.month, 1)
    pe = month_end(ps)
    defs = db.execute(select(KpiDefinition).where(KpiDefinition.status == "active", KpiDefinition.validated.is_(True))
                      .order_by(KpiDefinition.code)).scalars().all()
    codes = [d.code for d in defs if get_formula(db, d).calculator_key in CALCULATORS]
    if not codes:
        return 0
    system = AccessContext(user_id=None, tenant_id=ctx.tenant_id, email="system:period_close", locale=ctx.locale,
                           grants={"kpi.read": None, "cost.read": None})
    scopes: list[set[int] | None] = [None]
    scopes += [{le.id} for le in visible_entities(db, system, "kpi.read") if le.entity_type != "client"]
    for ents in scopes:
        run_calculation(db, system, codes, ps, pe, entity_ids=ents, trigger="period_close")
    return len(codes) * len(scopes)


def required_feeds_of_active_kpis(db: Session) -> set[str]:
    """Union of `required_feeds` over the current formula versions of active KPIs (period-close coverage list)."""
    rows = db.execute(select(KpiFormulaVersion.parameters).join(KpiDefinition, KpiDefinition.id == KpiFormulaVersion.kpi_id)
                      .where(KpiFormulaVersion.is_current.is_(True), KpiDefinition.status == "active")).scalars()
    return {f for p in rows for f in ((p or {}).get("required_feeds") or ())}


# Allowed formula parameters per calculator (audit finding #7): unknown keys / wrong types are rejected.
_LEAVE_TYPES = LEAVE_TYPES  # ADR-019: includes work_injury
_EVENT_TYPES = set(SAFETY_EVENT_TYPES)
_TERM = {"voluntary", "employer_initiated", "contract_end", "project_end", "retirement", "death", "other", "unknown"}
PARAM_RULES = {
    "absence_types": lambda v: isinstance(v, list) and v and set(v) <= _LEAVE_TYPES,
    # ADR-019: every leave type is classified explicitly - absence_types + excluded_types must cover LEAVE_TYPES,
    # so a new type can never drop out of an absence KPI silently (WP-1 2.2)
    "excluded_types": lambda v: isinstance(v, list) and set(v) <= _LEAVE_TYPES and len(set(v)) == len(v),
    "long_absence_types": lambda v: isinstance(v, list) and v and set(v) <= _LEAVE_TYPES - {"annual"},
    "annualize": lambda v: isinstance(v, bool),
    "min_coverage": lambda v: isinstance(v, int | float) and 0 < v <= 1,
    "multiplier": lambda v: v in (200_000, 1_000_000),
    "event_types": lambda v: isinstance(v, list) and v and set(v) <= _EVENT_TYPES,
    "reporting_currency": lambda v: isinstance(v, str) and len(v) == 3 and v.isalpha() and v.isupper(),
    "days": lambda v: isinstance(v, int) and 1 <= v <= 365,
    "exclude_categories": lambda v: isinstance(v, list) and set(v) <= _TERM,
    "include_categories": lambda v: isinstance(v, list) and bool(v) and set(v) <= _TERM,
    "hours_per_day": lambda v: isinstance(v, int | float) and 4 <= v <= 12,
    "threshold_min_days": lambda v: isinstance(v, int) and 1 <= v <= 400,
    # WP-2 R1 / ADR-018: colour only when hours worked in the window >= this value
    "threshold_min_exposure_hours": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 10_000_000,
    # B-20 / ADR-020 coverage gate (engine-level, every calculator): source feeds that must be declared complete
    "required_feeds": lambda v: isinstance(v, list) and bool(v) and set(v) <= set(FEED_TYPES) and len(set(v)) == len(v),
    "min_feed_coverage": lambda v: isinstance(v, int | float) and not isinstance(v, bool) and 0 < v <= 1,
    # ADR-021: a declared feed attribute that contradicts this makes the cell uncovered (KPI-043: a safety declaration
    # with includes_near_miss = false does not cover the near-miss rate). Only the safety feed has attributes.
    "required_feed_attributes": lambda v: isinstance(v, dict) and bool(v) and set(v) <= {"safety"} and all(
        isinstance(a, dict) and a and set(a) <= set(SAFETY_COVERAGE_ATTRIBUTES)
        and all(isinstance(b, bool) for b in a.values()) for a in v.values()),
    # ADR-021 / WP-6 HSE 3.7: third-party cases and subcontractor / agency cases without hours in the denominator
    # are reported as out-of-rate cases instead of entering the numerator
    "exclude_out_of_rate_cases": lambda v: isinstance(v, bool),  # older formula versions only (superseded by D-10)
    # Decision D-10 (PO-3): HSE covers own employees only - cases of any other worker category never enter the rate
    "own_employees_only": lambda v: isinstance(v, bool),
    # ADR-023 / WP-5 3.4: which early leavers are planned contract/project ends (none = v1, category = C1 only,
    # verified = planned_end_verified IS TRUE; HR Director S-1 -> verified)
    "planned_end_exclusion": lambda v: v in ("none", "category", "verified"),
    # WP-5 3.4 / S-5: note when early turnover falls while the planned-end share rises by >= this many points (3 periods)
    "planned_end_alarm_points": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 100,
    # 'same employer' for repeated planned ends - legal_entity | group (decision D-11 / PO-4: catalog uses group)
    "same_employer_basis": lambda v: v in ("legal_entity", "group"),
    # ADR-023 / WP-5 3.5 (S-4): unknown-category share bands [colour limit, value limit] for KPI-020
    "unknown_share_bands": lambda v: isinstance(v, list) and len(v) == 2 and all(
        isinstance(x, int | float) and not isinstance(x, bool) for x in v) and 0 < v[0] < v[1] < 1,
    # WP-5 review condition 1: (unknown + other) / all separations above this -> low-information note
    "low_information_share": lambda v: isinstance(v, int | float) and not isinstance(v, bool) and 0 < v < 1,
    # WP-5 review condition 4: breakdown of counted separations with these stored SGK exit codes (23 / 25)
    "voluntary_just_cause_codes": lambda v: isinstance(v, list) and bool(v) and all(
        isinstance(x, str) and len(x) == 2 and x.isdigit() for x in v),
    # ADR-024 / WP-4 3.4: KPI-055/056 count leave DAYS by work pattern (hours_per_day = fallback divisor only)
    "day_basis": lambda v: v == "leave_days",
    # WP-4 review condition 1 / S-1: share of the eligible population with a known work pattern (NOT min_coverage,
    # which is KPI-004's productive-hours coverage)
    "min_pattern_coverage": lambda v: isinstance(v, int | float) and not isinstance(v, bool) and 0 < v <= 1,
    # WP-4 3.5 + review condition 2: planned_hours (v1) | planned_capacity (leave hours added back per employee-day)
    "denominator_basis": lambda v: v in ("planned_hours", "planned_capacity"),
}


# Parameters each calculator actually reads (HR sprint-1 finding: a valid-looking key the calculator ignores, e.g.
# exclude_categories on early_turnover_90, silently changed nothing). Engine-level keys apply to every calculator.
_ENGINE_PARAMS = {"annualize", "threshold_min_days", "required_feeds", "min_feed_coverage", "required_feed_attributes"}
CALCULATOR_PARAMS: dict[str, set[str]] = {
    "turnover_rate": {"exclude_categories", "include_categories", "unknown_share_bands", "low_information_share",
                      "voluntary_just_cause_codes"},
    "absenteeism_rate": {"absence_types", "excluded_types", "denominator_basis"},
    "protected_leave_share": {"absence_types", "denominator_basis"},
    "annual_leave_usage": {"hours_per_day", "day_basis", "min_pattern_coverage"},
    "annual_leave_nonuse": {"hours_per_day", "long_absence_types", "day_basis", "min_pattern_coverage"},
    "capacity_utilization": {"min_coverage"},
    "unit_labor_cost": {"reporting_currency"},
    "ltifr": {"event_types", "multiplier", "threshold_min_exposure_hours", "exclude_out_of_rate_cases",
              "own_employees_only"},
    "trir": {"event_types", "multiplier", "threshold_min_exposure_hours", "exclude_out_of_rate_cases",
             "own_employees_only"},
    "near_miss_rate": {"multiplier", "threshold_min_exposure_hours"},
    "early_turnover_90": {"days", "planned_end_exclusion", "planned_end_alarm_points", "same_employer_basis"},
    "labor_cost_per_employee": {"reporting_currency"},
    "overtime_cost_share": {"reporting_currency"},
}


def validate_parameters(params: dict, calculator_key: str | None = None) -> None:
    allowed = None if calculator_key is None else _ENGINE_PARAMS | CALCULATOR_PARAMS.get(calculator_key, set())
    for k, v in params.items():
        rule = PARAM_RULES.get(k)
        if rule is None:
            raise KpiError(f"Unknown formula parameter '{k}'")
        if allowed is not None and k not in allowed:
            raise KpiError(f"Formula parameter '{k}' is not used by calculator '{calculator_key}'")
        if not rule(v):
            raise KpiError(f"Invalid value for formula parameter '{k}'")
    if "excluded_types" in params:
        inc, exc = set(params.get("absence_types") or ()), set(params["excluded_types"])
        if not inc or inc & exc or inc | exc != _LEAVE_TYPES:
            raise KpiError("absence_types and excluded_types must be disjoint and together list every leave type ("
                           + ", ".join(sorted(_LEAVE_TYPES)) + ")")


def create_formula_version(db: Session, ctx: AccessContext, code: str, *, parameters: dict,
                           formula_text: str | None, change_note: str) -> KpiFormulaVersion:
    """The KPI catalog is GLOBAL (shared by all tenants), so changing it is a platform-operator action that
    requires admin.platform in addition to kpi.manage. Tenant-specific overrides are backlog B-09."""
    if not ctx.has("admin.platform"):
        raise ScopeDenied("Formula versions are global; admin.platform is required")
    kpi = get_definition(db, code)
    # row lock serialises concurrent version bumps; unique(kpi_id, version) is the final guard
    cur = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == kpi.id,
                                                     KpiFormulaVersion.is_current.is_(True)).with_for_update()).scalar_one()
    validate_parameters(parameters, cur.calculator_key)
    cur.is_current = False
    new = KpiFormulaVersion(kpi_id=kpi.id, version=cur.version + 1, calculator_key=cur.calculator_key,
                            formula_text=formula_text or cur.formula_text, numerator_def=cur.numerator_def,
                            denominator_def=cur.denominator_def, parameters=parameters, is_current=True,
                            change_note=change_note, created_by=ctx.user_id)
    db.add(new)
    db.commit()
    db.info.pop("kpi_memo", None)
    return new


def bump_data_version(db: Session, tenant_id: int) -> None:
    """Invalidate every cached KPI result of the tenant (new cache key namespace)."""
    db.execute(update(Tenant).where(Tenant.id == tenant_id).values(data_version=Tenant.data_version + 1))


def mark_stale_from(db: Session, tenant_id: int, from_date: date) -> int:
    """Late-arriving / back-dated data: flag persisted results whose period ends on/after from_date."""
    bump_data_version(db, tenant_id)
    db.info.pop("kpi_memo", None)
    db.info.pop("hours_memo", None)
    db.info.pop("headcount_memo", None)
    db.info.pop("ts_dirty", None)
    db.info.pop("coverage_memo", None)
    db.info.pop("holiday_memo", None)
    db.info.setdefault("warm_tenants", set()).add(tenant_id)  # app.services.prewarm runs after the commit
    res = db.execute(update(KpiResult).where(KpiResult.tenant_id == tenant_id, KpiResult.period_end >= from_date,
                                             KpiResult.is_stale.is_(False)).values(is_stale=True))
    return res.rowcount or 0
