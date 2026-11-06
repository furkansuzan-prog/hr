"""Scope-aware authorization (ADR-005, ADR-017).

AccessContext answers, per permission, *which legal entities' data* the user may see:
  - None  -> every entity of the tenant (tenant-scoped grant)
  - set() -> the explicit set (legal-entity-scoped grant, expanded to subcontractors only where an
             active contractor relationship has data_sharing_granted = true)

ADR-017: an entity reached through a contractor relationship that carries a project_id is visible ONLY for that
project (or projects). Such entities are listed in `project_limits[perm]` = {entity_id: {project_id, ...}}; an entity
in the grant set but not in project_limits is visible as a whole. Workforce data of a project-limited entity is
visible only when it can be attributed to an allowed project (rows with a NULL project are NOT visible).

Every service query that touches workforce data must filter with `entity_clause(perm, ...)` (ORM) or, for the KPI
engine, `scope_for(perms)` folded into `calculators.Scope`. `entity_filter(perm)` still returns the entity id set
(project-limited entities included) for callers that only need entity-level decisions; it alone is NOT enough to
filter workforce rows. Writes require whole-entity access: `covers_entity`.

D-9 / ADR-025: with HRSWI_FEATURE_SUBCONTRACTORS off (default) there is no expansion through contractor relationships:
a legal-entity assignment covers exactly that entity, so project_limits stay empty.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.models import Assignment, ContractorRelationship, LegalEntity, RolePermission, User, UserRoleAssignment
from app.security.features import subcontractors_enabled

# entity_id -> allowed project ids (only for project-limited entities)
ProjectLimits = dict[int, frozenset[int]]


@dataclass
class AccessContext:
    user_id: int
    tenant_id: int
    email: str
    locale: str
    # permission -> None (tenant-wide) | set of legal_entity ids
    grants: dict[str, set[int] | None] = field(default_factory=dict)
    # permission -> {entity_id: allowed project ids}; entities absent here are visible as a whole (ADR-017)
    project_limits: dict[str, ProjectLimits] = field(default_factory=dict)

    @property
    def permissions(self) -> set[str]:
        return set(self.grants)

    def has(self, perm: str) -> bool:
        return perm in self.grants

    def entity_filter(self, perm: str) -> set[int] | None:
        """None = unrestricted within tenant. Raises PermissionError if the permission is not held at all.
        Includes project-limited entities: use entity_clause / scope_for to filter workforce rows."""
        if perm not in self.grants:
            raise PermissionError(perm)
        return self.grants[perm]

    def limits(self, perm: str) -> ProjectLimits:
        """Project-limited entities for `perm` (empty for tenant-wide grants)."""
        if self.grants.get(perm, set()) is None:
            return {}
        return dict(self.project_limits.get(perm, {}))

    def can_see_entity(self, perm: str, entity_id: int) -> bool:
        """Some visibility of the entity (whole, or limited to projects). Read paths must still apply
        entity_clause; write paths use covers_entity."""
        if perm not in self.grants:
            return False
        scope = self.grants[perm]
        return scope is None or entity_id in scope

    def covers_entity(self, perm: str, entity_id: int) -> bool:
        """Whole-entity access (required for writes and entity-wide administration)."""
        return self.can_see_entity(perm, entity_id) and entity_id not in self.limits(perm)

    def restrict(self, perm: str, requested: set[int] | None) -> set[int] | None:
        """Intersect a user-requested entity filter with the permitted scope.
        Requesting an entity outside the scope silently narrows to the allowed subset - callers that
        must report denial should use can_see_entity first. Project limits still apply (entity_clause)."""
        allowed = self.entity_filter(perm)
        if requested is None:
            return allowed
        if allowed is None:
            return set(requested)
        return set(requested) & allowed

    def scope_for(self, perms: list[str]) -> tuple[set[int] | None, ProjectLimits]:
        """Effective (entities, project limits) for data that needs ALL of `perms` (e.g. kpi.read + cost.read).
        Entities: intersection. Limits: an entity limited under any permission stays limited; limited under several
        -> intersection of their projects (an empty intersection removes the entity)."""
        entities: set[int] | None = None
        limits: dict[int, frozenset[int]] = {}
        for p in perms:
            s = self.entity_filter(p)
            if s is not None:
                entities = set(s) if entities is None else entities & s
            for eid, projs in self.limits(p).items():
                limits[eid] = limits[eid] & projs if eid in limits else projs
        if entities is None:
            return None, {}
        for eid in [e for e, ps in limits.items() if not ps]:
            entities.discard(eid)
        return entities, {e: ps for e, ps in limits.items() if e in entities and ps}

    def scope_signature(self, perm: str) -> str:
        """Canonical text of the effective scope of `perm` (cache keys must include project limits)."""
        if not self.has(perm):
            return "-"
        s = self.entity_filter(perm)
        if s is None:
            return "*"
        lim = self.limits(perm)
        return ",".join(str(e) + (("@" + "+".join(map(str, sorted(lim[e])))) if e in lim else "") for e in sorted(s))

    def entity_clause(self, perm: str, entity_col, *, project_col=None, employment_col=None):
        """SQLAlchemy condition restricting rows to the permitted scope, or None when unrestricted.
        Rows of a project-limited entity pass only when attributable to an allowed project:
          project_col    -> the row's own project column must be an allowed project (NULL never passes);
          employment_col -> the employment must have an assignment on an allowed project;
          neither        -> rows of project-limited entities are never visible (no project dimension)."""
        s = self.entity_filter(perm)
        if s is None:
            return None
        lim = self.limits(perm)
        full = {e for e in s if e not in lim}
        conds = [entity_col.in_(full or {-1})]
        for eid, projs in sorted(lim.items()):
            if project_col is not None:
                conds.append(and_(entity_col == eid, project_col.in_(projs)))
            elif employment_col is not None:
                conds.append(and_(entity_col == eid, exists().where(
                    Assignment.employment_id == employment_col, Assignment.project_id.in_(projs))))
        return or_(*conds) if len(conds) > 1 else conds[0]

    def employment_visible(self, db: Session, perm: str, entity_id: int, employment_id: int) -> bool:
        if not self.can_see_entity(perm, entity_id):
            return False
        lim = self.limits(perm)
        if entity_id not in lim:
            return True
        return db.execute(select(Assignment.id).where(Assignment.employment_id == employment_id,
                                                      Assignment.project_id.in_(lim[entity_id]))).first() is not None


def _shared_subcontractors(db: Session, tenant_id: int, roots: set[int], on: date) -> dict[int, frozenset[int] | None]:
    """Transitively expand roots through contractor relationships that are valid on `on` and explicitly grant data
    sharing. Relationships without the grant do NOT expose the child (no automatic access).

    Returns {entity_id: None (whole entity) | frozenset(project ids)} (ADR-017):
      - roots are whole entities;
      - a relationship with project_id P exposes the child only for P;
      - a relationship with project_id NULL exposes the child as a whole - unless the parent itself is limited, in
        which case the child inherits the parent's projects;
      - from a parent limited to projects L, only relationships on a project in L (or NULL) are followed.
    A whole-entity path always wins over a project-limited one; limited paths union their projects."""
    state: dict[int, frozenset[int] | None] = {r: None for r in roots}
    frontier = set(roots)
    while frontier:
        rows = db.execute(
            select(ContractorRelationship.parent_entity_id, ContractorRelationship.child_entity_id,
                   ContractorRelationship.project_id).where(
                ContractorRelationship.tenant_id == tenant_id,
                ContractorRelationship.parent_entity_id.in_(frontier),
                ContractorRelationship.data_sharing_granted.is_(True),
                ContractorRelationship.valid_from <= on,
                or_(ContractorRelationship.valid_to.is_(None), ContractorRelationship.valid_to > on),
            )
        ).all()
        nxt: set[int] = set()
        for parent, child, project in rows:
            plim = state[parent]
            if plim is None:
                lim = None if project is None else frozenset({project})
            elif project is None:
                lim = plim
            elif project in plim:
                lim = frozenset({project})
            else:
                continue
            if child in state:
                cur = state[child]
                if cur is None:
                    continue
                new = None if lim is None else cur | lim
                if new == cur:
                    continue
            else:
                new = lim
            state[child] = new
            nxt.add(child)
        frontier = nxt
    return state


def build_access_context(db: Session, user: User, on: date | None = None) -> AccessContext:
    on = on or date.today()
    expand = subcontractors_enabled()  # D-9: firm-based unless the subcontractor feature is on
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
        expanded: dict[int, frozenset[int] | None] | None
        if a.scope_type == "tenant":
            expanded = None
        else:
            # the scoped entity must belong to the user's tenant
            ok = db.execute(
                select(LegalEntity.id).where(and_(LegalEntity.id == a.legal_entity_id, LegalEntity.tenant_id == user.tenant_id))
            ).scalar()
            if not ok:
                continue
            expanded = (_shared_subcontractors(db, user.tenant_id, {a.legal_entity_id}, on) if expand
                        else {a.legal_entity_id: None})
        for p in perms:
            if p in ctx.grants and ctx.grants[p] is None:
                continue
            if expanded is None:
                ctx.grants[p] = None
                ctx.project_limits.pop(p, None)
                continue
            had = ctx.grants.get(p) or set()
            lim = ctx.project_limits.setdefault(p, {})
            for eid, projs in expanded.items():
                if eid in had and eid not in lim:
                    continue  # already whole-entity
                if projs is None:
                    lim.pop(eid, None)
                else:
                    lim[eid] = lim.get(eid, frozenset()) | projs
                had.add(eid)
            ctx.grants[p] = had
            if not lim:
                ctx.project_limits.pop(p)
    return ctx

