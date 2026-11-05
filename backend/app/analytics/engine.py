"""KPI engine service: the ONLY entry point through which numbers reach the API, reports and the assistant."""
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analytics.calculators import CALCULATORS, Scope
from app.analytics.domain import CalcResult, month_end, month_starts
from app.core.cache import cache
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
        "threshold_status": _threshold_status(kpi, judged),
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
) -> dict:
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
    key = f"kpi:{ctx.tenant_id}:{dv}:{f.id}:{scope.key()}:{ps.isoformat()}:{pe.isoformat()}"
    hit = cache.get(key)
    if hit is not None:
        res = CalcResult(**hit)
    else:
        res = calc(db, scope, ps, pe, f.parameters or {})
        cache.set(key, res.to_dict())
    return _payload(kpi, f, scope, ps, pe, res, ctx.locale)


def monthly_series(db: Session, ctx: AccessContext, code: str, ps: date, pe: date, **kw) -> list[dict]:
    out = []
    for m in month_starts(ps, pe):
        out.append(evaluate(db, ctx, code, max(m, ps), min(month_end(m), pe), **kw))
    return out


def visible_entities(db: Session, ctx: AccessContext, perm: str = "kpi.read") -> list[LegalEntity]:
    q = select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id).order_by(LegalEntity.name)
    allowed = ctx.entity_filter(perm)
    if allowed is not None:
        q = q.where(LegalEntity.id.in_(allowed or {-1}))
    return list(db.execute(q).scalars())


def by_entity(db: Session, ctx: AccessContext, code: str, ps: date, pe: date, **kw) -> list[dict]:
    rows = []
    for le in visible_entities(db, ctx, "cost.read" if get_definition(db, code).sensitive else "kpi.read"):
        r = evaluate(db, ctx, code, ps, pe, entity_ids={le.id}, **kw)
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


def create_formula_version(db: Session, ctx: AccessContext, code: str, *, parameters: dict,
                           formula_text: str | None, change_note: str) -> KpiFormulaVersion:
    kpi = get_definition(db, code)
    cur = get_formula(db, kpi)
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
    res = db.execute(update(KpiResult).where(KpiResult.tenant_id == tenant_id, KpiResult.period_end >= from_date,
                                             KpiResult.is_stale.is_(False)).values(is_stale=True))
    return res.rowcount or 0
