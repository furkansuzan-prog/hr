"""Hand-built 'EXACT' tenant whose KPI values are computed by hand in the docstrings below.

Entities: A (main contractor), B (sub of A, data sharing granted), C (sub of A, NO data sharing),
D (sub of A until 2025-01-01, sharing granted - relationship ended), F (sub of A, sharing, only 3 people).

People (Q1 2025 = 2025-01-01..2025-03-31):
  A: 10 stable persons (2024-01-01 -> open)
     P1: A 2025-01-01..2025-03-31 voluntary, then A again from 2025-06-01 (two spells, scenario 1)
     P2: A 2024-01-01..2025-02-28, then B from 2025-03-01 (transfer, scenario 2)
     P3: A 2024-06-01..2025-02-15, termination reason NOT provided (unknown)
  B: 6 stable, C: 6 stable, F: 3 persons from 2025-01-01

KPI-001 tenant Q1: headcount at 01-01=25, 01-31=25 (+F 3 = 28,28), 02-28: 27, 03-31: 27  (F included)
   -> avg = (28+28+27+27)/4 = 27.5 ; separations = P1 + P3 = 2 (P2 is an internal transfer) -> 7.2727 %
KPI-001 entity A Q1: headcount 13,13,12,11 -> avg 12.25 ; separations P1,P2,P3 = 3 -> 24.4898 %
KPI-003 A Jan 2025: (sick 9h + annual 18h) / planned 450h = 6.0 %  (company definition counts annual leave)
KPI-007 A Jan 2025: overtime 50h / worked 500h = 10.0 %
KPI-019 B Mar 2025: 7 persons x 1000 EUR x 45.0 = 315000 TRY / avg headcount 7 = 45000.0
"""
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    ContractorRelationship,
    EmploymentRelationship,
    FxRate,
    LeaveRecord,
    LegalEntity,
    PayrollCost,
    Person,
    Tenant,
    Timesheet,
    User,
    UserRoleAssignment,
)
from app.security.auth import hash_password, national_id_hash
from app.seed.reference import seed_permissions_and_roles, seed_reference
from app.services.coverage import declare_complete

D = date


def declare_all_feeds(db: Session, tenant_id: int, entity_ids=None, first: date = date(2023, 1, 1),
                      last: date = date(2026, 12, 31)) -> None:
    """B-20 / ADR-020: hand-built test tenants declare every feed complete, so the coverage gate leaves their
    hand-calculated values and colours unchanged (the gate itself is tested in tests/test_feed_coverage.py).
    entity_ids None = every legal entity of the tenant (flushed)."""
    from sqlalchemy import select

    from app.models import FEED_TYPES

    db.flush()
    if entity_ids is None:
        entity_ids = db.execute(select(LegalEntity.id).where(LegalEntity.tenant_id == tenant_id)).scalars().all()

    # hand-built timesheets carry the planned hours of leave days too (WP-4 3.5 scenario A, ADR-024)
    declare_complete(db, tenant_id, entity_ids, FEED_TYPES, first, last, note="test tenant: feeds complete",
                     invalidate=False, planned_on_leave_days="included")


def assign_default_pattern(db: Session, tenant_id: int, entity_ids=None, code: str = "TR-5x9", weekdays=(1, 2, 3, 4, 5),
                           daily_hours: float = 9):
    """ADR-024: the leave-days KPI-055/056 versions need a known work pattern (min_pattern_coverage). Hand-built
    tenants whose leave hours are 9 h per day get the 5 x 9 h pattern on every legal entity - their v1 values stay the
    same (WP-4 E2: a 5-day pattern converts like the old /9 rule)."""
    from sqlalchemy import select

    from app.services.work_patterns import assign_pattern, define_pattern

    db.flush()
    if entity_ids is None:
        entity_ids = db.execute(select(LegalEntity.id).where(LegalEntity.tenant_id == tenant_id)).scalars().all()
    p = define_pattern(db, tenant_id, code, list(weekdays), daily_hours)
    for e in entity_ids:
        assign_pattern(db, tenant_id, p, "legal_entity", e, date(2000, 1, 1), invalidate=False)
    return p


