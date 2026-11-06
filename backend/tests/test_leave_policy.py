"""Decisions D-5..D-7: unplanned absence, protected (parental) leave, annual-leave sustainability indicators.

LEAVEX tenant, entity A (main contractor). Year 2025 (365 days, proration factor 1). Hours per day = 9.
Eligible (active 2025-12-31, tenure >= 1 year):
  P1 start 2020-01-01 (6.0 y -> 20 d), born 1990, annual 10 d (90 h)
  P2 start 2023-06-01 (2.6 y -> 14 d), born 1990, annual 14 d (126 h)
  P3 start 2010-01-01 (16 y -> 26 d), born 1990, annual 1 d (the 9 h on 2025-01-09 below)
  P4 start 2023-01-01 (3.0 y -> 14 d, age 55 -> 20 d), born 1970, no annual leave
  P5 start 2024-01-01 (2.0 y -> 14 d), born 1995, annual 7 d (63 h)
  P6 start 2025-06-01 (< 1 y) -> excluded, annual 5 d
  P7 spells 2019-01-01..2023-12-31 and 2024-06-01..open with A: service 2405 d = 6.58 y -> 20 d (current spell alone
     would give 14 d - 4857 md.54 counts all spells), no annual leave
  P8 start 2024-09-01: one-year date 2025-08-31 -> entitlement 14 x 123/365 = 4.7178 d, annual 2 d (Oct);
     not in KPI-056 (one-year date after period start)
  P9 start 2015-06-01 (10.6 y -> 20 d), sick 150 d (1350 h, Feb-Aug) > half of 260.7 working days -> excluded
     from KPI-056 only (counted in lineage); in KPI-055 with 0 taken
KPI-055 = (10 + 14 + 1 + 0 + 7 + 0 + 2 + 0) / (20 + 14 + 26 + 20 + 14 + 20 + 4.7178 + 20) = 34 / 138.7178 = 24.5102 %
KPI-056 population P1-P5, P7 = 6 ; no annual leave: P4, P7 -> 2 / 6 = 33.3333 % (P8 not yet eligible, P9 long absent)
January 2025: timesheets 6 persons x 100 planned h = 600 h.
  leave: P1 sick 9 h, P2 unexcused 9 h, P3 annual 9 h, parental 10 h for P1..P5 (5 takers = 50 h)
  KPI-053 = (9 + 9) / 600 = 3.0 % ; KPI-003 = (9 + 9 + 9) / 600 = 4.5 % (parental excluded) ;
  KPI-054 = 50 / 600 = 8.3333 %
February 2025: same timesheets, parental 10 h for only 4 people -> KPI-054 suppressed.
"""
from datetime import date

import pytest

from app.analytics import engine
from app.models import EmploymentRelationship, LeaveRecord, LegalEntity, Person, Tenant, Timesheet
from app.security.access import AccessContext
from app.services.timekeeping import normalize_leave_type
from tests.factories import assign_default_pattern, declare_all_feeds

D = date


@pytest.fixture(scope="module")
def leavex():
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        t = Tenant(code="LEAVEX", name="Leave policy test tenant")
        db.add(t)
        db.flush()
        a = LegalEntity(tenant_id=t.id, code="A", name="A", entity_type="main_contractor")
        db.add(a)
        db.flush()
        spec = [(D(2020, 1, 1), 1990, 90), (D(2023, 6, 1), 1990, 126), (D(2010, 1, 1), 1990, 0),
                (D(2023, 1, 1), 1970, 0), (D(2024, 1, 1), 1995, 63), (D(2025, 6, 1), 1990, 45)]
        emps = []
        for i, (sd, by, annual_h) in enumerate(spec):
            p = Person(tenant_id=t.id, person_code=f"L{i}", first_name="L", last_name=f"P{i}", birth_year=by)
            db.add(p)
            db.flush()
            e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=a.id, start_date=sd)
            db.add(e)
            db.flush()
            emps.append(e)
            if annual_h:
                days = annual_h // 9
                db.add(LeaveRecord(tenant_id=t.id, employment_id=e.id, leave_type="annual", start_date=D(2025, 7, 1),
                                   end_date=D(2025, 7, days), hours=annual_h))
            for m in (1, 2):
                db.add(Timesheet(tenant_id=t.id, employment_id=e.id, work_date=D(2025, m, 6), planned_hours=100,
                                 worked_hours=100, overtime_hours=0))
        add = lambda i, lt, h, m=1, d=8: db.add(LeaveRecord(  # noqa: E731
            tenant_id=t.id, employment_id=emps[i].id, leave_type=lt, start_date=D(2025, m, d), end_date=D(2025, m, d),
            hours=h))
        add(0, "sick", 9)
        add(1, "unexcused", 9)
        add(2, "annual", 9, d=9)
        for i in range(5):
            add(i, "parental", 10, d=10)
        for i in range(4):
            add(i, "parental", 10, m=2, d=10)
        def person(code, by, spells):
            p = Person(tenant_id=t.id, person_code=code, first_name="L", last_name=code, birth_year=by)
            db.add(p)
            db.flush()
            out = []
            for sd, ed in spells:
                e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=a.id, start_date=sd,
                                           end_date=ed, termination_category="other" if ed else None)
                db.add(e)
                db.flush()
                out.append(e)
            return out

        person("P7", 1990, [(D(2019, 1, 1), D(2023, 12, 31)), (D(2024, 6, 1), None)])
        e8 = person("P8", 1990, [(D(2024, 9, 1), None)])[0]
        db.add(LeaveRecord(tenant_id=t.id, employment_id=e8.id, leave_type="annual", start_date=D(2025, 10, 6),
                           end_date=D(2025, 10, 7), hours=18))
        e9 = person("P9", 1990, [(D(2015, 6, 1), None)])[0]
        db.add(LeaveRecord(tenant_id=t.id, employment_id=e9.id, leave_type="sick", start_date=D(2025, 2, 1),
                           end_date=D(2025, 8, 31), hours=1350))
        declare_all_feeds(db, t.id)  # ADR-020: every feed complete
        # ADR-024: the leave-days KPI-055/056 version needs known work patterns; 5 x 9 h converts exactly like the
        # former /9 rule, so every value below is unchanged (WP-4 E2)
        assign_default_pattern(db, t.id)
        db.commit()
        yield t.id


