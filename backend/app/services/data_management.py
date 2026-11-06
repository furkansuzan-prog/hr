"""Organisation / workforce data management business rules (Agent 06 - Backend).

Invariants enforced here (INTEGRATION_CONTRACTS.md sections 4 and 5):
- every query is tenant-filtered; entity-level writes check the caller's scope via AccessContext;
- history is never overwritten: role grants are revoked by closing ``valid_to``, employments are ended and a new
  row is created on transfer, contractor relationships are closed instead of deleted;
- every write that changes analytical inputs calls ``mark_stale_from`` in the same transaction;
- raw national identity numbers are never persisted or echoed: only ``national_id_hash`` (HMAC) is stored and it is
  never returned in any payload;
- role grants are protected against privilege escalation: a granter may only grant permissions it holds itself,
  within the scope of its own ``admin.users`` grant.

Services raise ``DataManagementError`` subclasses; the router maps them to HTTP status codes and writes the audit
trail (allowed and denied) via ``write_audit``.
"""
from datetime import date, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.analytics.engine import mark_stale_from
from app.models import (
    Assignment,
    ContractorRelationship,
    EmploymentRelationship,
    IdentityReviewItem,
    ImportBatch,
    ImportStagingRow,
    LegalEntity,
    Occupation,
    Person,
    PersonIdentifier,
    Project,
    Role,
    RolePermission,
    User,
    UserRoleAssignment,
)
from app.security.access import AccessContext
from app.security.auth import hash_password, national_id_hash, random_password
from app.services import exit_codes

CONTRACT_TYPES = ("permanent", "fixed_term", "project", "seasonal")
ENTITY_TYPES = ("client", "main_contractor", "subcontractor", "employer")
REVIEW_STATUSES = ("open", "merged", "created", "rejected")
TRANSFER_REASON_CODE = "internal_transfer"


# ------------------------------------------------------------------ errors


class DataManagementError(Exception):
    status_code = 400

    def __init__(self, detail):
        super().__init__(str(detail))
        self.detail = detail


class NotFound(DataManagementError):
    status_code = 404


class Conflict(DataManagementError):
    status_code = 409


class Invalid(DataManagementError):
    status_code = 422


class Forbidden(DataManagementError):
    """Scope / privilege denial. The router audits it with outcome='denied' before returning 403."""

    status_code = 403

    def __init__(self, detail: str, *, permission: str, entity_type: str = "legal_entity", entity_id: str | None = None):
        super().__init__(detail)
        self.permission = permission
        self.entity_type = entity_type
        self.entity_id = entity_id


# ------------------------------------------------------------------ helpers


def _covers(ctx: AccessContext, perm: str, entity_id: int | None) -> bool:
    """Does the caller hold `perm` for the target scope? entity_id None = tenant-wide scope required."""
    if not ctx.has(perm):
        return False
    if entity_id is None:
        return ctx.entity_filter(perm) is None
    # ADR-017: an entity visible only for some projects is never covered as a whole (no entity-wide admin/writes)
    return ctx.covers_entity(perm, entity_id)


def _require_entity_scope(ctx: AccessContext, perm: str, entity_id: int) -> None:
    if not ctx.covers_entity(perm, entity_id):  # writes need whole-entity access (ADR-017)
        raise Forbidden("Requested legal entity is outside your access scope", permission=perm,
                        entity_id=str(entity_id))


def _entity(db: Session, ctx: AccessContext, entity_id: int, *, status: type[DataManagementError] = NotFound) -> LegalEntity:
    le = db.get(LegalEntity, entity_id)
    if le is None or le.tenant_id != ctx.tenant_id:
        raise status(f"Legal entity {entity_id} not found")
    return le


def _active_on(model, on: date):
    return and_(or_(model.valid_from.is_(None), model.valid_from <= on), or_(model.valid_to.is_(None), model.valid_to > on))


def employment_json(e: EmploymentRelationship) -> dict:
    return {"id": e.id, "person_id": e.person_id, "legal_entity_id": e.legal_entity_id,
            "employee_number": e.employee_number, "contract_type": e.contract_type, "start_date": e.start_date,
            "end_date": e.end_date, "termination_category": e.termination_category,
            "termination_reason_code": e.termination_reason_code, "record_version": e.record_version}


