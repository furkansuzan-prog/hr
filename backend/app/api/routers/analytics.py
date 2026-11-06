"""KPI engine, dashboards, forecasting, assistant, reports, imports, data-feed coverage."""
import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import assistant
from app.ai.service import headcount_forecast, kpi_anomalies
from app.analytics import dashboards, engine
from app.api.common import Filters, default_period
from app.core.config import get_settings
from app.core.db import get_db
from app.models import (
    COVERAGE_DECLARE_ACTION,
    ImportBatch,
    KpiCalculationRun,
    KpiDefinition,
    KpiFormulaVersion,
    KpiResult,
    PeriodClose,
)
from app.security.access import AccessContext
from app.security.deps import deny_kpi_permission, require, write_audit
from app.security.features import is_health_kpi, kpi_hidden, report_hidden
from app.services import coverage as cov
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
    # D-9: KPIs of a disabled feature are not listed; D-12: health-derived KPIs only with health.read
    return [_def_out(d, ctx.locale) for d in db.execute(q).scalars()
            if not kpi_hidden(d) and (ctx.has("health.read") or not is_health_kpi(d))]


@router.get("/kpi/definitions/{code}", tags=["kpi"])
def kpi_definition(code: str, request: Request, ctx: AccessContext = Depends(require("kpi.read")),
                   db: Session = Depends(get_db)):
    d = _kpi_call(engine.get_visible_definition, db, code)
    if is_health_kpi(d) and not ctx.has("health.read"):  # D-12, audited
        deny_kpi_permission(ctx, request, d.code, ["health.read"])
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
    # D-12: health-derived KPIs follow the same rule with health.read; D-9: KPIs of a disabled feature are left out
    health_ok = ctx.has("health.read") and (ctx.entity_filter("health.read") is None or run.triggered_by == ctx.user_id)
    defs = {d.code: d for d in db.execute(select(KpiDefinition)).scalars()}
    rows = [(r, c, v) for r, c, v in rows if (cost_ok or not defs[c].sensitive)
            and (health_ok or not is_health_kpi(defs[c])) and not kpi_hidden(defs[c])]
    return {"run_id": run.id, "status": run.status, "period_start": run.period_start, "period_end": run.period_end,
            "started_at": run.started_at, "finished_at": run.finished_at, "error": run.error, "results": [
                {"code": c, "formula_version": v, "value": float(r.value) if r.value is not None else None,
                 "status": r.status, "scope_key": r.scope_key, "is_stale": r.is_stale,
                 "quality_score": float(r.quality_score) if r.quality_score is not None else None,
                 "lineage": r.lineage} for r, c, v in rows]}


class PeriodIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


def _require_main_contractor(db: Session, ctx: AccessContext, action: str) -> None:
    if not engine.can_close_periods(db, ctx):
        write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="authz.scope_denied", entity_type="period",
                    outcome="denied", details={"action": action, "reason": "main contractor authority required"})
        raise HTTPException(403, "Closing or reopening periods is reserved for the main contractor")


@router.post("/kpi/periods/{period_month}/close", tags=["kpi"])
def close_period(period_month: date, ctx: AccessContext = Depends(require("period.close")),
                 db: Session = Depends(get_db)):
    from sqlalchemy.exc import IntegrityError

    _require_main_contractor(db, ctx, "period.close")
    pm = date(period_month.year, period_month.month, 1)
    row = db.execute(select(PeriodClose).where(PeriodClose.tenant_id == ctx.tenant_id,
                                               PeriodClose.period_month == pm)).scalar()
    if row is not None and row.status == "closed":
        raise HTTPException(409, "Period is already closed")
    # B-18: freeze results BEFORE closing so a closed month never falls back to live recalculation
    frozen = _kpi_call(engine.freeze_period, db, ctx, pm)
    db.expire_all()
    row = db.execute(select(PeriodClose).where(PeriodClose.tenant_id == ctx.tenant_id,
                                               PeriodClose.period_month == pm).with_for_update()).scalar()
    if row is not None and row.status == "closed":  # closed concurrently while we were freezing
        db.rollback()
        raise HTTPException(409, "Period is already closed")
    if row is None:
        db.add(PeriodClose(tenant_id=ctx.tenant_id, period_month=pm, status="closed", closed_by=ctx.user_id))
    else:
        row.status, row.closed_by = "closed", ctx.user_id
    # served values switch to frozen results: invalidate KPI + dashboard caches (and re-warm after commit)
    engine.bump_data_version(db, ctx.tenant_id)
    db.info.setdefault("warm_tenants", set()).add(ctx.tenant_id)
    try:
        db.commit()
    except IntegrityError:  # concurrent first close of the same month (unique tenant_id, period_month)
        db.rollback()
        raise HTTPException(409, "Period is already closed") from None
    # WP-6 3.5 step 5 / HR Director S-3: feeds that are not 'complete' are listed, closing is not blocked
    gaps = cov.period_gaps(db, ctx, pm, engine.required_feeds_of_active_kpis(db))
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="period.close", entity_id=pm.isoformat(),
                details={"frozen_results": frozen, "coverage_gaps": gaps})
    return {"period_month": pm, "status": "closed", "frozen_results": frozen, "coverage_gaps": gaps}


