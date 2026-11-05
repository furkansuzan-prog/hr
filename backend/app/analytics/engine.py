"""KPI engine service: the ONLY entry point through which numbers reach the API, reports and the assistant."""
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.analytics.calculators import BY_ENTITY_BATCH, CALCULATORS, SERIES_CALCULATORS, Scope
from app.analytics.domain import OK, SUPPRESSED, CalcResult, month_end, month_starts
from app.core.cache import cache
from app.core.config import get_settings
from app.models import (
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
CACHE_SCHEMA = "v2"


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
    perms = ["kpi.read"] + (["cost.read"] if kpi.sensitive else [])
    allowed: set[int] | None = None
    first = True
    for p in perms:
        if not ctx.has(p):
            raise ScopeDenied(f"Missing permission {p} for {kpi.code}")
        s = ctx.entity_filter(p)
        if first:
            allowed, first = s, False
        elif s is not None:
            allowed = s if allowed is None else allowed & s
    if entity_ids:
        if allowed is not None and not set(entity_ids) <= allowed:
            raise ScopeDenied("Requested legal entity is outside your access scope")
        eff = frozenset(entity_ids)
    else:
        eff = frozenset(allowed) if allowed is not None else None
    return Scope(ctx.tenant_id, eff, project_id, occupation_group)


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


def _payload(kpi: KpiDefinition, f: KpiFormulaVersion, scope: Scope, ps: date, pe: date, res, locale: str) -> dict:
    d = res.to_dict()
    days = (pe - ps).days + 1
    annualized = None
    if (f.parameters or {}).get("annualize") and d["value"] is not None:
        annualized = round(d["value"] * 365 / days, 4)
    judged = annualized if annualized is not None else d["value"]
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
        "threshold_status": (None if days < (f.parameters or {}).get("threshold_min_days", 0)
                             else _threshold_status(kpi, judged)),
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
    key = f"kpi:{CACHE_SCHEMA}:{ctx.tenant_id}:{dv}:{f.id}:{scope.key()}:{ps.isoformat()}:{pe.isoformat()}"
    frozen = _frozen_result(db, ctx.tenant_id, kpi, f, scope, ps, pe)
    if frozen is not None:
        res, meta = frozen
    else:
        meta = None
        def compute() -> dict:
            raw = precomputed() if precomputed is not None else calc(db, scope, ps, pe, f.parameters or {})
            return enforce_privacy(raw).to_dict()

        res = CalcResult(**cache.compute_once(key, compute))
    out = _payload(kpi, f, scope, ps, pe, res, ctx.locale)
    if meta:
        out.update(meta)
    return out


_LINEAGE_SAFE_KEYS = {"tables", "method", "transfer_gap_days", "proration", "excluded_categories", "event_types_counted",
                      "window_days", "reporting_currency", "fx_source", "units", "as_of", "average_headcount",
                      "included_categories"}


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
        if res.status == OK:
            res.status = SUPPRESSED
            res.note = f"Group smaller than the minimum publishable size ({min_n})"
    return res


def _months_all_closed(db: Session, tenant_id: int, ps: date, pe: date) -> bool:
    months = month_starts(ps, pe)
    n = db.execute(select(func.count()).select_from(PeriodClose).where(
        PeriodClose.tenant_id == tenant_id, PeriodClose.period_month.in_(months), PeriodClose.status == "closed")).scalar()
    return n == len(months)


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
    res = enforce_privacy(CalcResult(r.status, num(r.value), num(r.numerator), num(r.denominator), r.sample_size,
                                     num(r.quality_score), r.breakdown or {}, r.lineage or {},
                                     "Closed period: frozen result" + (" (newer source data exists - reopen to "
                                                                      "recalculate)" if r.is_stale else "")))
    return res, {"frozen": True, "is_stale": r.is_stale, "run_id": r.run_id}


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
            scope = Scope(ctx.tenant_id, frozenset(e.id for e in ents), kw.get("project_id"),
                          kw.get("occupation_group"))
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
                             breakdown=r["breakdown"], lineage=r["lineage"]))
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


# Allowed formula parameters per calculator (audit finding #7): unknown keys / wrong types are rejected.
_LEAVE_TYPES = {"annual", "sick", "unpaid", "unexcused", "public_holiday", "parental", "other"}
_EVENT_TYPES = {"fatality", "lost_time_injury", "restricted_work", "medical_treatment", "first_aid", "near_miss"}
_TERM = {"voluntary", "employer_initiated", "contract_end", "project_end", "retirement", "death", "other", "unknown"}
PARAM_RULES = {
    "absence_types": lambda v: isinstance(v, list) and v and set(v) <= _LEAVE_TYPES,
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
}


def validate_parameters(params: dict) -> None:
    for k, v in params.items():
        rule = PARAM_RULES.get(k)
        if rule is None:
            raise KpiError(f"Unknown formula parameter '{k}'")
        if not rule(v):
            raise KpiError(f"Invalid value for formula parameter '{k}'")


def create_formula_version(db: Session, ctx: AccessContext, code: str, *, parameters: dict,
                           formula_text: str | None, change_note: str) -> KpiFormulaVersion:
    """The KPI catalog is GLOBAL (shared by all tenants), so changing it is a platform-operator action that
    requires admin.platform in addition to kpi.manage. Tenant-specific overrides are backlog B-09."""
    if not ctx.has("admin.platform"):
        raise ScopeDenied("Formula versions are global; admin.platform is required")
    validate_parameters(parameters)
    kpi = get_definition(db, code)
    # row lock serialises concurrent version bumps; unique(kpi_id, version) is the final guard
    cur = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == kpi.id,
                                                     KpiFormulaVersion.is_current.is_(True)).with_for_update()).scalar_one()
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
    db.info.setdefault("warm_tenants", set()).add(tenant_id)  # app.services.prewarm runs after the commit
    res = db.execute(update(KpiResult).where(KpiResult.tenant_id == tenant_id, KpiResult.period_end >= from_date,
                                             KpiResult.is_stale.is_(False)).values(is_stale=True))
    return res.rowcount or 0