def _assignment_json(a: UserRoleAssignment, role_code: str) -> dict:
    today = date.today()
    active = (a.valid_from is None or a.valid_from <= today) and (a.valid_to is None or a.valid_to > today)
    return {"assignment_id": a.id, "role_code": role_code, "scope_type": a.scope_type,
            "legal_entity_id": a.legal_entity_id, "valid_from": a.valid_from, "valid_to": a.valid_to, "active": active}


def _sanitize_payload(payload: dict | None) -> dict:
    """KVKK: no identity number (raw or hashed) ever leaves the server."""
    return {k: v for k, v in (payload or {}).items() if "national_id" not in k.lower()}


# ------------------------------------------------------------------ users & roles


def list_users(db: Session, ctx: AccessContext) -> list[dict]:
    users = list(db.execute(select(User).where(User.tenant_id == ctx.tenant_id).order_by(User.email)).scalars())
    by_user: dict[int, list[dict]] = {u.id: [] for u in users}
    if users:
        rows = db.execute(
            select(UserRoleAssignment, Role.code).join(Role, Role.id == UserRoleAssignment.role_id)
            .where(UserRoleAssignment.user_id.in_(by_user)).order_by(UserRoleAssignment.id)
        ).all()
        for a, code in rows:
            by_user[a.user_id].append(_assignment_json(a, code))
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "locale": u.locale, "is_active": u.is_active,
             "roles": by_user[u.id]} for u in users]


def create_user(db: Session, ctx: AccessContext, *, email: str, full_name: str, locale: str) -> dict:
    email = email.strip().lower()
    if db.execute(select(User.id).where(func.lower(User.email) == email)).scalar() is not None:
        raise Conflict("A user with this email already exists")
    pw = random_password()
    u = User(tenant_id=ctx.tenant_id, email=email, full_name=full_name.strip(), locale=locale,
             password_hash=hash_password(pw), is_active=True)
    db.add(u)
    db.commit()
    return {"id": u.id, "initial_password": pw}


def deactivate_user(db: Session, ctx: AccessContext, user_id: int) -> dict:
    """Users are never deleted (audit trail integrity). Deactivation blocks login and revokes existing tokens."""
    u = db.get(User, user_id)
    if u is None or u.tenant_id != ctx.tenant_id:
        raise NotFound("User not found")
    if u.id == ctx.user_id:
        raise Conflict("You cannot deactivate yourself")
    today = date.today()
    for a in db.execute(select(UserRoleAssignment).where(UserRoleAssignment.user_id == u.id,
                                                         _active_on(UserRoleAssignment, today))).scalars():
        # an entity-scoped admin may only deactivate users whose every active grant lies inside its own scope
        if not _covers(ctx, "admin.users", None if a.scope_type == "tenant" else a.legal_entity_id):
            raise Forbidden("User holds roles outside your administration scope", permission="admin.users",
                            entity_type="user", entity_id=str(u.id))
    if not u.is_active:
        raise Conflict("User is already inactive")
    u.is_active = False
    u.token_version = (u.token_version or 0) + 1
    db.commit()
    return {"id": u.id, "is_active": False}


def _roles_query(ctx: AccessContext):
    return select(Role).where(or_(Role.tenant_id == ctx.tenant_id, Role.tenant_id.is_(None)))


def _role_permissions(db: Session, role_id: int) -> set[str]:
    return set(db.execute(select(RolePermission.permission_code).where(RolePermission.role_id == role_id)).scalars())


def list_roles(db: Session, ctx: AccessContext) -> list[dict]:
    roles = list(db.execute(_roles_query(ctx).order_by(Role.code)).scalars())
    return [{"id": r.id, "code": r.code, "name": r.name, "is_system": r.is_system,
             "permissions": sorted(_role_permissions(db, r.id))} for r in roles]


