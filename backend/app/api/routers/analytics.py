"""KPI engine, dashboards, forecasting, assistant, reports, imports."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import assistant
from app.ai.service import headcount_forecast, kpi_anomalies
from app.analytics import engine
from app.api.common import Filters
from app.core.config import get_settings
from app.core.db import get_db
from app.models import (
    ImportBatch,
    KpiCalculationRun,
    KpiDefinition,
    KpiFormulaVersion,
    KpiResult,
    PeriodClose,
)
from app.security.access import AccessContext
from app.security.deps import require, write_audit
from app.services import imports as imp
from app.services import reports as rep

router = APIRouter(prefix="/api/v1")


def _kpi_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except engine.KpiError as e:
        if isinstance(e, engine.ScopeDenied):  # audit finding #15: scope denials on analytics are audited too
            ctx = next((x for x in a if isinstance(x, AccessContext)), None)
            if ctx is not None:
                write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="authz.scope_denied",
                            entity_type="kpi", outcome="denied", details={"fn": fn.__name__, "reason": str(e)})
        raise HTTPException(e.status_code, str(e)) from None


# ------------------------------------------------------------------ KPI catalog


def _def_out(d: KpiDefinition, locale: str, versions: list[KpiFormulaVersion] | None = None) -> dict:
    out = {"code": d.code, "name": d.name.get(locale) or d.name["en"], "names": d.name, "family": d.family,
           "module": d.module, "unit": d.unit, "direction": d.direction, "status": d.status, "validated": d.validated,
           "sensitive": d.sensitive, "description": d.description, "purpose": d.purpose, "frequency": d.frequency,
           "data_sources": d.data_sources, "data_owner_role": d.data_owner_role, "scope_levels": d.scope_levels,
           "inclusion_rules": d.inclusion_rules, "exclusion_rules": d.exclusion_rules,
           "missing_data_behavior": d.missing_data_behavior, "standard_refs": d.standard_refs,
           "target": float(d.target_value) if d.target_value is not None else None,
           "warning": float(d.warning_threshold) if d.warning_threshold is not None else None,
           "critical": float(d.critical_threshold) if d.critical_threshold is not None else None}
    if versions is not None:
        out["formula_versions"] = [{"version": v.version, "calculator": v.calculator_key, "formula": v.formula_text,
                                    "numerator": v.numerator_def, "denominator": v.denominator_def,
                                    "parameters": v.parameters, "is_current": v.is_current,
                                    "change_note": v.change_note, "created_at": v.created_at} for v in versions]
    return out


@router.get("/kpi/definitions", tags=["kpi"])
def kpi_definitions(status: str | None = None, ctx: AccessContext = Depends(require("kpi.read")),
                    db: Session = Depends(get_db)):
    q = select(KpiDefinition).order_by(KpiDefinition.code)
    if status:
        q = q.where(KpiDefinition.status == status)
    return [_def_out(d, ctx.locale) for d in db.execute(q).scalars()]


@router.get("/kpi/definitions/{code}", tags=["kpi"])
def kpi_definition(code: str, ctx: AccessContext = Depends(require("kpi.read")), db: Session = Depends(get_db)):
    d = _kpi_call(engine.get_definition, db, code)
    versions = list(db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == d.id)
                               .order_by(KpiFormulaVersion.version)).scalars())
    return _def_out(d, ctx.locale, versions)


class FormulaVersionIn(BaseModel):
    parameters: dict
    formula_text: str | None = None
    change_note: str = Field(min_length=5, max_length=1000)


@router.post("/kpi/definitions/{code}/versions", tags=["kpi"], status_code=201)
def new_formula_version(code: str, body: FormulaVersionIn, ctx: AccessContext = Depends(require("kpi.manage")),
                        db: Session = Depends(get_db)):
    v = _kpi_call(engine.create_formula_version, db, ctx, code, parameters=body.parameters,
                  formula_text=body.formula_text, change_note=body.change_note)
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="kpi.formula_version", entity_type="kpi",
                entity_id=code, details={"version": v.version, "parameters": body.parameters})
    return {"code": code, "version": v.version}


# ------------------------------------------------------------------ KPI evaluation


@router.get("/kpi/evaluate/{code}", tags=["kpi"])
def kpi_evaluate(code: str, version: int | None = None, f: Filters = Depends(),
                 ctx: AccessContext = Depends(require("kpi.read")), db: Session = Depends(get_db)):
    return _kpi_call(engine.evaluate, db, ctx, code, f.ps, f.pe, version=version, **f.kw())


@router.get("/kpi/series/{code}", tags=["kpi"])
def kpi_series(code: str, f: Filters = Depends(), ctx: AccessContext = Depends(require("kpi.read")),
               db: Session = Depends(get_db)):
    return _kpi_call(engine.monthly_series, db, ctx, code, f.ps, f.pe, **f.kw())


@router.get("/kpi/by-entity/{code}", tags=["kpi"])
def kpi_by_entity(code: str, f: Filters = Depends(), ctx: AccessContext = Depends(require("kpi.read")),
                  db: Session = Depends(get_db)):
    rows = _kpi_call(engine.by_entity, db, ctx, code, f.ps, f.pe, project_id=f.project_id,
                     occupation_group=f.occupation_group)
    if f.entity_ids:
        rows = [r for r in rows if r["legal_entity"]["id"] in f.entity_ids]
    return rows


class RunIn(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=60)
    period_start: date
    period_end: date
    entity_ids: list[int] | None = None
    project_id: int | None = None


@router.post("/kpi/runs", tags=["kpi"], status_code=201)
def kpi_run(body: RunIn, ctx: AccessContext = Depends(require("kpi.calculate")), db: Session = Depends(get_db)):
    run = _kpi_call(engine.run_calculation, db, ctx, body.codes, body.period_start, body.period_end,
                    entity_ids=set(body.entity_ids) if body.entity_ids else None, project_id=body.project_id)
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="kpi.run", entity_type="kpi_run",
                entity_id=str(run.id), details={"codes": body.codes})
    return {"run_id": run.id, "status": run.status}


@router.get("/kpi/runs/{run_id}", tags=["kpi"])
def kpi_run_get(run_id: int, ctx: AccessContext = Depends(require("kpi.read")), db: Session = Depends(get_db)):
    run = db.get(KpiCalculationRun, run_id)
    if run is None or run.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Not found")
    # audit finding #1: persisted runs may cover a wider scope than the reader; only tenant-wide readers or the
    # user who triggered the run may read it, and cost KPIs additionally need tenant-wide cost.read
    if ctx.entity_filter("kpi.read") is not None and run.triggered_by != ctx.user_id:
        write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="authz.scope_denied", entity_type="kpi_run",
                    entity_id=str(run_id), outcome="denied")
        raise HTTPException(404, "Not found")
    cost_ok = ctx.has("cost.read") and (ctx.entity_filter("cost.read") is None or run.triggered_by == ctx.user_id)
    rows = db.execute(select(KpiResult, KpiDefinition.code, KpiFormulaVersion.version)
                      .join(KpiDefinition, KpiDefinition.id == KpiResult.kpi_id)
                      .join(KpiFormulaVersion, KpiFormulaVersion.id == KpiResult.formula_version_id)
                      .where(KpiResult.run_id == run.id)).all()
    sensitive = {d.code for d in db.execute(select(KpiDefinition).where(KpiDefinition.sensitive.is_(True))).scalars()}
    rows = [(r, c, v) for r, c, v in rows if cost_ok or c not in sensitive]
    return {"run_id": run.id, "status": run.status, "period_start": run.period_start, "period_end": run.period_end,
            "started_at": run.started_at, "finished_at": run.finished_at, "error": run.error, "results": [
                {"code": c, "formula_version": v, "value": float(r.value) if r.value is not None else None,
                 "status": r.status, "scope_key": r.scope_key, "is_stale": r.is_stale,
                 "quality_score": float(r.quality_score) if r.quality_score is not None else None,
                 "lineage": r.lineage} for r, c, v in rows]}


class PeriodIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


@router.post("/kpi/periods/{period_month}/close", tags=["kpi"])
def close_period(period_month: date, ctx: AccessContext = Depends(require("period.close")),
                 db: Session = Depends(get_db)):
    pm = date(period_month.year, period_month.month, 1)
    row = db.execute(select(PeriodClose).where(PeriodClose.tenant_id == ctx.tenant_id,
                                               PeriodClose.period_month == pm)).scalar()
    if row is None:
        db.add(PeriodClose(tenant_id=ctx.tenant_id, period_month=pm, status="closed", closed_by=ctx.user_id))
    else:
        row.status, row.closed_by = "closed", ctx.user_id
    db.commit()
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="period.close", entity_id=pm.isoformat())
    return {"period_month": pm, "status": "closed"}


@router.post("/kpi/periods/{period_month}/reopen", tags=["kpi"])
def reopen_period(period_month: date, body: PeriodIn, ctx: AccessContext = Depends(require("period.close")),
                  db: Session = Depends(get_db)):
    if not body.reason:
        raise HTTPException(422, "A reason is required to reopen a closed period")
    pm = date(period_month.year, period_month.month, 1)
    row = db.execute(select(PeriodClose).where(PeriodClose.tenant_id == ctx.tenant_id,
                                               PeriodClose.period_month == pm)).scalar()
    if row is None:
        raise HTTPException(404, "Period is not closed")
    row.status, row.reopen_reason = "reopened", body.reason
    db.commit()
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="period.reopen", entity_id=pm.isoformat(),
                details={"reason": body.reason})
    return {"period_month": pm, "status": "reopened"}


# ------------------------------------------------------------------ dashboards


EXEC_KPIS = ["KPI-016", "KPI-017", "KPI-001", "KPI-003", "KPI-007", "KPI-004", "KPI-005", "KPI-008", "KPI-014A",
             "KPI-009", "KPI-006"]


@router.get("/dashboard/executive", tags=["dashboard"])
def dashboard_executive(f: Filters = Depends(), ctx: AccessContext = Depends(require("kpi.read")),
                        db: Session = Depends(get_db)):
    kw = f.kw()
    tiles = [_kpi_call(engine.evaluate, db, ctx, c, f.ps, f.pe, **kw) for c in EXEC_KPIS]
    if ctx.has("cost.read"):
        tiles.append(_kpi_call(engine.evaluate, db, ctx, "KPI-019", f.ps, f.pe, **kw))
    trends = {c: [{"period": s["period_start"][:7], "value": s["value"], "status": s["status"]}
                  for s in _kpi_call(engine.monthly_series, db, ctx, c, f.ps, f.pe, **kw)]
              for c in ("KPI-016", "KPI-001", "KPI-007", "KPI-003")}
    distribution = [{"entity": r["legal_entity"]["name"], "type": r["legal_entity"]["type"], "value": r["value"]}
                    for r in _kpi_call(engine.by_entity, db, ctx, "KPI-016", f.ps, f.pe, project_id=f.project_id)
                    if r["legal_entity"]["type"] != "client"
                    and (not f.entity_ids or r["legal_entity"]["id"] in f.entity_ids)]
    return {"period_start": f.ps, "period_end": f.pe, "filters": kw, "tiles": tiles, "trends": trends,
            "headcount_by_entity": distribution, "demo_data": True}


# ------------------------------------------------------------------ forecasting & anomalies


@router.get("/forecast/headcount", tags=["ai"])
def forecast_headcount(horizon: int = Query(3, ge=1, le=12), history_months: int = Query(24, ge=6, le=60),
                       as_of: date | None = None, f: Filters = Depends(),
                       ctx: AccessContext = Depends(require("forecast.read")), db: Session = Depends(get_db)):
    return _kpi_call(headcount_forecast, db, ctx, as_of=as_of or f.pe, entity_ids=f.entity_ids,
                     project_id=f.project_id, horizon=horizon, history_months=history_months)


@router.get("/analytics/anomalies/{code}", tags=["ai"])
def anomalies(code: str, f: Filters = Depends(), ctx: AccessContext = Depends(require("kpi.read")),
              db: Session = Depends(get_db)):
    return _kpi_call(kpi_anomalies, db, ctx, code, f.ps, f.pe, **f.kw())


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@router.post("/assistant/ask", tags=["ai"])
def ask(body: AskIn, ctx: AccessContext = Depends(require("assistant.use")), db: Session = Depends(get_db)):
    return assistant.handle(db, ctx, body.question)


# ------------------------------------------------------------------ reports


@router.get("/reports", tags=["reports"])
def report_catalog(ctx: AccessContext = Depends(require("report.read"))):
    return rep.catalog(ctx.locale)


@router.get("/reports/{code}", tags=["reports"])
def report(code: str, request: Request, format: str = Query("json", pattern="^(json|xlsx|csv|pdf)$"),
           f: Filters = Depends(), ctx: AccessContext = Depends(require("report.read")), db: Session = Depends(get_db)):
    if format != "json" and not ctx.has("report.export"):
        write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="report.export", entity_type="report",
                    entity_id=code, outcome="denied", details={"format": format})
        raise HTTPException(403, "Missing permission: report.export")
    if code not in rep.REPORTS:
        raise HTTPException(404, "Unknown or not yet available report")
    data = _kpi_call(rep.build, db, ctx, code, f.ps, f.pe, entity_ids=f.entity_ids, project_id=f.project_id)
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="report.export" if format != "json" else "report.view",
                entity_type="report", entity_id=code, details={"format": format, "period": [str(f.ps), str(f.pe)],
                                                                "entity_ids": sorted(f.entity_ids or [])})
    fname = f"{code}_{f.ps}_{f.pe}"
    if format == "xlsx":
        return Response(rep.to_xlsx(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})
    if format == "csv":
        return Response(rep.to_csv(data), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    if format == "pdf":
        return Response(rep.to_pdf(data), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})
    return data


# ------------------------------------------------------------------ imports


def _imp(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except imp.DuplicateFile as e:
        raise HTTPException(409, {"message": str(e), "batch_id": e.batch_id}) from None
    except imp.ImportError_ as e:
        raise HTTPException(e.status_code, str(e)) from None


@router.post("/imports", tags=["imports"], status_code=201)
async def import_upload(file: UploadFile = File(...), entity_type: str = Form("employment"),
                        ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    limit = get_settings().max_upload_mb * 1024 * 1024
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):  # audit finding #18: never buffer more than the limit
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, f"File larger than {get_settings().max_upload_mb} MB")
    content = bytes(buf)
    res = _imp(imp.upload, db, ctx, entity_type=entity_type, filename=file.filename or "upload", content=content)
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="import.upload", entity_type="import_batch",
                entity_id=str(res["batch_id"]), details={"rows": res["total_rows"]})
    return res


class ValidateIn(BaseModel):
    mapping: dict[str, str | None] | None = None


@router.post("/imports/{batch_id}/validate", tags=["imports"])
def import_validate(batch_id: int, body: ValidateIn, ctx: AccessContext = Depends(require("import.write")),
                    db: Session = Depends(get_db)):
    return _imp(imp.validate, db, ctx, batch_id, body.mapping)


@router.post("/imports/{batch_id}/commit", tags=["imports"])
def import_commit(batch_id: int, ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    res = _imp(imp.commit, db, ctx, batch_id)
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="import.commit", entity_type="import_batch",
                entity_id=str(batch_id), details={"committed_rows": res["committed_rows"]})
    return res


@router.get("/imports", tags=["imports"])
def import_list(ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    q = select(ImportBatch).where(ImportBatch.tenant_id == ctx.tenant_id)
    if ctx.entity_filter("import.write") is not None:  # entity-scoped importers only see their own batches
        q = q.where(ImportBatch.created_by == ctx.user_id)
    rows = db.execute(q.order_by(ImportBatch.id.desc()).limit(50)).scalars()
    return [imp.summary(db, ctx, b.id) for b in rows]


@router.get("/imports/{batch_id}", tags=["imports"])
def import_get(batch_id: int, ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    return _imp(imp.summary, db, ctx, batch_id)


@router.get("/imports/{batch_id}/errors.csv", tags=["imports"])
def import_errors(batch_id: int, ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    csv_text = _imp(imp.error_report_csv, db, ctx, batch_id)
    return Response(csv_text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="import_{batch_id}_errors.csv"'})
