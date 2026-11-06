"""Auth, organisation, people, health, audit, integrations, data quality, risk, sustainability."""
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import (
    Assignment,
    AuditLog,
    ContractorRelationship,
    DataQualityIssue,
    DataSource,
    EmploymentRelationship,
    FrameworkRequirement,
    ImportBatch,
    ImprovementAction,
    KpiDefinition,
    LegalEntity,
    Occupation,
    Person,
    Project,
    RiskItem,
    SurveyAggregate,
    SustainabilityFramework,
    User,
)
from app.security.access import AccessContext
from app.security.auth import create_access_token
from app.security.deps import deny_entity, get_context, require, write_audit
from app.security.login import authenticate, revoke_all_tokens

router = APIRouter()


# ------------------------------------------------------------------ health


@router.get("/health/live", tags=["health"])
def live():
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return {"status": "ok", "db": "ok", "migration": rev}


# ------------------------------------------------------------------ auth


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@router.post("/api/v1/auth/login", tags=["auth"])
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    """401 for every credential problem (unknown e-mail, wrong password, inactive, locked); 429 + Retry-After when
    the client IP exceeded the failed-attempt window. See app/security/login.py."""
    ip = request.client.host if request.client else None
    user = authenticate(db, body.email, body.password, ip)
    return {"access_token": create_access_token(user.id, user.tenant_id, user.token_version), "token_type": "bearer"}


@router.post("/api/v1/auth/logout-all", tags=["auth"])
def logout_all(request: Request, ctx: AccessContext = Depends(get_context), db: Session = Depends(get_db)):
    """Revoke every access token of the calling user (all devices), including the one used for this call."""
    revoke_all_tokens(db, ctx.user_id)
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="auth.logout_all",
                ip=request.client.host if request.client else None)
    return {"status": "ok"}


@router.get("/api/v1/auth/me", tags=["auth"])
def me(ctx: AccessContext = Depends(get_context), db: Session = Depends(get_db)):
    user = db.get(User, ctx.user_id)
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name, "locale": user.locale,
        "tenant_id": ctx.tenant_id, "permissions": sorted(ctx.permissions),
        "scopes": {p: (sorted(s) if s is not None else "tenant") for p, s in ctx.grants.items()},
        # ADR-017: entities visible only for some projects, per permission ({entity_id: [project_id, ...]})
        "project_limits": {p: {str(e): sorted(ps) for e, ps in sorted(ctx.limits(p).items())}
                           for p in sorted(ctx.grants) if ctx.limits(p)},
        "mfa_enabled": user.mfa_enabled,
    }


# ------------------------------------------------------------------ organisation


def _visible_entities_q(ctx: AccessContext, perm: str):
    q = select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)
    s = ctx.entity_filter(perm)
    if s is not None:
        q = q.where(LegalEntity.id.in_(s or {-1}))
    return q


@router.get("/api/v1/org/legal-entities", tags=["organisation"])
def legal_entities(ctx: AccessContext = Depends(require("org.read")), db: Session = Depends(get_db)):
    return [{"id": e.id, "code": e.code, "name": e.name, "type": e.entity_type, "country": e.country}
            for e in db.execute(_visible_entities_q(ctx, "org.read").order_by(LegalEntity.code)).scalars()]


@router.get("/api/v1/org/projects", tags=["organisation"])
def projects(ctx: AccessContext = Depends(require("org.read")), db: Session = Depends(get_db)):
    rows = db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id).order_by(Project.code)).scalars()
    return [{"id": p.id, "code": p.code, "name": p.name, "phase": p.phase, "status": p.status,
             "start_date": p.start_date, "end_date": p.end_date} for p in rows]


@router.get("/api/v1/org/occupations", tags=["organisation"])
def occupations(ctx: AccessContext = Depends(require("org.read")), db: Session = Depends(get_db)):
    rows = db.execute(select(Occupation).where(Occupation.tenant_id == ctx.tenant_id).order_by(Occupation.code)).scalars()
    return [{"id": o.id, "code": o.code, "name": o.name, "group": o.occupation_group, "critical": o.is_critical}
            for o in rows]