def grant_role(db: Session, ctx: AccessContext, user_id: int, *, role_code: str, scope_type: str,
               legal_entity_id: int | None) -> dict:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != ctx.tenant_id:
        raise NotFound("User not found")
    # tenant-specific role wins over a global template with the same code
    role = db.execute(_roles_query(ctx).where(Role.code == role_code)
                      .order_by(Role.tenant_id.is_(None))).scalars().first()
    if role is None:
        raise NotFound(f"Role '{role_code}' not found")
    if scope_type == "tenant":
        if legal_entity_id is not None:
            raise Invalid("legal_entity_id must be null for scope_type 'tenant'")
        target: int | None = None
    else:
        if legal_entity_id is None:
            raise Invalid("legal_entity_id is required for scope_type 'legal_entity'")
        target = _entity(db, ctx, legal_entity_id, status=Invalid).id
    # privilege-escalation guard 1: only within the granter's own admin.users scope
    if not _covers(ctx, "admin.users", target):
        raise Forbidden("You can only grant roles within your own administration scope", permission="admin.users",
                        entity_type="role_grant", entity_id=str(target) if target else "tenant")
    # privilege-escalation guard 2: every permission of the role must be held by the granter for that scope
    missing = sorted(p for p in _role_permissions(db, role.id) if not _covers(ctx, p, target))
    if missing:
        raise Forbidden(f"You cannot grant permissions you do not hold for this scope: {', '.join(missing)}",
                        permission="admin.users", entity_type="role_grant", entity_id=role.code)
    today = date.today()
    dup = db.execute(select(UserRoleAssignment.id).where(
        UserRoleAssignment.user_id == user.id, UserRoleAssignment.role_id == role.id,
        UserRoleAssignment.scope_type == scope_type,
        (UserRoleAssignment.legal_entity_id.is_(None) if target is None else UserRoleAssignment.legal_entity_id == target),
        _active_on(UserRoleAssignment, today))).scalar()
    if dup is not None:
        raise Conflict(f"An active identical role assignment already exists ({dup})")
    a = UserRoleAssignment(user_id=user.id, role_id=role.id, scope_type=scope_type, legal_entity_id=target,
                           valid_from=today, valid_to=None)
    db.add(a)
    db.commit()
    return _assignment_json(a, role.code)


def revoke_role(db: Session, ctx: AccessContext, assignment_id: int) -> dict:
    a = db.get(UserRoleAssignment, assignment_id)
    user = db.get(User, a.user_id) if a is not None else None
    if a is None or user is None or user.tenant_id != ctx.tenant_id:
        raise NotFound("Role assignment not found")
    role = db.get(Role, a.role_id)
    target = None if a.scope_type == "tenant" else a.legal_entity_id
    if not _covers(ctx, "admin.users", target):
        raise Forbidden("You can only revoke roles within your own administration scope", permission="admin.users",
                        entity_type="role_assignment", entity_id=str(a.id))
    today = date.today()
    if a.valid_to is not None and a.valid_to <= today:
        raise Conflict("Role assignment is already revoked or expired")
    if a.user_id == ctx.user_id and "admin.users" in _role_permissions(db, a.role_id):
        others = db.execute(
            select(func.count()).select_from(UserRoleAssignment)
            .join(RolePermission, RolePermission.role_id == UserRoleAssignment.role_id)
            .where(UserRoleAssignment.user_id == ctx.user_id, UserRoleAssignment.id != a.id,
                   RolePermission.permission_code == "admin.users", _active_on(UserRoleAssignment, today))
        ).scalar()
        if not others:
            raise Conflict("You cannot revoke your own last admin.users grant")
    # never delete: close the validity interval (half-open [valid_from, valid_to))
    a.valid_to = max(today, a.valid_from) if a.valid_from else today
    db.commit()
    return _assignment_json(a, role.code)


# ------------------------------------------------------------------ identity review queue


def _reviews_query(ctx: AccessContext):
    q = select(IdentityReviewItem).where(IdentityReviewItem.tenant_id == ctx.tenant_id)
    if ctx.entity_filter("people.write") is not None:
        # entity-scoped users only see items of import batches they created themselves
        q = q.join(ImportBatch, ImportBatch.id == IdentityReviewItem.import_batch_id).where(
            ImportBatch.created_by == ctx.user_id)
    return q


def _review_json(i: IdentityReviewItem) -> dict:
    return {"id": i.id, "reason": i.reason, "payload": _sanitize_payload(i.payload),
            "candidate_person_id": i.candidate_person_id, "import_batch_id": i.import_batch_id, "status": i.status,
            "created_at": i.created_at}


def list_identity_reviews(db: Session, ctx: AccessContext, status: str = "open") -> list[dict]:
    q = _reviews_query(ctx)
    if status != "all":
        q = q.where(IdentityReviewItem.status == status)
    return [_review_json(i) for i in db.execute(q.order_by(IdentityReviewItem.id)).scalars()]