@router.post("/kpi/periods/{period_month}/reopen", tags=["kpi"])
def reopen_period(period_month: date, body: PeriodIn, ctx: AccessContext = Depends(require("period.close")),
                  db: Session = Depends(get_db)):
    _require_main_contractor(db, ctx, "period.reopen")
    if not body.reason:
        raise HTTPException(422, "A reason is required to reopen a closed period")
    pm = date(period_month.year, period_month.month, 1)
    row = db.execute(select(PeriodClose).where(PeriodClose.tenant_id == ctx.tenant_id,
                                               PeriodClose.period_month == pm)).scalar()
    if row is None:
        raise HTTPException(404, "Period is not closed")
    row.status, row.reopen_reason = "reopened", body.reason
    engine.bump_data_version(db, ctx.tenant_id)  # values are live again
    db.info.setdefault("warm_tenants", set()).add(ctx.tenant_id)
    db.commit()
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="period.reopen", entity_id=pm.isoformat(),
                details={"reason": body.reason})
    return {"period_month": pm, "status": "reopened"}


# ------------------------------------------------------------------ dashboards


@router.get("/dashboard/executive", tags=["dashboard"])
def dashboard_executive(f: Filters = Depends(), ctx: AccessContext = Depends(require("kpi.read")),
                        db: Session = Depends(get_db)):
    return _kpi_call(dashboards.executive, db, ctx, f.ps, f.pe, **f.kw())


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
    if code not in rep.REPORTS or report_hidden(code):  # D-9: subcontractor report hidden with the feature
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
                        coverage: str | None = Form(None, description="Optional JSON coverage declaration (B-20): "
                                                    "period_start, period_end, legal_entity_codes, project_codes, "
                                                    "completeness, coverage_ratio, note"),
                        mode: str | None = Form(None, description="timesheet / training / cost (ADR-022, ADR-027): "
                                                "'skip' (default) keeps stored rows; 'replace' corrects stored rows of "
                                                "open periods in place (audited)"),
                        approved_source: bool | None = Form(None, description="timesheet only (ADR-022): the file "
                                                            "contains approved rows only (needed when no status "
                                                            "column is mapped; otherwise rows are stored as draft)"),
                        all_completed: bool | None = Form(None, description="training only (ADR-027): every row of the "
                                                          "file is a completed training (needed when no status column "
                                                          "is mapped; no default status is assigned)"),
                        ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    limit = get_settings().max_upload_mb * 1024 * 1024
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):  # audit finding #18: never buffer more than the limit
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, f"File larger than {get_settings().max_upload_mb} MB")
    content = bytes(buf)
    decl = None
    if coverage:
        try:
            decl = json.loads(coverage)
        except ValueError:
            raise HTTPException(422, "coverage must be a JSON object") from None
    res = _imp(imp.upload, db, ctx, entity_type=entity_type, filename=file.filename or "upload", content=content,
               coverage=decl, options={"mode": mode, "approved_source": approved_source,
                                       "all_completed": all_completed})
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
    if not ctx.has("hse.read"):  # ADR-021: HSE case batches are visible only with hse.read
        q = q.where(ImportBatch.entity_type != "safety_event")
    if not ctx.has("cost.write"):  # ADR-027: payroll cost batches are visible only with cost.write
        q = q.where(ImportBatch.entity_type != "cost")
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


# ------------------------------------------------------------------ data-feed coverage (B-20, ADR-020)


class CoverageIn(BaseModel):
    feed_type: str
    legal_entity_id: int
    project_id: int | None = None
    period_start: date
    period_end: date
    completeness: str
    coverage_ratio: float | None = None
    confirmed_empty: bool = False
    attributes: dict | None = None
    planned_on_leave_days: str | None = None
    note: str | None = Field(None, max_length=1000)


@router.get("/coverage", tags=["imports"])
def coverage_matrix(period_start: date | None = None, period_end: date | None = None,
                    feed_types: str | None = Query(None, description="Comma-separated feed types"),
                    ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    dps, dpe = default_period()
    ps, pe = period_start or dps, period_end or dpe
    if pe < ps:
        raise HTTPException(422, "period_end must be >= period_start")
    feeds = [f.strip() for f in feed_types.split(",") if f.strip()] if feed_types else None
    try:
        return cov.matrix(db, ctx, ps, pe, feeds)
    except cov.CoverageError as e:
        raise HTTPException(e.status_code, str(e)) from None


@router.post("/coverage", tags=["imports"], status_code=201)
def coverage_declare(body: CoverageIn, ctx: AccessContext = Depends(require("import.write")),
                     db: Session = Depends(get_db)):
    """Manual declaration (WP-6 3.5 step 4): whole-employer import.write; 'none' / confirmed_empty need a note;
    the safety feed also needs hse.read. Audited (coverage.declare) and marks results stale."""
    raw = body.model_dump()
    raw["period_start"], raw["period_end"] = body.period_start.isoformat(), body.period_end.isoformat()
    try:
        decl = cov.normalize(db, ctx, body.feed_type, raw, manual=True)
    except cov.CoverageDenied as e:
        write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=COVERAGE_DECLARE_ACTION,
                    entity_type="data_feed_coverage", entity_id=body.feed_type, outcome="denied",
                    details={"legal_entity_id": body.legal_entity_id, "reason": str(e)})
        raise HTTPException(403, str(e)) from None
    except cov.CoverageError as e:
        raise HTTPException(e.status_code, str(e)) from None
    n = cov.declare(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, decl=decl, basis="manual_declaration")
    db.commit()
    return {"declared_rows": n, "declaration": decl}