@router.get("/api/v1/org/contractor-tree", tags=["organisation"])
def contractor_tree(on: date | None = None, ctx: AccessContext = Depends(require("org.read")),
                    db: Session = Depends(get_db)):
    on = on or date.today()
    visible = {e.id: e for e in db.execute(_visible_entities_q(ctx, "org.read")).scalars()}
    rels = db.execute(select(ContractorRelationship).where(
        ContractorRelationship.tenant_id == ctx.tenant_id, ContractorRelationship.valid_from <= on,
        or_(ContractorRelationship.valid_to.is_(None), ContractorRelationship.valid_to > on))).scalars()
    edges = [{"parent_id": r.parent_entity_id, "child_id": r.child_entity_id, "tier": r.tier,
              "project_id": r.project_id, "scope_of_work": r.scope_of_work,
              "data_sharing_granted": r.data_sharing_granted, "valid_from": r.valid_from, "valid_to": r.valid_to}
             for r in rels if r.parent_entity_id in visible and r.child_entity_id in visible]
    return {"as_of": on, "nodes": [{"id": e.id, "code": e.code, "name": e.name, "type": e.entity_type}
                                   for e in visible.values()], "edges": edges}


# ------------------------------------------------------------------ people


def _mask(s: str) -> str:
    return (s[:1] + "***") if s else ""


@router.get("/api/v1/people", tags=["people"])
def people(
    request: Request,
    entity_id: int | None = None,
    status: str = Query("active", pattern="^(active|left|all)$"),
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: AccessContext = Depends(require("people.read")),
    db: Session = Depends(get_db),
):
    if entity_id is not None and not ctx.can_see_entity("people.read", entity_id):
        deny_entity(ctx, request, "people.read", {entity_id})
    today = date.today()
    base = (select(EmploymentRelationship, Person, LegalEntity)
            .join(Person, Person.id == EmploymentRelationship.person_id)
            .join(LegalEntity, LegalEntity.id == EmploymentRelationship.legal_entity_id)
            .where(EmploymentRelationship.tenant_id == ctx.tenant_id))
    # ADR-017: employments of a project-limited entity only when assigned to an allowed project
    clause = ctx.entity_clause("people.read", EmploymentRelationship.legal_entity_id,
                               employment_col=EmploymentRelationship.id)
    if clause is not None:
        base = base.where(clause)
    if entity_id is not None:
        base = base.where(EmploymentRelationship.legal_entity_id == entity_id)
    if status == "active":
        base = base.where(or_(EmploymentRelationship.end_date.is_(None), EmploymentRelationship.end_date >= today))
    elif status == "left":
        base = base.where(EmploymentRelationship.end_date < today)
    pii = ctx.has("people.pii")
    if q and pii:
        like = f"%{q.lower()}%"
        base = base.where(or_(func.lower(Person.last_name).like(like), func.lower(Person.first_name).like(like),
                              EmploymentRelationship.employee_number.ilike(like)))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar()
    rows = db.execute(base.order_by(EmploymentRelationship.id).offset((page - 1) * page_size).limit(page_size)).all()
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="people.list",
                details={"entity_id": entity_id, "status": status, "page": page, "pii": pii})
    return {"total": total, "page": page, "page_size": page_size, "items": [
        {"employment_id": e.id, "person_id": p.id,
         "name": f"{p.first_name} {p.last_name}" if pii else f"{_mask(p.first_name)} {_mask(p.last_name)}",
         "employee_number": e.employee_number if pii else None, "legal_entity": le.name,
         "legal_entity_id": le.id, "contract_type": e.contract_type, "start_date": e.start_date,
         "end_date": e.end_date, "termination_category": e.termination_category} for e, p, le in rows]}