def _staged_nid_hash(db: Session, item: IdentityReviewItem) -> str | None:
    """The review payload deliberately omits the hash; recover it from the staged (already pseudonymised) row."""
    if item.import_batch_id is None:
        return None
    p = item.payload or {}
    keys = ("external_id", "source_system", "legal_entity_code", "start_date")
    for (m,) in db.execute(select(ImportStagingRow.mapped).where(ImportStagingRow.batch_id == item.import_batch_id,
                                                                 ImportStagingRow.committed.is_(False))):
        if m and all((m.get(k) or None) == (p.get(k) or None) for k in keys):
            return m.get("national_id_hash")
    return None


def _parse_iso(v, field: str) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise Invalid(f"Review payload has an invalid {field}") from None


def _create_employment(db: Session, ctx: AccessContext, *, person_id: int, legal_entity_id: int, start_date: date,
                       end_date: date | None = None, termination_category: str | None = None,
                       contract_type: str = "permanent", employee_number: str | None = None,
                       project_id: int | None = None, occupation_id: int | None = None,
                       source_system: str = "manual", source_record_id: str | None = None) -> EmploymentRelationship:
    emp = EmploymentRelationship(tenant_id=ctx.tenant_id, person_id=person_id, legal_entity_id=legal_entity_id,
                                 employee_number=employee_number, contract_type=contract_type, start_date=start_date,
                                 end_date=end_date, termination_category=termination_category if end_date else None,
                                 source_system=source_system, source_record_id=source_record_id,
                                 updated_by=ctx.user_id)
    db.add(emp)
    db.flush()
    if project_id or occupation_id:
        db.add(Assignment(tenant_id=ctx.tenant_id, employment_id=emp.id, project_id=project_id,
                          occupation_id=occupation_id, valid_from=start_date,
                          valid_to=end_date + timedelta(days=1) if end_date else None, is_primary=True))
    return emp


def resolve_identity_review(db: Session, ctx: AccessContext, item_id: int, action: str) -> dict:
    item = db.execute(_reviews_query(ctx).where(IdentityReviewItem.id == item_id).with_for_update(
        of=IdentityReviewItem)).scalar()
    if item is None:
        raise NotFound("Identity review item not found")
    if item.status != "open":
        raise Conflict(f"Identity review item is already resolved ({item.status})")
    out: dict = {"id": item.id}
    if action == "reject":
        item.status = "rejected"
        db.commit()
        return {**out, "status": item.status}

    p = item.payload or {}
    le = db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id,
                                              LegalEntity.code == p.get("legal_entity_code"))).scalar()
    if le is None:
        raise Invalid("Legal entity of the review payload no longer exists")
    _require_entity_scope(ctx, "people.write", le.id)
    start = _parse_iso(p.get("start_date"), "start_date")
    if start is None or not p.get("external_id"):
        raise Invalid("Review payload is missing start_date or external_id")
    end = _parse_iso(p.get("end_date"), "end_date")
    src = p.get("source_system") or "import"
    ext = str(p["external_id"])
    ident = db.execute(select(PersonIdentifier).where(PersonIdentifier.tenant_id == ctx.tenant_id,
                                                      PersonIdentifier.source_system == src,
                                                      PersonIdentifier.external_id == ext)).scalar()
    if action == "merge":
        if item.candidate_person_id is None:
            raise Invalid("This item has no candidate person; use create_new or reject")
        person = db.get(Person, item.candidate_person_id)
        if person is None or person.tenant_id != ctx.tenant_id:
            raise Conflict("Candidate person no longer exists")
        if ident is not None and ident.person_id != person.id:
            raise Conflict("The source external id is already linked to a different person")
        new_status = "merged"
    else:  # create_new
        if ident is not None:
            raise Conflict("The source external id is already linked to an existing person; use merge or reject")
        nid = _staged_nid_hash(db, item)
        if nid and db.execute(select(Person.id).where(Person.tenant_id == ctx.tenant_id,
                                                      Person.national_id_hash == nid)).first():
            # the reviewer decided these are different people: do not store a hash that collides with another
            # person (it would route every future import of either person back into review)
            nid = None
            out["note"] = "national id hash not stored because it collides with an existing person"
        person = Person(tenant_id=ctx.tenant_id, person_code=f"R{item.id}-{src}-{ext}"[:40],
                        first_name=str(p.get("first_name") or "")[:100], last_name=str(p.get("last_name") or "")[:100],
                        national_id_hash=nid, birth_year=p.get("birth_year") or None,
                        gender=(str(p["gender"])[:10] if p.get("gender") else None), source_system=src,
                        source_record_id=ext, updated_by=ctx.user_id)
        db.add(person)
        db.flush()
        new_status = "created"
    if ident is None:
        db.add(PersonIdentifier(tenant_id=ctx.tenant_id, person_id=person.id, source_system=src, external_id=ext))
    emp = db.execute(select(EmploymentRelationship).where(
        EmploymentRelationship.person_id == person.id, EmploymentRelationship.legal_entity_id == le.id,
        EmploymentRelationship.start_date == start)).scalar()
    if emp is None:
        proj = db.execute(select(Project.id).where(Project.tenant_id == ctx.tenant_id,
                                                   Project.code == p.get("project_code"))).scalar() \
            if p.get("project_code") else None
        occ = db.execute(select(Occupation.id).where(Occupation.tenant_id == ctx.tenant_id,
                                                     Occupation.code == p.get("occupation_code"))).scalar() \
            if p.get("occupation_code") else None
        emp = _create_employment(db, ctx, person_id=person.id, legal_entity_id=le.id, start_date=start, end_date=end,
                                 termination_category=(p.get("termination_category") or "unknown") if end else None,
                                 contract_type=p.get("contract_type") or "permanent",
                                 employee_number=p.get("employee_number") or None, project_id=proj,
                                 occupation_id=occ, source_system=src, source_record_id=ext)
        mark_stale_from(db, ctx.tenant_id, start)
    item.status = new_status
    db.commit()
    return {**out, "status": item.status, "person_id": person.id, "employment_id": emp.id}


