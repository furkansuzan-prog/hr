"""Organisation / workforce data management API (owned by Agent 06 - Backend).

Thin HTTP layer: request validation (Pydantic), permission dependency, error mapping and audit trail.
Business rules live in app.services.data_management.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.people import TERMINATION_CATEGORIES
from app.security.access import AccessContext
from app.security.deps import require, write_audit
from app.services import data_management as svc

router = APIRouter(prefix="/api/v1/manage", tags=["data-management"])

ContractType = Literal["permanent", "fixed_term", "project", "seasonal"]
_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _call(request: Request, ctx: AccessContext, fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except svc.Forbidden as e:
        write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action="authz.scope_denied",
                    entity_type=e.entity_type, entity_id=e.entity_id, outcome="denied", ip=_ip(request),
                    details={"path": request.url.path, "permission": e.permission, "reason": str(e.detail)})
        raise HTTPException(403, e.detail) from None
    except svc.DataManagementError as e:
        raise HTTPException(e.status_code, e.detail) from None


def _audit(request: Request, ctx: AccessContext, action: str, entity_type: str, entity_id, details: dict | None = None):
    write_audit(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=action, entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None, details=details, ip=_ip(request))


# ------------------------------------------------------------------ users & roles


class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=_EMAIL_RE)
    full_name: str = Field(min_length=1, max_length=200)
    locale: Literal["tr", "en", "ru"] = "tr"


class RoleGrantIn(BaseModel):
    role_code: str = Field(min_length=1, max_length=40)
    scope_type: Literal["tenant", "legal_entity"]
    legal_entity_id: int | None = None

    @model_validator(mode="after")
    def _scope(self):
        if self.scope_type == "tenant" and self.legal_entity_id is not None:
            raise ValueError("legal_entity_id must be null for scope_type 'tenant'")
        if self.scope_type == "legal_entity" and self.legal_entity_id is None:
            raise ValueError("legal_entity_id is required for scope_type 'legal_entity'")
        return self


@router.get("/users")
def users_list(ctx: AccessContext = Depends(require("admin.users")), db: Session = Depends(get_db)):
    return svc.list_users(db, ctx)


@router.post("/users", status_code=201)
def users_create(body: UserIn, request: Request, ctx: AccessContext = Depends(require("admin.users")),
                 db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.create_user, db, ctx, email=body.email, full_name=body.full_name, locale=body.locale)
    _audit(request, ctx, "admin.user_create", "user", res["id"], {"email": body.email.lower()})
    return res


@router.get("/roles")
def roles_list(ctx: AccessContext = Depends(require("admin.users")), db: Session = Depends(get_db)):
    return svc.list_roles(db, ctx)


@router.post("/users/{user_id}/roles", status_code=201)
def roles_grant(user_id: int, body: RoleGrantIn, request: Request, ctx: AccessContext = Depends(require("admin.users")),
                db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.grant_role, db, ctx, user_id, role_code=body.role_code, scope_type=body.scope_type,
                legal_entity_id=body.legal_entity_id)
    _audit(request, ctx, "admin.role_grant", "role_assignment", res["assignment_id"],
           {"user_id": user_id, "role_code": body.role_code, "scope_type": body.scope_type,
            "legal_entity_id": body.legal_entity_id})
    return res


@router.delete("/role-assignments/{assignment_id}")
def roles_revoke(assignment_id: int, request: Request, ctx: AccessContext = Depends(require("admin.users")),
                 db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.revoke_role, db, ctx, assignment_id)
    _audit(request, ctx, "admin.role_revoke", "role_assignment", assignment_id,
           {"role_code": res["role_code"], "valid_to": str(res["valid_to"])})
    return res


@router.post("/users/{user_id}/deactivate")
def users_deactivate(user_id: int, request: Request, ctx: AccessContext = Depends(require("admin.users")),
                     db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.deactivate_user, db, ctx, user_id)
    _audit(request, ctx, "admin.user_deactivate", "user", user_id)
    return res


# ------------------------------------------------------------------ identity review queue


class ResolveIn(BaseModel):
    action: Literal["merge", "create_new", "reject"]


@router.get("/identity-reviews")
def identity_reviews(status: str = Query("open", pattern="^(open|merged|created|rejected|all)$"),
                     ctx: AccessContext = Depends(require("people.write")), db: Session = Depends(get_db)):
    return svc.list_identity_reviews(db, ctx, status)


@router.post("/identity-reviews/{item_id}/resolve")
def identity_review_resolve(item_id: int, body: ResolveIn, request: Request,
                            ctx: AccessContext = Depends(require("people.write")), db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.resolve_identity_review, db, ctx, item_id, body.action)
    _audit(request, ctx, "identity_review.resolve", "identity_review", item_id,
           {"action": body.action, "status": res["status"], "person_id": res.get("person_id"),
            "employment_id": res.get("employment_id")})
    return res


# ------------------------------------------------------------------ people & employments


class EmploymentIn(BaseModel):
    legal_entity_id: int
    start_date: date
    contract_type: ContractType
    employee_number: str | None = Field(None, max_length=40)
    project_id: int | None = None
    occupation_id: int | None = None


class PersonIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    national_id: str | None = Field(None, max_length=40)
    birth_year: int | None = None
    gender: str | None = Field(None, max_length=10)
    employment: EmploymentIn

    @field_validator("first_name", "last_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("birth_year")
    @classmethod
    def _birth_year(cls, v: int | None) -> int | None:
        if v is not None and not 1930 <= v <= date.today().year - 14:
            raise ValueError("implausible birth year")
        return v


class TerminateIn(BaseModel):
    end_date: date
    termination_category: str
    record_version: int = Field(ge=1)
    termination_reason_code: str | None = Field(None, max_length=40)

    @field_validator("termination_category")
    @classmethod
    def _category(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in TERMINATION_CATEGORIES:
            raise ValueError(f"must be one of {', '.join(TERMINATION_CATEGORIES)}")
        return v


class TransferIn(BaseModel):
    target_legal_entity_id: int
    transfer_date: date
    record_version: int = Field(ge=1)


@router.post("/people", status_code=201)
def people_create(body: PersonIn, request: Request, ctx: AccessContext = Depends(require("people.write")),
                  db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.create_person, db, ctx, first_name=body.first_name, last_name=body.last_name,
                national_id=body.national_id, birth_year=body.birth_year, gender=body.gender,
                employment=body.employment.model_dump())
    # the raw national id is never written to the audit trail
    _audit(request, ctx, "people.create", "person", res["person_id"],
           {"employment_id": res["employment_id"], "legal_entity_id": body.employment.legal_entity_id,
            "national_id_provided": bool(body.national_id)})
    return res


@router.post("/employments/{employment_id}/terminate")
def employment_terminate(employment_id: int, body: TerminateIn, request: Request,
                         ctx: AccessContext = Depends(require("people.write")), db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.terminate_employment, db, ctx, employment_id, end_date=body.end_date,
                termination_category=body.termination_category, record_version=body.record_version,
                termination_reason_code=body.termination_reason_code)
    _audit(request, ctx, "employment.terminate", "employment", employment_id,
           {"end_date": str(body.end_date), "termination_category": body.termination_category,
            "record_version": res["record_version"]})
    return res


@router.post("/employments/{employment_id}/transfer")
def employment_transfer(employment_id: int, body: TransferIn, request: Request,
                        ctx: AccessContext = Depends(require("people.write")), db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.transfer_employment, db, ctx, employment_id,
                target_legal_entity_id=body.target_legal_entity_id, transfer_date=body.transfer_date,
                record_version=body.record_version)
    _audit(request, ctx, "employment.transfer", "employment", employment_id,
           {"target_legal_entity_id": body.target_legal_entity_id, "transfer_date": str(body.transfer_date),
            "new_employment_id": res["new_employment_id"]})
    return res


# ------------------------------------------------------------------ organisation


class LegalEntityIn(BaseModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.\-]+$")
    name: str = Field(min_length=1, max_length=200)
    entity_type: Literal["client", "main_contractor", "subcontractor", "employer"]
    country: str = Field("TR", pattern=r"^[A-Za-z]{2}$")


class RelationshipIn(BaseModel):
    parent_entity_id: int
    child_entity_id: int
    project_id: int | None = None
    tier: int = Field(1, ge=1, le=10)
    valid_from: date
    data_sharing_granted: bool = False
    scope_of_work: str | None = Field(None, max_length=200)


class RelationshipEndIn(BaseModel):
    valid_to: date


@router.post("/legal-entities", status_code=201)
def legal_entity_create(body: LegalEntityIn, request: Request, ctx: AccessContext = Depends(require("org.write")),
                        db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.create_legal_entity, db, ctx, code=body.code, name=body.name,
                entity_type=body.entity_type, country=body.country)
    _audit(request, ctx, "org.legal_entity_create", "legal_entity", res["id"], {"code": body.code})
    return res


@router.post("/contractor-relationships", status_code=201)
def relationship_create(body: RelationshipIn, request: Request, ctx: AccessContext = Depends(require("org.write")),
                        db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.create_contractor_relationship, db, ctx, **body.model_dump())
    _audit(request, ctx, "org.contractor_relationship_create", "contractor_relationship", res["id"],
           {"parent_entity_id": body.parent_entity_id, "child_entity_id": body.child_entity_id,
            "data_sharing_granted": body.data_sharing_granted, "valid_from": str(body.valid_from)})
    return res


@router.post("/contractor-relationships/{relationship_id}/end")
def relationship_end(relationship_id: int, body: RelationshipEndIn, request: Request,
                     ctx: AccessContext = Depends(require("org.write")), db: Session = Depends(get_db)):
    res = _call(request, ctx, svc.end_contractor_relationship, db, ctx, relationship_id, valid_to=body.valid_to)
    _audit(request, ctx, "org.contractor_relationship_end", "contractor_relationship", relationship_id,
           {"valid_to": str(body.valid_to)})
    return res