@router.get("/api/v1/people/{person_id}/history", tags=["people"])
def person_history(person_id: int, request: Request, ctx: AccessContext = Depends(require("people.read")),
                   db: Session = Depends(get_db)):
    p = db.get(Person, person_id)
    if p is None or p.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Not found")
    emps = list(db.execute(select(EmploymentRelationship).where(EmploymentRelationship.person_id == person_id)
                           .order_by(EmploymentRelationship.start_date)).scalars())
    visible = [e for e in emps if ctx.employment_visible(db, "people.read", e.legal_entity_id, e.id)]
    if not visible:  # 404 (not 403) so person ids cannot be probed; the attempt is still audited
        write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="authz.scope_denied", entity_type="person",
                    entity_id=str(person_id), outcome="denied")
        raise HTTPException(404, "Not found")
    names = {e.id: e.name for e in db.execute(select(LegalEntity).where(
        LegalEntity.id.in_({x.legal_entity_id for x in visible}))).scalars()}
    out = []
    limits = ctx.limits("people.read")
    for e in visible:
        aq = select(Assignment).where(Assignment.employment_id == e.id)
        if e.legal_entity_id in limits:  # ADR-017: only assignments on the allowed projects
            aq = aq.where(Assignment.project_id.in_(limits[e.legal_entity_id]))
        asg = db.execute(aq).scalars()
        out.append({"employment_id": e.id, "legal_entity": names[e.legal_entity_id], "start_date": e.start_date,
                    "end_date": e.end_date, "termination_category": e.termination_category,
                    "record_version": e.record_version,
                    "assignments": [{"project_id": a.project_id, "occupation_id": a.occupation_id,
                                     "valid_from": a.valid_from, "valid_to": a.valid_to} for a in asg]})
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="people.history", entity_type="person",
                entity_id=str(person_id))
    pii = ctx.has("people.pii")
    return {"person_id": p.id, "name": f"{p.first_name} {p.last_name}" if pii else None,
            "employments": out}


# ------------------------------------------------------------------ data quality & integrations


@router.get("/api/v1/data-quality/issues", tags=["data"])
def dq_issues(limit: int = Query(100, le=500), ctx: AccessContext = Depends(require("import.write")),
              db: Session = Depends(get_db)):
    q = select(DataQualityIssue).where(DataQualityIssue.tenant_id == ctx.tenant_id)
    if ctx.entity_filter("import.write") is not None:
        q = q.where(DataQualityIssue.import_batch_id.in_(
            select(ImportBatch.id).where(ImportBatch.created_by == ctx.user_id)))
    if not ctx.has("hse.read"):  # ADR-021: the HSE case queue is visible only with hse.read
        q = q.where(DataQualityIssue.entity_type != "safety_event")
    rows = db.execute(q.order_by(DataQualityIssue.id.desc()).limit(limit)).scalars()
    return [{"id": i.id, "batch_id": i.import_batch_id, "rule": i.rule_code, "severity": i.severity,
             "message": i.message, "row": i.row_number, "status": i.status} for i in rows]


@router.get("/api/v1/integrations/sources", tags=["data"])
def sources(ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    rows = db.execute(select(DataSource).where(DataSource.tenant_id == ctx.tenant_id)).scalars()
    return [{"id": s.id, "code": s.code, "name": s.name, "kind": s.kind, "status": s.status,
             "last_success_at": s.last_success_at, "last_error": s.last_error} for s in rows]


@router.post("/api/v1/integrations/sources/{source_id}/sync", tags=["data"])
def sync_source(source_id: int, ctx: AccessContext = Depends(require("import.write")), db: Session = Depends(get_db)):
    """Connector framework stub: real connectors implement fetch() against documented, authorised APIs only.
    An unavailable source fails cleanly: no partial data is written, the error is recorded (scenario 20)."""
    s = db.get(DataSource, source_id)
    if s is None or s.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Not found")
    if s.status == "disabled":
        raise HTTPException(409, "Source disabled")
    if s.status == "unavailable":
        s.last_error = f"Source unreachable at {datetime.now(UTC).isoformat(timespec='seconds')}; will retry"
        db.commit()
        write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="integration.sync", entity_type="data_source",
                    entity_id=str(s.id), outcome="error", details={"error": s.last_error})
        raise HTTPException(503, {"code": "source_unavailable", "message": s.last_error, "data_written": 0,
                                  "retry": "exponential backoff (scheduler)"})
    s.last_success_at = datetime.now(UTC)
    s.last_error = None
    db.commit()
    return {"status": "ok", "records_fetched": 0, "note": "mock connector - no external system configured"}


# ------------------------------------------------------------------ risk & actions