# ------------------------------------------------------------------ people & employments


def create_person(db: Session, ctx: AccessContext, *, first_name: str, last_name: str, national_id: str | None,
                  birth_year: int | None, gender: str | None, employment: dict) -> dict:
    le = _entity(db, ctx, employment["legal_entity_id"])
    _require_entity_scope(ctx, "people.write", le.id)
    project_id, occupation_id = employment.get("project_id"), employment.get("occupation_id")
    if project_id is not None:
        pr = db.get(Project, project_id)
        if pr is None or pr.tenant_id != ctx.tenant_id:
            raise Invalid(f"Project {project_id} not found")
    if occupation_id is not None:
        oc = db.get(Occupation, occupation_id)
        if oc is None or oc.tenant_id != ctx.tenant_id:
            raise Invalid(f"Occupation {occupation_id} not found")
    nid = national_id_hash(national_id) if national_id and national_id.strip() else None
    if nid:
        cand = db.execute(select(Person.id).where(Person.tenant_id == ctx.tenant_id,
                                                  Person.national_id_hash == nid).order_by(Person.id)).scalar()
        if cand is not None:
            raise Conflict({"message": "A person with this national id already exists", "candidate_person_id": cand})
    person = Person(tenant_id=ctx.tenant_id, person_code="TMP", first_name=first_name.strip(),
                    last_name=last_name.strip(), national_id_hash=nid, birth_year=birth_year,
                    gender=gender.strip().upper() if gender else None, source_system="manual", updated_by=ctx.user_id)
    db.add(person)
    db.flush()
    person.person_code = f"M{person.id:07d}"
    emp = _create_employment(db, ctx, person_id=person.id, legal_entity_id=le.id,
                             start_date=employment["start_date"], contract_type=employment["contract_type"],
                             employee_number=employment.get("employee_number") or None, project_id=project_id,
                             occupation_id=occupation_id)
    mark_stale_from(db, ctx.tenant_id, employment["start_date"])
    db.commit()
    return {"person_id": person.id, "employment_id": emp.id}


def _employment_for_write(db: Session, ctx: AccessContext, employment_id: int, record_version: int
                          ) -> EmploymentRelationship:
    emp = db.get(EmploymentRelationship, employment_id, with_for_update=True)
    if emp is None or emp.tenant_id != ctx.tenant_id:
        raise NotFound("Employment not found")
    _require_entity_scope(ctx, "people.write", emp.legal_entity_id)
    if emp.record_version != record_version:
        raise Conflict({"message": "The employment was changed by someone else; reload and retry",
                        "current_record_version": emp.record_version})
    return emp


def _close_assignments(db: Session, emp: EmploymentRelationship, end_date: date) -> None:
    """Assignments are half-open; close those still running after the (new) last employed day."""
    stop = end_date + timedelta(days=1)
    for a in db.execute(select(Assignment).where(
            Assignment.employment_id == emp.id, Assignment.valid_from <= end_date,
            or_(Assignment.valid_to.is_(None), Assignment.valid_to > stop))).scalars():
        a.valid_to = stop