def build_exact_tenant(db: Session) -> dict:
    seed_reference(db)
    t = Tenant(code="EXACT", name="Exact test tenant")
    db.add(t)
    db.flush()
    roles = seed_permissions_and_roles(db, t.id)
    ents = {}
    for code, typ in [("A", "main_contractor"), ("B", "subcontractor"), ("C", "subcontractor"),
                      ("D", "subcontractor"), ("F", "subcontractor")]:
        e = LegalEntity(tenant_id=t.id, code=code, name=f"Entity {code}", entity_type=typ)
        db.add(e)
        ents[code] = e
    db.flush()
    for child, sharing, vf, vt in [("B", True, D(2020, 1, 1), None), ("C", False, D(2020, 1, 1), None),
                                   ("D", True, D(2020, 1, 1), D(2025, 1, 1)), ("F", True, D(2020, 1, 1), None)]:
        db.add(ContractorRelationship(tenant_id=t.id, parent_entity_id=ents["A"].id, child_entity_id=ents[child].id,
                                      tier=1, data_sharing_granted=sharing, valid_from=vf, valid_to=vt))
    n = 0

    def person(last="Exact", nid=None):
        nonlocal n
        n += 1
        p = Person(tenant_id=t.id, person_code=f"X{n:04d}", first_name="Test", last_name=f"{last}{n}",
                   national_id_hash=national_id_hash(nid) if nid else None)
        db.add(p)
        db.flush()
        return p

    def emp(p, ent, s, e=None, cat=None):
        r = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ents[ent].id, start_date=s,
                                   end_date=e, termination_category=cat)
        db.add(r)
        db.flush()
        return r

    stable_a = []
    for i in range(10):
        p = person(nid=f"1000000000{i}")
        stable_a.append((p, emp(p, "A", D(2024, 1, 1))))
    p1 = person()
    emp(p1, "A", D(2025, 1, 1), D(2025, 3, 31), "voluntary")
    emp(p1, "A", D(2025, 6, 1))
    p2 = person()
    emp(p2, "A", D(2024, 1, 1), D(2025, 2, 28), "other")
    e_p2b = emp(p2, "B", D(2025, 3, 1))
    p3 = person()
    emp(p3, "A", D(2024, 6, 1), D(2025, 2, 15), None)
    b_emps = [emp(person(), "B", D(2024, 1, 1)) for _ in range(6)] + [e_p2b]
    c_emps = [emp(person(), "C", D(2024, 1, 1)) for _ in range(6)]
    for _ in range(3):
        emp(person(), "F", D(2025, 1, 1))

    # timesheets: A Jan 2025 (one weekly row per stable person); none in Feb (missing data scenario)
    for _, e in stable_a:
        db.add(Timesheet(tenant_id=t.id, employment_id=e.id, work_date=D(2025, 1, 6), planned_hours=45,
                         worked_hours=50, overtime_hours=5))
    db.add(LeaveRecord(tenant_id=t.id, employment_id=stable_a[0][1].id, leave_type="sick", start_date=D(2025, 1, 7),
                       end_date=D(2025, 1, 7), hours=9))
    db.add(LeaveRecord(tenant_id=t.id, employment_id=stable_a[1][1].id, leave_type="annual", start_date=D(2025, 1, 8),
                       end_date=D(2025, 1, 9), hours=18))
    # C Jan 2025: rows exist but planned = worked = 0  -> denominator zero
    for e in c_emps:
        db.add(Timesheet(tenant_id=t.id, employment_id=e.id, work_date=D(2025, 1, 6), planned_hours=0,
                         worked_hours=0, overtime_hours=0))
    # costs: B in EUR (rate exists), C in RUB (no rate -> insufficient), A none
    for e in b_emps:
        db.add(PayrollCost(tenant_id=t.id, employment_id=e.id, period_month=D(2025, 3, 1), cost_type="gross_wage",
                           amount=1000, currency="EUR"))
    for e in c_emps:
        db.add(PayrollCost(tenant_id=t.id, employment_id=e.id, period_month=D(2025, 3, 1), cost_type="gross_wage",
                           amount=50000, currency="RUB"))
    db.add(FxRate(rate_date=D(2025, 3, 31), from_ccy="EUR", to_ccy="TRY", rate=45.0, source="TEST"))

    pw = hash_password("Demo!2345")
    users = {}
    for email, role, ent in [("ex.admin@exact.local", "SYSTEM_ADMIN", None),
                             ("ex.aops@exact.local", "OPERATIONS_DIRECTOR", "A"),
                             ("ex.b@exact.local", "CONTRACTOR_MANAGER", "B"),
                             ("ex.c@exact.local", "CONTRACTOR_MANAGER", "C"),
                             ("ex.pm@exact.local", "PROJECT_MANAGER", None),
                             ("ex.hse@exact.local", "HSE_SPECIALIST", None),
                             ("ex.auditor@exact.local", "INTERNAL_AUDITOR", None),
                             ("ex.hrdir@exact.local", "HR_DIRECTOR", None),
                             ("ex.hrman@exact.local", "HR_MANAGER", "A")]:
        u = User(tenant_id=t.id, email=email, full_name=email.split("@")[0], password_hash=pw, locale="en")
        db.add(u)
        db.flush()
        users[email] = u.id
        db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role], scope_type="legal_entity" if ent else "tenant",
                                  legal_entity_id=ents[ent].id if ent else None))
    declare_all_feeds(db, t.id, [e.id for e in ents.values()])
    db.commit()
    return {"tenant_id": t.id, "entities": {k: v.id for k, v in ents.items()}, "users": users,
            "p1": p1.id, "p2": p2.id, "stable_a_person": stable_a[0][0].id}
