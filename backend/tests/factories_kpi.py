"""Hand-built 'KPIX' tenant for the B-10 KPIs (KPI-020/021/022/023/028/032/034/036/048/050) and the B-18 period
close. Kept separate from the EXACT tenant (tests/factories.py) so existing exact-value tests are untouched.

Entities: MC (main_contractor), S1 (subcontractor), S2 (subcontractor, only 3 people -> always suppressed).
No contractor relationships: an MC-scoped user sees MC only.
Project P1 is owned by MC. Reference month M = March 2025 (2025-03-01..2025-03-31, 31 days).

People (active at 2025-03-31 unless stated):
  MC9   9 persons MC 2021-03-31 -> open                     tenure 1461 d each; gender 4 F, 5 M
  R1    MC 2010-01-01..2020-12-31 retirement, MC again 2024-03-31 -> open (re-hire)   tenure 365 d (current spell); F
  C1    S1 2024-01-01 -> open AND MC 2024-12-01 -> open (concurrent spells)           M
  S1x5  5 more persons S1 2024-01-01 -> open (S1 has 6 stable incl. C1)  tenure 455 d; gender 2 F, 2 M, 1 unknown
  T1    MC 2022-01-01..2025-03-12 voluntary, S1 from 2025-03-14 (transfer within 7 days)   tenure 17 d; 'kadın' (F)
  S2x3  S2 2024-06-01 -> open                                tenure 303 d; gender M, M, unknown
  L1/L2 MC 2023-01-01..2025-03-10 / ..2025-03-20 voluntary ; L3 ..2025-03-15 employer_initiated ;
  L4    MC 2023-01-01..2025-03-05, NO termination reason (unknown)
  Early-turnover cohort (Jan 2024, all ended by 2024-12-31 so they do not touch March 2025):
  H1 MC 2024-01-08..01-22 (14 d) | H2 MC 01-08..03-01 (53 d) | H3 MC 01-10..06-30 (172 d) | H4 MC 01-10..12-31 (356 d)
  H5 MC 01-15..08-31 (229 d)     | H6 MC 01-20..02-05 then S1 2024-02-06..12-31 (transfer out at tenant level)

Headcount tenant: 03-01 = MC{MC9,R1,C1,T1,L1-4}=16 + S1 new {5} + S2 3 = 24 ; 03-31 = MC{MC9,R1,C1}=11 + S1 new {5,T1}
  + S2 3 = 20 -> avg 22.  MC entity: 03-01 = 16, 03-31 = 11 -> avg 13.5
KPI-020 tenant M: voluntary L1, L2 (T1 is a transfer) = 2 / 22 = 9.0909 % ; annualised x365/31 = 107.0381
   quality: 1 of 4 separations (L1-L4) has no reason -> 75.0 ; KPI-001 for comparison 4/22 = 18.1818
KPI-020 MC M: L1, L2, T1 (transfer out of scope) = 3 / 13.5 = 22.2222 %
KPI-021 tenant cohort Jan 2024: hires H1-H6 + S1 stable 6 (start 2024-01-01) = 12 ; <30 d: H1 -> 8.3333 %
KPI-022 tenant: <180 d: H1, H2, H3 = 3 / 12 = 25.0 %
KPI-021 MC: hires H1-H6 = 6 ; <30 d: H1, H6 (H6's S1 spell is out of scope) = 2 -> 33.3333 %
KPI-022 MC: H1, H2, H3, H6 = 4 / 6 = 66.6667 %
KPI-023 tenant 03-31: 20 persons, days 9x1461 + 365 + 6x455 (C1: max(455,120)) + 17 + 3x303 = 17170
   -> 17170 / 365.25 / 20 = 2.3504 years ; MC: 9x1461 + 365 + 120 (C1's MC spell) = 13634 / 365.25 / 11 = 3.3934
KPI-028 MC/tenant M: timesheets MC9 + R1 = 10 rows x planned 40 = 400 h ; sick 8 h (03-04) + 16 h over 03-30..04-02
   prorated 2/4 = 8 h -> 16 / 400 = 4.0 %  (annual 8 h and unexcused 4 h excluded)
   KPI-003 company definition: sick 16 + unexcused 4 + annual 8 = 28 / 400 = 7.0 %
KPI-032 M: night 10 x 11 = 110 h / worked 10 x 44 = 440 h = 25.0 %
KPI-034 M (timesheets on P1): DT1 MC equipment 20 h + DT2 MC weather 10 h + DT3 no entity material 6 h
   (+ DT4 2025-04-02 outside) -> tenant 36 / 400 = 9.0 % ; MC 30 / 400 = 7.5 % (1 event without entity excluded)
KPI-036 MC/tenant M: gross 10 x 1000 TRY + overtime 10 x 200 TRY + employer_ss 10 x 20 CHF x 40.0 = 20000 TRY
   -> overtime 2000 / 20000 = 10.0 % ; Feb 2025: S1 costs in JPY (no FX rate) -> insufficient_data
KPI-048 tenant 03-31: F = 4 + R1 + 2 + T1 = 8 ; M = 5 + C1 + 2 + 2 (S2) = 10 ; unknown 2 -> 8/18 = 44.4444 %,
   quality 18/20 = 90.0 ; MC: F 5 (4 + R1), M 6 (5 + C1) -> 45.4545 % ; S1: F 3 (<5) -> suppressed
KPI-050 tenant 03-31: persons employed by a subcontractor = S1 {6, T1} 7 + S2 3 = 10 / 20 = 50.0 % ;
   MC: 0 / 11 = 0.0 % (C1's S1 spell is outside the MC scope)
"""
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    DowntimeEvent,
    EmploymentRelationship,
    FxRate,
    LeaveRecord,
    LegalEntity,
    PayrollCost,
    Person,
    Project,
    Tenant,
    Timesheet,
    User,
    UserRoleAssignment,
)
from app.security.auth import hash_password
from app.seed.reference import seed_permissions_and_roles, seed_reference