def terminate_employment(db: Session, ctx: AccessContext, employment_id: int, *, end_date: date,
                         termination_category: str, record_version: int,
                         termination_reason_code: str | None = None) -> dict:
    emp = _employment_for_write(db, ctx, employment_id, record_version)
    if emp.termination_reason_code == TRANSFER_REASON_CODE:
        # the spell was closed by a transfer; changing it here would desynchronise the follow-up employment
        raise Conflict("Employment was ended by an internal transfer; correct the transfer instead")
    if end_date < emp.start_date:
        raise Invalid("end_date must not be before the employment start_date")
    old_end = emp.end_date
    emp.end_date = end_date
    emp.termination_category = termination_category
    # ADR-023 / WP-5 L-8: a health-implying, withheld or unmapped SGK exit code is never stored, also not manually
    emp.termination_reason_code = exit_codes.storable_code(db, ctx.tenant_id, termination_reason_code, end_date)
    emp.termination_reason_source = "manual"
    emp.planned_end_verified = None  # a manual correction is never a verified planned end (WP-5 3.3: NULL = counted)
    emp.record_version += 1
    emp.updated_by = ctx.user_id
    _close_assignments(db, emp, end_date)
    # back-dated terminations allowed: invalidate from the earliest date whose numbers change
    mark_stale_from(db, ctx.tenant_id, min(end_date, old_end) if old_end else end_date)
    db.commit()
    return employment_json(emp)


def transfer_employment(db: Session, ctx: AccessContext, employment_id: int, *, target_legal_entity_id: int,
                        transfer_date: date, record_version: int) -> dict:
    emp = _employment_for_write(db, ctx, employment_id, record_version)
    target = _entity(db, ctx, target_legal_entity_id, status=Invalid)
    _require_entity_scope(ctx, "people.write", target.id)
    if target.id == emp.legal_entity_id:
        raise Invalid("target_legal_entity_id must differ from the current legal entity")
    last_day = transfer_date - timedelta(days=1)
    if last_day < emp.start_date:
        raise Invalid("transfer_date must be after the employment start_date")
    if emp.end_date is not None:
        # only an active (open) employment can be transferred; prevents double transfers creating duplicate spells
        raise Conflict("Employment has already ended; only an active employment can be transferred")
    overlap = db.execute(select(EmploymentRelationship.id).where(
        EmploymentRelationship.person_id == emp.person_id, EmploymentRelationship.legal_entity_id == target.id,
        or_(EmploymentRelationship.end_date.is_(None), EmploymentRelationship.end_date >= transfer_date))).first()
    if overlap:
        raise Conflict("The person already has an employment with the target legal entity on/after the transfer date")
    primary = db.execute(select(Assignment).where(
        Assignment.employment_id == emp.id, Assignment.is_primary.is_(True), Assignment.valid_from <= last_day,
        or_(Assignment.valid_to.is_(None), Assignment.valid_to > last_day)).order_by(Assignment.valid_from.desc())
    ).scalars().first()
    project_id = primary.project_id if primary else None
    occupation_id = primary.occupation_id if primary else None
    # history is never overwritten: end the current spell (gap of 1 day <= KPI transfer window) and start a new one
    emp.end_date = last_day
    emp.termination_category = "other"
    emp.termination_reason_code = TRANSFER_REASON_CODE
    emp.record_version += 1
    emp.updated_by = ctx.user_id
    _close_assignments(db, emp, last_day)
    new = _create_employment(db, ctx, person_id=emp.person_id, legal_entity_id=target.id, start_date=transfer_date,
                             contract_type=emp.contract_type, project_id=project_id, occupation_id=occupation_id,
                             source_system="manual", source_record_id=f"transfer:{emp.id}")
    mark_stale_from(db, ctx.tenant_id, last_day)
    db.commit()
    return {"ended_employment_id": emp.id, "new_employment_id": new.id, "person_id": emp.person_id,
            "ended_record_version": emp.record_version}


# ------------------------------------------------------------------ organisation


def entity_json(e: LegalEntity) -> dict:
    return {"id": e.id, "code": e.code, "name": e.name, "entity_type": e.entity_type, "type": e.entity_type,
            "country": e.country, "is_active": e.is_active}


