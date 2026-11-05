"""Scope-aware authorization (ADR-005).

AccessContext answers, per permission, *which legal entities' data* the user may see:
  - None  -> every entity of the tenant (tenant-scoped grant)
  - set() -> the explicit set (legal-entity-scoped grant, expanded to subcontractors only where an
             active contractor relationship has data_sharing_granted = true)

Every service query that touches workforce data must filter with `entity_filter(perm)`.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import ContractorRelationship, LegalEntity, RolePermission, User, UserRoleAssignment


@dataclass
class AccessContext:
    user_id: int
    tenant_id: int
    email: str
    locale: str
    # permission -> None (tenant-wide) | set of legal_entity ids
    grants: dict[str, set[int] | None] = field(default_factory=dict)

    @property
    def permissions(self) -> set[str]:
        return set(self.grants)

    def has(self, perm: str) -> bool:
        return perm in self.grants

    def entity_filter(self, perm: str) -> set[int] | None:
        """None = unrestricted within tenant. Raises KeyError if the permission is not held at all."""
        if perm not in self.grants:
            raise PermissionError(perm)
        return self.grants[perm]

    def can_see_entity(self, perm: str, entity_id: int) -> bool:
        if perm not in self.grants:
            return False
        scope = self.grants[perm]
        return scope is None or entity_id in scope

    def restrict(self, perm: str, requested: set[int] | None) -> set[int] | None:
        """Intersect a user-requested entity filter with the permitted scope.
        Requesting an entity outside the scope silently narrows to the allowed subset - callers that
        must report denial should use can_see_entity first."""
        allowed = self.entity_filter(perm)
        if requested is None:
            return allowed
        if allowed is None:
            return set(requested)
        return set(requested) & allowed


def _shared_subcontractors(db: Session, tenant_id: int, roots: set[int], on: date) -> set[int]:
    """Transitively expand roots through contractor relationships that are valid on `on` and explicitly
    grant data sharing. Relationships without the grant do NOT expose the child (no automatic access)."""
    seen = set(roots)
    frontier = set(roots)
    while frontier:
        rows = db.execute(
            select(ContractorRelationship.child_entity_id).where(
                ContractorRelationship.tenant_id == tenant_id,
                ContractorRelationship.parent_entity_id.in_(frontier),
                ContractorRelationship.data_sharing_granted.is_(True),
                ContractorRelationship.valid_from <= on,
                or_(ContractorRelationship.valid_to.is_(None), ContractorRelationship.valid_to > on),
            )
        ).scalars()
        nxt = set(rows) - seen
        seen |= nxt
        frontier = nxt
    return seen


def build_access_context(db: Session, user: User, on: date | None = None) -> AccessContext:
    on = on or date.today()
    ctx = AccessContext(user_id=user.id, tenant_id=user.tenant_id, email=user.email, locale=user.locale)
    assignments = db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user.id,
            or_(UserRoleAssignment.valid_from.is_(None), UserRoleAssignment.valid_from <= on),
            or_(UserRoleAssignment.valid_to.is_(None), UserRoleAssignment.valid_to > on),
        )
    ).scalars()
    for a in assignments:
        perms = db.execute(select(RolePermission.permission_code).where(RolePermission.role_id == a.role_id)).scalars()
        if a.scope_type == "tenant":
            scope: set[int] | None = None
        else:
            # the scoped entity must belong to the user's tenant
            ok = db.execute(
                select(LegalEntity.id).where(and_(LegalEntity.id == a.legal_entity_id, LegalEntity.tenant_id == user.tenant_id))
            ).scalar()
            if not ok:
                continue
            scope = _shared_subcontractors(db, user.tenant_id, {a.legal_entity_id}, on)
        for p in perms:
            if p in ctx.grants and ctx.grants[p] is None:
                continue
            if scope is None:
                ctx.grants[p] = None
            else:
                ctx.grants[p] = (ctx.grants.get(p) or set()) | scope
    return ctx