D = date


def build_kpi_tenant(db: Session) -> dict:
    seed_reference(db)
    t = Tenant(code="KPIX", name="KPI extended test tenant")
    db.add(t)
    db.flush()
    roles = seed_permissions_and_roles(db, t.id)
    ents = {}
    for code, typ in [("MC", "main_contractor"), ("S1", "subcontractor"), ("S2", "subcontractor")]:
        e = LegalEntity(tenant_id=t.id, code=f"KX-{code}", name=f"KPIX {code}", entity_type=typ)
        db.add(e)
        ents[code] = e
    db.flush()
    p1 = Project(tenant_id=t.id, code="KX-P1", name="KPIX Project 1", owner_entity_id=ents["MC"].id)
    db.add(p1)
    db.flush()
    n = 0

    def person(gender=None):
        nonlocal n
        n += 1
        p = Person(tenant_id=t.id, person_code=f"KX{n:04d}", first_name="Kpi", last_name=f"Test{n}", gender=gender)
        db.add(p)
        db.flush()
        return p

    def emp(p, ent, s, e=None, cat=None):
        r = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ents[ent].id, start_date=s,
                                   end_date=e, termination_category=cat)
        db.add(r)
        db.flush()
        return r

    mc_emps = [emp(person(g), "MC", D(2021, 3, 31)) for g in ["F"] * 4 + ["M"] * 5]
    r1 = person("F")
    emp(r1, "MC", D(2010, 1, 1), D(2020, 12, 31), "retirement")
    mc_emps.append(emp(r1, "MC", D(2024, 3, 31)))
    c1 = person("M")
    s1_emps = [emp(c1, "S1", D(2024, 1, 1))]
    emp(c1, "MC", D(2024, 12, 1))
    s1_emps += [emp(person(g), "S1", D(2024, 1, 1)) for g in ["F", "F", "M", "M", None]]
    t1 = person("kadın")
    emp(t1, "MC", D(2022, 1, 1), D(2025, 3, 12), "voluntary")
    emp(t1, "S1", D(2025, 3, 14))
    for g in ["M", "M", None]:
        emp(person(g), "S2", D(2024, 6, 1))
    for end, cat in [(D(2025, 3, 10), "voluntary"), (D(2025, 3, 20), "voluntary"),
                     (D(2025, 3, 15), "employer_initiated"), (D(2025, 3, 5), None)]:
        emp(person(), "MC", D(2023, 1, 1), end, cat)
    # early-turnover cohort, January 2024
    emp(person(), "MC", D(2024, 1, 8), D(2024, 1, 22), "voluntary")
    emp(person(), "MC", D(2024, 1, 8), D(2024, 3, 1), "voluntary")
    emp(person(), "MC", D(2024, 1, 10), D(2024, 6, 30), "employer_initiated")
    emp(person(), "MC", D(2024, 1, 10), D(2024, 12, 31), "contract_end")
    emp(person(), "MC", D(2024, 1, 15), D(2024, 8, 31), "voluntary")
    h6 = person()
    emp(h6, "MC", D(2024, 1, 20), D(2024, 2, 5), "other")
    emp(h6, "S1", D(2024, 2, 6), D(2024, 12, 31), "contract_end")

    # March 2025 timesheets on P1: MC9 + R1
    for e in mc_emps:
        db.add(Timesheet(tenant_id=t.id, employment_id=e.id, project_id=p1.id, work_date=D(2025, 3, 3),
                         planned_hours=40, worked_hours=44, overtime_hours=4, night_hours=11))
    for i, (typ, s, e, h) in enumerate([("sick", D(2025, 3, 4), D(2025, 3, 4), 8),
                                        ("sick", D(2025, 3, 30), D(2025, 4, 2), 16),
                                        ("annual", D(2025, 3, 10), D(2025, 3, 10), 8),
                                        ("unexcused", D(2025, 3, 11), D(2025, 3, 11), 4)]):
        db.add(LeaveRecord(tenant_id=t.id, employment_id=mc_emps[i].id, leave_type=typ, start_date=s, end_date=e,
                           hours=h))
    for ent, d, cause, h in [("MC", D(2025, 3, 5), "equipment", 20), ("MC", D(2025, 3, 18), "weather", 10),
                             (None, D(2025, 3, 25), "material", 6), ("MC", D(2025, 4, 2), "equipment", 50)]:
        db.add(DowntimeEvent(tenant_id=t.id, project_id=p1.id, legal_entity_id=ents[ent].id if ent else None,
                             event_date=d, cause=cause, lost_hours=h, affected_headcount=5))
    for e in mc_emps:
        for ctype, amount, ccy in [("gross_wage", 1000, "TRY"), ("overtime", 200, "TRY"), ("employer_ss", 20, "CHF")]:
            db.add(PayrollCost(tenant_id=t.id, employment_id=e.id, period_month=D(2025, 3, 1), cost_type=ctype,
                               amount=amount, currency=ccy))
    for e in s1_emps:
        db.add(PayrollCost(tenant_id=t.id, employment_id=e.id, period_month=D(2025, 2, 1), cost_type="overtime",
                           amount=5000, currency="JPY"))
    db.add(FxRate(rate_date=D(2025, 3, 31), from_ccy="CHF", to_ccy="TRY", rate=40.0, source="KPIX"))

    pw = hash_password("Demo!2345")
    users = {}
    for email, role, ent in [("kx.admin@kpix.local", "SYSTEM_ADMIN", None),
                             ("kx.hrdir@kpix.local", "HR_DIRECTOR", None),
                             ("kx.mcdir@kpix.local", "HR_DIRECTOR", "MC"),
                             ("kx.ops@kpix.local", "OPERATIONS_DIRECTOR", None)]:
        u = User(tenant_id=t.id, email=email, full_name=email.split("@")[0], password_hash=pw, locale="en")
        db.add(u)
        db.flush()
        users[email] = u.id
        db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role], scope_type="legal_entity" if ent else "tenant",
                                  legal_entity_id=ents[ent].id if ent else None))
    db.commit()
    return {"tenant_id": t.id, "entities": {k: v.id for k, v in ents.items()}, "project": p1.id, "users": users}