def ev(db, tid, code, ps, pe):
    ctx = AccessContext(user_id=None, tenant_id=tid, email="t", locale="en", grants={"kpi.read": None})
    return engine.evaluate(db, ctx, code, ps, pe)


JAN = (D(2025, 1, 1), D(2025, 1, 31))
YEAR = (D(2025, 1, 1), D(2025, 12, 31))


def test_unplanned_absence_and_company_absenteeism_exclude_parental(db, leavex):
    assert ev(db, leavex, "KPI-053", *JAN)["value"] == pytest.approx(3.0)
    r = ev(db, leavex, "KPI-003", *JAN)
    assert r["value"] == pytest.approx(4.5)
    assert "parental" in r["breakdown"]["excluded_hours_by_type"]
    assert ev(db, leavex, "KPI-028", *JAN)["value"] == pytest.approx(1.5)


def test_protected_leave_share_and_small_group_suppression(db, leavex):
    r = ev(db, leavex, "KPI-054", *JAN)
    assert r["status"] == "ok" and r["value"] == pytest.approx(8.3333, abs=1e-3) and r["threshold_status"] is None
    feb = ev(db, leavex, "KPI-054", D(2025, 2, 1), D(2025, 2, 28))
    assert feb["status"] == "suppressed" and feb["value"] is None and feb["numerator"] is None


def test_annual_leave_usage_and_nonuse(db, leavex):
    u = ev(db, leavex, "KPI-055", *YEAR)
    assert u["value"] == pytest.approx(24.5102, abs=1e-3) and u["sample_size"] == 8
    assert "4857 md.53" in u["lineage"]["entitlement_rule"]
    n = ev(db, leavex, "KPI-056", *YEAR)
    assert n["value"] == pytest.approx(33.3333, abs=1e-3) and n["sample_size"] == 6 and n["note"] is None
    assert n["lineage"]["excluded_long_absence"] == 1
    # thresholds (HR Director annex): 24.5 % usage <= critical 60 ; 33 % non-use >= critical 15
    assert u["threshold_status"] == "critical" and n["threshold_status"] == "critical"


def test_no_traffic_light_below_twelve_months(db, leavex):
    r = ev(db, leavex, "KPI-056", D(2025, 7, 1), D(2025, 12, 31))
    assert r["threshold_status"] is None and "12-month" in r["note"]


def test_approved_thresholds_applied(db, leavex):
    r = ev(db, leavex, "KPI-053", *JAN)
    assert r["thresholds"] == {"target": 2.5, "warning": 3.5, "critical": 5.0}
    assert r["threshold_status"] == "good"  # 3.0 < warning 3.5
    assert ev(db, leavex, "KPI-003", *JAN)["thresholds"]["critical"] == 11.0


@pytest.mark.parametrize("label,code,expected", [
    ("Analık raporu", None, "parental"), ("Doğum sonrası ücretsiz izin", None, "parental"),
    ("Süt izni", None, "parental"), ("Babalık izni", None, "parental"), ("Maternity leave", None, "parental"),
    ("Отпуск по беременности и родам", None, "parental"), ("İstirahat raporu", "4", "parental"),
    ("İstirahat raporu", "3", "sick"), ("Hastalık raporu", None, "sick"), ("Yıllık izin", None, "annual"),
    ("Ücretsiz izin", None, "unpaid"), ("Habersiz devamsızlık", None, "unexcused"),
    ("Resmi tatil", None, "public_holiday"), ("Evlilik izni", None, "other"), (None, None, "other"),
    # Turkish-safe folding (Agent 18 finding): upper case and dotted/dotless i must not change the mapping.
    # The next 5 rows deliberately changed from 'sick' to 'work_injury' with WP-1 / ADR-019 (HR Director review
    # condition 2): they were a temporary expectation until the separate work-injury leave type existed.
    ("İŞ KAZASI", None, "work_injury"), ("İş kazası istirahati", None, "work_injury"),
    ("Meslek hastalığı", None, "work_injury"), ("Work injury", None, "work_injury"),
    ("Производственная травма", None, "work_injury"), ("ANALIK RAPORU", None, "parental"),
    ("DOĞUM İZNİ", None, "parental"), ("YILLIK İZİN", None, "annual"), ("SÜT İZNİ", None, "parental"),
    ("ÜCRETSİZ İZİN", None, "unpaid"), ("HABERSİZ DEVAMSIZLIK", None, "unexcused"),
])
def test_leave_type_mapping_protects_parental(label, code, expected):
    assert normalize_leave_type(label, code) == expected
