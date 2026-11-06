"""Dashboard compositions shared by the API and the cache pre-warmer (B-21), so both hit the same cache keys."""
import hashlib
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import coverage, engine
from app.analytics.domain import month_end
from app.core.cache import cache
from app.models import KpiFormulaVersion, Tenant
from app.security.access import AccessContext
from app.security.features import flags_signature, missing_permissions

EXEC_KPIS = ["KPI-016", "KPI-017", "KPI-001", "KPI-003", "KPI-007", "KPI-004", "KPI-005", "KPI-008", "KPI-014A",
             "KPI-009", "KPI-006"]
EXEC_TRENDS = ("KPI-016", "KPI-001", "KPI-007", "KPI-003")


def default_period(today: date | None = None) -> tuple[date, date]:
    """Last 12 complete months."""
    today = today or date.today()
    pe = date(today.year, today.month, 1) - timedelta(days=1)
    m = pe.month - 1 - 11
    ps = date(pe.year + m // 12, m % 12 + 1, 1)
    return ps, month_end(pe)


def _response_key(db: Session, ctx: AccessContext, ps: date, pe: date, kw: dict) -> str:
    """Whole-response cache key. Everything that can change the payload is in it: tenant data version (bumped by
    every analytics write and by period close/reopen), the catalog version (any formula version), the caller's
    effective kpi.read / cost.read / health.read scopes (project limits included, ADR-017; health.read hides
    health-derived breakdowns, D-12), filters, period, display language, the coverage gate mode (ADR-020) and the
    feature flags (D-9, ADR-025)."""
    dv = db.execute(select(Tenant.data_version).where(Tenant.id == ctx.tenant_id)).scalar() or 0
    catv = db.execute(select(func.max(KpiFormulaVersion.id))).scalar() or 0
    filt = f"{sorted(kw['entity_ids']) if kw['entity_ids'] else ''}|{kw['project_id']}|{kw['occupation_group']}"
    raw = (f"{ctx.scope_signature('kpi.read')}#{ctx.scope_signature('cost.read')}#{ctx.scope_signature('health.read')}"
           f"#{flags_signature()}#{filt}")
    return (f"dash:{engine.CACHE_SCHEMA}:{ctx.tenant_id}:{dv}:{catv}:{ps}:{pe}:{ctx.locale}:"
            f"cov={coverage.mode_signature()}:"
            + hashlib.sha256(raw.encode()).hexdigest()[:32])


def executive(db: Session, ctx: AccessContext, ps: date, pe: date, *, entity_ids: set[int] | None = None,
              project_id: int | None = None, occupation_group: str | None = None) -> dict:
    """Executive dashboard. The whole response is cached (B-21: under load the warm path was dominated by ~260
    per-KPI cache reads and JSON work per request). Authorization errors are raised before anything is cached,
    because every KPI evaluation inside checks the scope."""
    kw = {"entity_ids": entity_ids, "project_id": project_id, "occupation_group": occupation_group}
    return cache.compute_once(_response_key(db, ctx, ps, pe, kw),
                              lambda: _executive(db, ctx, ps, pe, kw))


def _executive(db: Session, ctx: AccessContext, ps: date, pe: date, kw: dict) -> dict:
    entity_ids, project_id = kw["entity_ids"], kw["project_id"]
    # a tile whose extra permission (cost.read / health.read) the caller lacks is left out, never an error
    tiles = [engine.evaluate(db, ctx, c, ps, pe, **kw) for c in EXEC_KPIS
             if not missing_permissions(ctx, engine.get_definition(db, c))]
    if ctx.has("cost.read"):
        tiles.append(engine.evaluate(db, ctx, "KPI-019", ps, pe, **kw))
    trends = {c: [{"period": s["period_start"][:7], "value": s["value"], "status": s["status"]}
                  for s in engine.monthly_series(db, ctx, c, ps, pe, **kw)]
              for c in EXEC_TRENDS}
    distribution = [{"entity": r["legal_entity"]["name"], "type": r["legal_entity"]["type"], "value": r["value"]}
                    for r in engine.by_entity(db, ctx, "KPI-016", ps, pe, project_id=project_id)
                    if r["legal_entity"]["type"] != "client"
                    and (not entity_ids or r["legal_entity"]["id"] in entity_ids)]
    return {"period_start": ps, "period_end": pe, "filters": kw, "tiles": tiles, "trends": trends,
            "headcount_by_entity": distribution, "demo_data": True}