@router.get("/api/v1/risks", tags=["risk"])
def risks(ctx: AccessContext = Depends(require("risk.read")), db: Session = Depends(get_db)):
    q = select(RiskItem).where(RiskItem.tenant_id == ctx.tenant_id)
    clause = ctx.entity_clause("risk.read", RiskItem.legal_entity_id, project_col=RiskItem.project_id)
    if clause is not None:
        q = q.where(clause)
    return [{"id": r.id, "title": r.title, "category": r.category, "severity": r.severity, "status": r.status,
             "target_date": r.target_date, "source_kpi": r.source_kpi_code, "legal_entity_id": r.legal_entity_id}
            for r in db.execute(q.order_by(RiskItem.target_date)).scalars()]


@router.get("/api/v1/actions", tags=["risk"])
def actions(ctx: AccessContext = Depends(require("risk.read")), db: Session = Depends(get_db)):
    q = select(ImprovementAction).where(ImprovementAction.tenant_id == ctx.tenant_id)
    clause = ctx.entity_clause("risk.read", ImprovementAction.legal_entity_id)  # no project: limited entities hidden
    if clause is not None:
        q = q.where(clause)
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    return [{"id": a.id, "problem": a.problem, "kpi_code": a.kpi_code, "baseline": f(a.baseline_value),
             "target": f(a.target_value), "result": f(a.result_value), "priority": a.priority, "status": a.status,
             "due_date": a.due_date, "effectiveness": a.effectiveness, "steps_taken": a.steps_taken}
            for a in db.execute(q.order_by(ImprovementAction.due_date)).scalars()]


# ------------------------------------------------------------------ sustainability


@router.get("/api/v1/sustainability/frameworks", tags=["sustainability"])
def frameworks(ctx: AccessContext = Depends(require("sustainability.read")), db: Session = Depends(get_db)):
    kpis = {k.code: k for k in db.execute(select(KpiDefinition)).scalars()}
    out = []
    for fw in db.execute(select(SustainabilityFramework).order_by(SustainabilityFramework.code)).scalars():
        reqs = list(db.execute(select(FrameworkRequirement).where(FrameworkRequirement.framework_id == fw.id)).scalars())
        items = []
        for r in reqs:
            states = [{"code": c, "status": kpis[c].status if c in kpis else "missing"} for c in r.kpi_codes]
            covered = any(s["status"] == "active" for s in states)
            items.append({"ref": r.title, "kpis": states, "data_available": covered})
        cov = sum(i["data_available"] for i in items)
        out.append({"code": fw.code, "name": fw.name, "version": fw.version, "obligation": fw.obligation,
                    "version_verified": fw.version_verified, "note": fw.applicability_note,
                    "coverage": {"requirements": len(items), "with_active_kpi": cov}, "requirements": items,
                    "disclaimer": "Data coverage indicator only - not a compliance or certification claim"})
    return out


@router.get("/api/v1/sustainability/survey", tags=["sustainability"])
def survey(ctx: AccessContext = Depends(require("sustainability.read")), db: Session = Depends(get_db)):
    q = select(SurveyAggregate.dimension, func.sum(SurveyAggregate.respondents),
               func.sum(SurveyAggregate.mean_score * SurveyAggregate.respondents) / func.sum(SurveyAggregate.respondents)
               ).where(SurveyAggregate.tenant_id == ctx.tenant_id).group_by(SurveyAggregate.dimension)
    # survey aggregates have no project: project-limited entities are excluded (ADR-017)
    clause = ctx.entity_clause("sustainability.read", SurveyAggregate.legal_entity_id)
    if clause is not None:
        q = q.where(clause)
    return [{"dimension": d, "respondents": int(n), "mean_score": round(float(m), 2)} for d, n, m in db.execute(q)
            if n and n >= 10]


# ------------------------------------------------------------------ audit


@router.get("/api/v1/audit", tags=["audit"])
def audit(limit: int = Query(100, le=1000), action: str | None = None,
          ctx: AccessContext = Depends(require("audit.read")), db: Session = Depends(get_db)):
    q = select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id)
    if action:
        q = q.where(AuditLog.action == action)
    return [{"id": a.id, "at": a.at, "user_id": a.user_id, "action": a.action, "entity_type": a.entity_type,
             "entity_id": a.entity_id, "outcome": a.outcome, "details": a.details}
            for a in db.execute(q.order_by(AuditLog.id.desc()).limit(limit)).scalars()]