def create_legal_entity(db: Session, ctx: AccessContext, *, code: str, name: str, entity_type: str,
                        country: str) -> dict:
    if not _covers(ctx, "org.write", None):
        # a new entity is outside every entity-scoped grant; only tenant-wide org writers may create one
        raise Forbidden("Creating legal entities requires tenant-wide org.write", permission="org.write")
    if db.execute(select(LegalEntity.id).where(LegalEntity.tenant_id == ctx.tenant_id,
                                               func.lower(LegalEntity.code) == code.lower())).scalar() is not None:
        raise Conflict(f"Legal entity code '{code}' already exists")
    le = LegalEntity(tenant_id=ctx.tenant_id, code=code, name=name.strip(), entity_type=entity_type,
                     country=country.upper(), source_system="manual", updated_by=ctx.user_id)
    db.add(le)
    db.commit()
    return entity_json(le)


def relationship_json(r: ContractorRelationship) -> dict:
    return {"id": r.id, "parent_entity_id": r.parent_entity_id, "child_entity_id": r.child_entity_id,
            "project_id": r.project_id, "tier": r.tier, "scope_of_work": r.scope_of_work,
            "data_sharing_granted": r.data_sharing_granted, "valid_from": r.valid_from, "valid_to": r.valid_to}


def _require_org_scope(ctx: AccessContext, *entity_ids: int) -> None:
    # both ends: a relationship with data sharing widens the parent's visibility to the child's workforce data
    for eid in entity_ids:
        if not ctx.covers_entity("org.write", eid):
            raise Forbidden("Requested legal entity is outside your access scope", permission="org.write",
                            entity_id=str(eid))


def create_contractor_relationship(db: Session, ctx: AccessContext, *, parent_entity_id: int, child_entity_id: int,
                                   project_id: int | None, tier: int, valid_from: date, data_sharing_granted: bool,
                                   scope_of_work: str | None) -> dict:
    if parent_entity_id == child_entity_id:
        raise Invalid("parent_entity_id and child_entity_id must differ")
    _entity(db, ctx, parent_entity_id, status=Invalid)
    _entity(db, ctx, child_entity_id, status=Invalid)
    _require_org_scope(ctx, parent_entity_id, child_entity_id)
    if project_id is not None:
        pr = db.get(Project, project_id)
        if pr is None or pr.tenant_id != ctx.tenant_id:
            raise Invalid(f"Project {project_id} not found")
    overlap = db.execute(select(ContractorRelationship.id).where(
        ContractorRelationship.tenant_id == ctx.tenant_id,
        ContractorRelationship.parent_entity_id == parent_entity_id,
        ContractorRelationship.child_entity_id == child_entity_id,
        ContractorRelationship.project_id.is_(None) if project_id is None
        else ContractorRelationship.project_id == project_id,
        or_(ContractorRelationship.valid_to.is_(None), ContractorRelationship.valid_to > valid_from))).scalar()
    if overlap is not None:
        raise Conflict({"message": "An overlapping relationship already exists for this parent/child/project",
                        "relationship_id": overlap})
    r = ContractorRelationship(tenant_id=ctx.tenant_id, parent_entity_id=parent_entity_id,
                               child_entity_id=child_entity_id, project_id=project_id, tier=tier,
                               scope_of_work=scope_of_work, data_sharing_granted=data_sharing_granted,
                               valid_from=valid_from, valid_to=None)
    db.add(r)
    db.flush()
    # contractor structure feeds scope resolution and contractor breakdowns: invalidate conservatively (ADR-015)
    mark_stale_from(db, ctx.tenant_id, valid_from)
    db.commit()
    return relationship_json(r)


def end_contractor_relationship(db: Session, ctx: AccessContext, relationship_id: int, *, valid_to: date) -> dict:
    r = db.get(ContractorRelationship, relationship_id, with_for_update=True)
    if r is None or r.tenant_id != ctx.tenant_id:
        raise NotFound("Contractor relationship not found")
    _require_org_scope(ctx, r.parent_entity_id, r.child_entity_id)
    if r.valid_to is not None:
        raise Conflict("Contractor relationship is already ended")
    if valid_to <= r.valid_from:
        raise Invalid("valid_to must be after valid_from")
    r.valid_to = valid_to
    mark_stale_from(db, ctx.tenant_id, valid_to)
    db.commit()
    return relationship_json(r)
