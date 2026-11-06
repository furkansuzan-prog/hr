"""Work patterns, leave-day conversion, KPI-055/056 leave-days version and the planned-capacity absence denominator
(wave-2 items 15-17, ADR-024). Hand calculations: docs/hr/data/WP-4_work_pattern_leave_conversion.md section 4 +
WP-4_examples.csv, with the HR Director review (conditions 1-4, S-1..S-5, D) overriding the spec text.
Every group has 5 identical persons (HRSWI_MIN_GROUP_SIZE) so a "one-person example" is the group value.

WPAT tenant. Patterns TR-5x9 (Mon-Fri, 9 h) and TR-6x7.5 (Mon-Sat, 7.5 h). Test holidays: 2026-10-28 (eve, 0.5),
2026-10-29 (1.0). Window W12 = 2025-11-01 .. 2026-10-31 (365 days, proration 1). Persons start 2023-02-01
(3.75 y -> 14 days) unless stated; employment coverage from 2023-01-01 (E3b: from 2023-06-01).
  E1  6-day, 105 h annual (no days)        v1 105/9 = 11.67/14 = 83.33 %   new 105/7.5 = 14/14 = 100 %
  E2  5-day, 126 h                          v1 = new = 14/14 = 100 %
  E3a 6-day, spells 2019-03-01..2022-02-28 + 2023-06-01.. (1096 + 1249 = 2345 d -> 20 d), 150 h
                                            v1 16.67/20 = 83.33 %          new 20/20 = 100 %
  E3b only 2023-06-01.. loaded, coverage starts 2023-06 -> 1249 d -> 14 d; new 20/14 = 142.86 %, truncated -> q 0
  E3c only 2023-06-01.. + leave_seniority_date 2020-05-31 -> 2345 d -> 20 d -> 100 % (seniority_mismatch 5)
  E4a TIS +4 (approved, collective agreement) -> 18 d; 135 h = 18 d -> 100 %
  E4b same, 105 h = 14 d -> 14/18 = 77.78 % -> 'good' (warning 75, higher_better)
  E4t tunnel project, +4 rule NOT approved (AL-02): entitlement 14, 105 h -> 100 % + note "AL-02"
  E4l approved rule below the statutory floor (10 d) -> entitlement stays 14
  E5  pattern 5x9 until 2026-05-01, then 6x7.5: March Mon-Fri (5 d, 45 h) + August Mon-Sat (6 d, 45 h)
                                            v1 90/9 = 10 d -> 10/14 = 71.43 %; new 11 d -> 78.57 %
  E6  leave 2026-10-26 (Mon) .. 2026-11-07 (Sat), 29 Oct holiday + 28 Oct eve: 6-day 10.5 d / 78.75 h;
      5-day 8.5 d / 76.5 h
  E7  10 eligible, 3 without pattern -> coverage 0.70 < 0.80 -> insufficient_data, quality 70;
      1 without pattern -> 0.90 -> value, quality 90
  E8  8 eligible, no leave: leave feed complete -> 0 % / 100 % (real zero); leave feed undeclared -> no colour (warn)
      / insufficient_data (enforce) - ADR-020 gate decides the mode
  M3a spells 2024-01-01..2024-12-31 and 2024-07-01..2025-06-30: sum 731 d, union 547 d
Absence denominator, February 2026 (24 Mon-Sat working days), 6-day pattern, 7.5 h:
  E9  10 persons; P0 annual 6 d (45 h), P1 sick 2 d (15 h); scenario B (no rows on leave days): planned 1,740
      v1 KPI-003 60/1,740 = 3.4483 %, KPI-053 15/1,740 = 0.8621 %; new 60/1,800 = 3.3333 %, 15/1,800 = 0.8333 %
      scenario A (rows 7.5 planned, 0 worked): 1,800 -> 3.3333 % both; condition 2 mixed (rows on 3 of P0's 6 days):
      planned 1,762.5 + added back 22.5 (P0's 3 days without a row) + 15 (P1) = 1,800 -> 3.3333 %;
      declared 'included' -> nothing added back (3.4483 %)
  L6 / L5 KPI-056 long absence: > window working days / 2 of the person's pattern - 6-day 365 x 6/7 / 2 = 156.43,
      5-day 365 x 5/7 / 2 = 130.36. 150 sick days (source days): L6 (6-day, 5 persons) not long absent -> in the
      population, no annual leave -> 1 / 5 = 20 %; L5 (5-day, 6 persons) long absent -> excluded -> 0 / 5 = 0 %
  E10 50 persons, 5 on parental all month (5 x 180 = 900 h, no rows): v1 KPI-054 900/8,100 = 11.11 %, new 10.0 %
  E11 weekly period rows (period_days 7), annual 2 d: today's denominator 18/900 = 2.0 %, quality 50 + note
  E12 import, 6-day pattern, sick given as 12 days only -> 12 x 7.5 = 90 h (v1 108 h)
"""
from datetime import date

import pytest
from sqlalchemy import delete, select

from app.analytics import calculators, engine
from app.analytics.calculators import Scope, _union_days
from app.analytics.engine import KpiError, validate_parameters
from app.analytics.patterns import Holidays, PatternResolver, working_days
from app.models import (
    Assignment,
    DataQualityIssue,
    EmploymentRelationship,
    KpiDefinition,
    KpiFormulaVersion,
    KpiResult,
    LeaveEntitlementRule,
    LeaveRecord,
    LegalEntity,
    Person,
    Project,
    PublicHoliday,
    Tenant,
    Timesheet,
    WorkPattern,
    WorkPatternAssignment,
)
from app.security.access import AccessContext
from app.seed.reference import sync_kpi_catalog
from app.services import imports
from app.services.coverage import declare_complete
from app.services.work_patterns import PatternError, assign_pattern, define_pattern, load_public_holidays

D = date
W12 = (D(2025, 11, 1), D(2026, 10, 31))
FEB = (D(2026, 2, 1), D(2026, 2, 28))
ALL_FEEDS = ("employment", "leave", "timesheet", "training", "cost", "safety", "output", "downtime")
TEST_HOLIDAYS = [(D(2026, 10, 28), 0.5, "Cumhuriyet Bayramı arifesi"), (D(2026, 10, 29), 1.0, "Cumhuriyet Bayramı")]
V1_LEAVE = {"hours_per_day": 9, "threshold_min_days": 360}


def _feb_workdays():
    return [D(2026, 2, d) for d in range(1, 29) if D(2026, 2, d).isoweekday() <= 6]


@pytest.fixture(scope="module")
def wp(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        t = Tenant(code="WPAT", name="Work pattern test tenant")
        db.add(t)
        db.flush()
        tid = t.id
        p59 = define_pattern(db, tid, "TR-5x9", [1, 2, 3, 4, 5], 9)
        p675 = define_pattern(db, tid, "TR-6x7.5", "Pzt-Cmt", 7.5)
        load_public_holidays(db, "TR", TEST_HOLIDAYS, source="test calendar")
        ents: dict[str, int] = {}
        emps: dict[str, list[EmploymentRelationship]] = {}

        def entity(code, pattern=None, cover_from=D(2023, 1, 1), included=True, feeds=ALL_FEEDS):
            le = LegalEntity(tenant_id=tid, code=code, name=code, entity_type="employer")
            db.add(le)
            db.flush()
            if pattern is not None:
                assign_pattern(db, tid, pattern, "legal_entity", le.id, D(2000, 1, 1), invalidate=False)
            declare_complete(db, tid, [le.id], feeds, cover_from, D(2026, 12, 31), note="test", invalidate=False,
                             planned_on_leave_days="included" if included else None)
            ents[code] = le.id
            emps[code] = []
            return le

        def person(code, i, spells=((D(2023, 2, 1), None),), birth=1990, lsd=None, ta=None):
            p = Person(tenant_id=tid, person_code=f"{code}-{i}", first_name="W", last_name=f"{code}{i}",
                       birth_year=birth)
            db.add(p)
            db.flush()
            e = None
            for sd, ed in spells:
                e = EmploymentRelationship(tenant_id=tid, person_id=p.id, legal_entity_id=ents[code],
                                           employee_number=f"{code}-{i}", start_date=sd, end_date=ed,
                                           termination_category="other" if ed else None, leave_seniority_date=lsd,
                                           collective_agreement_code=ta, collective_agreement_known=ta is not None)
                db.add(e)
                db.flush()
            emps[code].append(e)
            return e

        def leave(e, lt, sd, ed, h, days=None):
            db.add(LeaveRecord(tenant_id=tid, employment_id=e.id, leave_type=lt, start_date=sd, end_date=ed, hours=h,
                               days=days))

        def group(code, pattern, hours, n=5, **kw):
            entity(code, pattern, **{k: kw.pop(k) for k in ("cover_from",) if k in kw})
            for i in range(n):
                e = person(code, i, **kw)
                if hours:
                    leave(e, "annual", D(2026, 6, 1), D(2026, 6, 20), hours)

        group("E1", p675, 105)
        group("E2", p59, 126)
        group("E3a", p675, 150, spells=((D(2019, 3, 1), D(2022, 2, 28)), (D(2023, 6, 1), None)))
        group("E3b", p675, 150, spells=((D(2023, 6, 1), None),), cover_from=D(2023, 6, 1))
        group("E3c", p675, 150, spells=((D(2023, 6, 1), None),), lsd=D(2020, 5, 31))
        group("E4a", p675, 135, ta="TIS-1")
        group("E4b", p675, 105, ta="TIS-1")
        group("E4t", p675, 105)
        group("E4l", p675, 105)
        db.add(LeaveEntitlementRule(tenant_id=tid, scope_level="collective_agreement", collective_agreement_code="TIS-1",
                                    rule_type="extra_days", value={"days": 4}, valid_from=D(2020, 1, 1),
                                    source_document="TIS 2025-2027 md. 21", approved_by="HR director",
                                    approved_at=D(2025, 1, 1)))
        tunnel = Project(tenant_id=tid, code="TUNNEL", name="Tunnel", owner_entity_id=ents["E4t"])
        db.add(tunnel)
        db.flush()
        for e in emps["E4t"]:
            db.add(Assignment(tenant_id=tid, employment_id=e.id, project_id=tunnel.id, valid_from=D(2023, 2, 1),
                              is_primary=True))
        db.add(LeaveEntitlementRule(tenant_id=tid, scope_level="project", project_id=tunnel.id, rule_type="extra_days",
                                    value={"days": 4}, valid_from=D(2020, 1, 1), pending_reason="AL-02",
                                    source_document="4857 md.53 underground work (legal confirmation pending)"))
        db.add(LeaveEntitlementRule(tenant_id=tid, scope_level="legal_entity", legal_entity_id=ents["E4l"],
                                    rule_type="band_table", value={"bands": [{"min_years": 0, "days": 10}]},
                                    valid_from=D(2020, 1, 1), source_document="contract template 2020",
                                    approved_by="HR", approved_at=D(2025, 1, 1)))
        # E5: employment-level pattern change on 2026-05-01 (no entity pattern); leave arrives through the import
        entity("E5")
        for i in range(5):
            e = person("E5", i)
            assign_pattern(db, tid, p59, "employment", e.id, D(2023, 2, 1), D(2026, 5, 1), invalidate=False)
            assign_pattern(db, tid, p675, "employment", e.id, D(2026, 5, 1), invalidate=False)
        # E6: one 6-day and one 5-day person (import only)
        entity("E6S", p675)
        person("E6S", 0)
        entity("E6F", p59)
        person("E6F", 0)
        # E7: 10 eligible, 7 resp. 9 with an employment-level pattern
        for code, known in (("E7", 7), ("E7b", 9)):
            entity(code)
            for i in range(10):
                e = person(code, i)
                if i < known:
                    assign_pattern(db, tid, p59, "employment", e.id, D(2020, 1, 1), invalidate=False)
        # L6 / L5: KPI-056 long-absence threshold by pattern
        for code, pat, n, hpd in (("L6", p675, 5, 7.5), ("L5", p59, 6, 9)):
            entity(code, pat)
            for i in range(n):
                e = person(code, i)
                if i == 0:
                    leave(e, "sick", D(2025, 12, 1), D(2026, 5, 31), 150 * hpd, days=150)
                else:
                    leave(e, "annual", D(2026, 6, 1), D(2026, 6, 1), hpd)
        # E8: no leave at all; E8b without a leave-feed declaration
        group("E8a", p675, 0, n=8)
        entity("E8b", p675, feeds=tuple(f for f in ALL_FEEDS if f != "leave"))
        for i in range(8):
            person("E8b", i)
        # absence denominator (February 2026)
        def absence(code, *, scenario, n=10, included=False):
            entity(code, p675, included=included)
            days = _feb_workdays()
            for i in range(n):
                e = person(code, i)
                off: set[date] = set()
                if i == 0:
                    leave(e, "annual", D(2026, 2, 2), D(2026, 2, 7), 45)
                    off = {D(2026, 2, d) for d in range(2, 8)}
                elif i == 1:
                    leave(e, "sick", D(2026, 2, 9), D(2026, 2, 10), 15)
                    off = {D(2026, 2, 9), D(2026, 2, 10)}
                for d in days:
                    if d not in off:
                        db.add(Timesheet(tenant_id=tid, employment_id=e.id, work_date=d, planned_hours=7.5,
                                         worked_hours=7.5))
                    elif scenario == "A" or (scenario == "mixed" and i == 0 and d.day <= 4):
                        db.add(Timesheet(tenant_id=tid, employment_id=e.id, work_date=d, planned_hours=7.5,
                                         worked_hours=0, day_type="leave"))

        absence("A9", scenario="B")
        absence("A9A", scenario="A")
        absence("A9M", scenario="mixed")
        absence("A9I", scenario="B", included=True)
        entity("A10", p675, included=False)
        for i in range(50):
            e = person("A10", i)
            if i < 5:
                leave(e, "parental", D(2026, 2, 1), D(2026, 2, 28), 180)
                continue
            for d in _feb_workdays():
                db.add(Timesheet(tenant_id=tid, employment_id=e.id, work_date=d, planned_hours=7.5, worked_hours=7.5))
        entity("A11", p59, included=False)
        for i in range(5):
            e = person("A11", i)
            for mon in (D(2026, 2, 2), D(2026, 2, 9), D(2026, 2, 16), D(2026, 2, 23)):
                db.add(Timesheet(tenant_id=tid, employment_id=e.id, work_date=mon, planned_hours=45, worked_hours=45,
                                 period_days=7))
            if i == 0:
                leave(e, "annual", D(2026, 2, 10), D(2026, 2, 11), 18)
        # imports: 6-day and 5-day employers with employee numbers
        entity("IMP", p675)
        for i in range(3):
            person("IMP", i)
        entity("IMN")  # no pattern anywhere
        person("IMN", 0)
        db.commit()
        yield {"tenant_id": tid, "e": ents, "emps": {k: [e.id for e in v] for k, v in emps.items()},
               "p59": p59.id, "p675": p675.id, "tunnel": tunnel.id}
        db.execute(delete(PublicHoliday).where(PublicHoliday.country == "TR",
                                               PublicHoliday.holiday_date.in_([h[0] for h in TEST_HOLIDAYS])))
        db.commit()


def _ctx(w, **extra):
    return AccessContext(user_id=None, tenant_id=w["tenant_id"], email="t", locale="en",
                         grants={"kpi.read": None, "import.write": None, "timekeeping.read": None, **extra})


def ev(db, w, code, window, entity, **kw):
    db.info.pop("kpi_memo", None)
    return engine.evaluate(db, _ctx(w), code, *window, entity_ids={w["e"][entity]}, **kw)


def v1(db, w, fn, window, entity, params):
    return fn(db, Scope(w["tenant_id"], frozenset({w["e"][entity]})), *window, params)


# ---------------------------------------------------------------- KPI-055 (WP-4 4.1)
def test_six_day_site_leave_hours_converted_with_7_5_hours_per_day(db, wp):
    """E1: 83.33 % (v1, 105 / 9) -> 100 % (105 / 7.5 = 14 days, hours_divided)."""
    r = ev(db, wp, "KPI-055", W12, "E1")
    assert r["value"] == pytest.approx(100.0) and r["sample_size"] == 5 and r["threshold_status"] == "good"
    assert r["lineage"]["pattern_coverage"] == 1.0 and r["lineage"]["day_basis_counts"] == {"hours_divided": 5}
    assert r["quality_score"] == 100.0 and r["formula"]["parameters"]["day_basis"] == "leave_days"
    assert v1(db, wp, calculators.annual_leave_usage, W12, "E1", V1_LEAVE).value == pytest.approx(83.3333, abs=1e-3)


def test_five_day_pattern_annual_leave_usage_unchanged_between_v1_and_v2(db, wp):
    """E2: 126 h on a 5 x 9 pattern = 14 days in both versions."""
    assert ev(db, wp, "KPI-055", W12, "E2")["value"] == pytest.approx(100.0)
    assert v1(db, wp, calculators.annual_leave_usage, W12, "E2", V1_LEAVE).value == pytest.approx(100.0)


def test_rehire_service_combined_md54_and_seniority_date_override(db, wp):
    """E3 a/b/c: md.54 spell union, missing earlier spell (truncated -> quality drops), leave seniority date."""
    a = ev(db, wp, "KPI-055", W12, "E3a")
    assert a["value"] == pytest.approx(100.0) and a["quality_score"] == 100.0
    assert v1(db, wp, calculators.annual_leave_usage, W12, "E3a", V1_LEAVE).value == pytest.approx(83.3333, abs=1e-3)
    b = ev(db, wp, "KPI-055", W12, "E3b")
    assert b["value"] == pytest.approx(142.8571, abs=1e-3)
    assert b["lineage"]["seniority_truncated"] == 5 and b["quality_score"] == 0.0
    assert v1(db, wp, calculators.annual_leave_usage, W12, "E3b", V1_LEAVE).value == pytest.approx(119.0476, abs=1e-3)
    c = ev(db, wp, "KPI-055", W12, "E3c")
    assert c["value"] == pytest.approx(100.0) and c["lineage"]["seniority_truncated"] == 0
    assert c["lineage"]["seniority_mismatch"] == 5  # 2,345 vs 1,249 days (> 30) -> HR operations queue count


def test_overlapping_spells_same_employer_counted_once():
    """M3a: 366 + 365 = 731 days summed (v1) -> union 2024-01-01..2025-06-30 = 547 days."""
    spells = [(D(2024, 1, 1), D(2024, 12, 31)), (D(2024, 7, 1), D(2025, 6, 30))]
    assert sum((e - s).days + 1 for s, e in spells) == 731
    assert _union_days(spells) == 547
    assert _union_days([(D(2024, 1, 1), D(2024, 1, 10)), (D(2024, 1, 11), D(2024, 1, 20))]) == 20  # adjacent


def test_contractual_entitlement_used_when_higher_never_below_statutory(db, wp):
    """E4: TIS +4 -> 18 days (a 100 %, b 77.78 % good); an unapproved tunnel rule changes nothing but adds a note;
    a lower contractual table never goes below the statutory 14 days (L-4)."""
    a = ev(db, wp, "KPI-055", W12, "E4a")
    assert a["value"] == pytest.approx(100.0) and a["lineage"]["contractual_rule_count"] == 5
    b = ev(db, wp, "KPI-055", W12, "E4b")
    assert b["value"] == pytest.approx(77.7778, abs=1e-3) and b["threshold_status"] == "good"
    assert v1(db, wp, calculators.annual_leave_usage, W12, "E4a", V1_LEAVE).value == pytest.approx(107.1429, abs=1e-3)
    t = ev(db, wp, "KPI-055", W12, "E4t")
    assert t["value"] == pytest.approx(100.0) and "AL-02" in t["note"] and "overstated" in t["note"]
    assert t["lineage"]["pending_entitlement_rules"] == 5 and t["lineage"]["contractual_rule_count"] == 0
    low = ev(db, wp, "KPI-055", W12, "E4l")
    assert low["value"] == pytest.approx(100.0) and low["lineage"]["contractual_rule_count"] == 0


def test_pattern_change_mid_period_converts_each_record_with_its_own_pattern(db, wp):
    """E5: each row converts with the pattern valid at its start: 5 (5 x 9) + 6 (6 x 7.5) = 11 days, 90 h."""
    rows = []
    for i in range(5):
        rows += [f"E5-{i};E5;Yıllık izin;2026-03-02;2026-03-06", f"E5-{i};E5;Yıllık izin;2026-08-03;2026-08-08"]
    v, _issues, _ = _import(db, wp, "Sicil;Firma;İzin Türü;Başlangıç;Bitiş", rows, "e5.csv")
    assert v["valid_rows"] == 10
    recs = db.execute(select(LeaveRecord).where(LeaveRecord.employment_id == wp["emps"]["E5"][0])
                      .order_by(LeaveRecord.start_date)).scalars().all()
    assert [(float(r.days), float(r.hours), r.day_basis) for r in recs] == [(5.0, 45.0, "pattern_counted"),
                                                                           (6.0, 45.0, "pattern_counted")]
    assert [r.work_pattern_id for r in recs] == [wp["p59"], wp["p675"]]
    r = ev(db, wp, "KPI-055", W12, "E5")
    assert r["value"] == pytest.approx(11 / 14 * 100, abs=1e-3)
    assert v1(db, wp, calculators.annual_leave_usage, W12, "E5", V1_LEAVE).value == pytest.approx(10 / 14 * 100,
                                                                                                  abs=1e-3)


def test_public_holiday_and_eve_half_day_not_counted_as_leave_day(db, wp):
    """E6: 26.10.2026 - 07.11.2026: 6-day 13 - 1 Sunday - 1 - 0.5 = 10.5 d (78.75 h); 5-day 10 - 1.5 = 8.5 d (76.5 h)."""
    h = Holidays(db)
    p6 = PatternResolver(db, wp["tenant_id"]).by_id(wp["p675"])
    assert working_days(p6, h, D(2026, 10, 26), D(2026, 11, 7)) == 10.5
    v, _issues, _ = _import(db, wp, "Sicil;Firma;İzin Türü;Başlangıç;Bitiş",
                           ["E6S-0;E6S;Yıllık izin;2026-10-26;2026-11-07", "E6F-0;E6F;Yıllık izin;2026-10-26;2026-11-07"],
                           "e6.csv")
    assert v["valid_rows"] == 2
    got = {r.employment_id: (float(r.days), float(r.hours)) for r in db.execute(select(LeaveRecord).where(
        LeaveRecord.employment_id.in_([wp["emps"]["E6S"][0], wp["emps"]["E6F"][0]]))).scalars()}
    assert got == {wp["emps"]["E6S"][0]: (10.5, 78.75), wp["emps"]["E6F"][0]: (8.5, 76.5)}
    # a year without a calendar: holidays not deducted + warning (WP-4 3.2 step 6)
    _, issues2, _ = _import(db, wp, "Sicil;Firma;İzin Türü;Başlangıç;Bitiş",
                            ["E6S-0;E6S;Yıllık izin;2025-12-01;2025-12-02"], "e6b.csv")
    assert "holiday_calendar_missing" in {i.rule_code for i in issues2}


def test_pattern_coverage_below_min_returns_insufficient_data(db, wp):
    """E7: 7/10 = 0.70 < 0.80 (min_pattern_coverage, review condition 1) -> insufficient_data, quality 70;
    9/10 -> value (the unknown person uses 9 h per day), quality 90."""
    r = ev(db, wp, "KPI-055", W12, "E7")
    assert r["status"] == "insufficient_data" and r["value"] is None and r["quality_score"] == 70.0
    assert "3 of 10" in r["note"] and r["status_reason"] is None
    n = ev(db, wp, "KPI-056", W12, "E7")
    assert n["status"] == "insufficient_data" and n["quality_score"] == 70.0
    ok = ev(db, wp, "KPI-055", W12, "E7b")
    assert ok["status"] == "ok" and ok["quality_score"] == 90.0 and "1 person(s)" in ok["note"]
    assert ok["formula"]["parameters"]["min_pattern_coverage"] == 0.8


def test_annual_leave_true_zero_only_with_complete_leave_feed(db, wp, monkeypatch):
    """E8: complete leave feed -> real zero (0 % / 100 %, coloured red); no leave declaration -> never a coloured zero:
    warn (PO-6 open) shows it without colour, enforce returns insufficient_data (ADR-020 gate)."""
    from app.core.config import get_settings

    u, n = ev(db, wp, "KPI-055", W12, "E8a"), ev(db, wp, "KPI-056", W12, "E8a")
    assert (u["value"], n["value"]) == (0.0, 100.0)
    assert u["threshold_status"] == "critical" and n["threshold_status"] == "critical"
    ub = ev(db, wp, "KPI-055", W12, "E8b")
    assert ub["value"] == 0.0 and ub["threshold_status"] is None and ub["status_reason"] == "low_coverage"
    monkeypatch.setattr(get_settings(), "coverage_modes", "default=warn,leave=enforce")
    assert ev(db, wp, "KPI-056", W12, "E8b")["status"] == "insufficient_data"


def test_kpi056_long_absence_threshold_uses_pattern_working_days(db, wp):
    """WP-4 3.4: long absence = days of the long-absence types > the pattern's window working days / 2 (6-day x 6/7,
    5-day x 5/7). 150 sick days: L6 (6-day) not long absent -> 1 / 5 = 20 %; L5 (5-day) excluded -> 0 / 5 = 0 %."""
    l6 = ev(db, wp, "KPI-056", W12, "L6")
    assert l6["value"] == pytest.approx(20.0) and l6["lineage"]["excluded_long_absence"] == 0
    assert l6["lineage"]["day_basis_counts"] == {"hours_divided": 4, "source_days": 1}
    l5 = ev(db, wp, "KPI-056", W12, "L5")
    assert l5["value"] == 0.0 and l5["sample_size"] == 5 and l5["lineage"]["excluded_long_absence"] == 1


# ---------------------------------------------------------------- versioning (acceptance 8)
@pytest.mark.parametrize("code,entity,window,old_params,old_value,new_value", [
    ("KPI-055", "E1", W12, {"hours_per_day": 9, "threshold_min_days": 360}, 83.3333, 100.0),
    ("KPI-003", "A9", FEB, None, 3.4483, 3.3333),
])
def test_kpi055_v1_results_preserved_after_v2(db, wp, code, entity, window, old_params, old_value, new_value):
    """A database whose current formula is the previous version: its persisted result keeps version and value; the
    catalog sync adds the ADR-024 parameters as a NEW version (INTEGRATION_CONTRACTS 9)."""
    d = db.execute(select(KpiDefinition).where(KpiDefinition.code == code)).scalar_one()
    cur = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == d.id,
                                                     KpiFormulaVersion.is_current.is_(True))).scalar_one()
    params = dict(cur.parameters)
    for k in ("day_basis", "min_pattern_coverage", "denominator_basis"):
        params.pop(k, None)
    if old_params:
        params.update(old_params)
    cur.is_current = False
    old = KpiFormulaVersion(kpi_id=d.id, version=cur.version + 1, calculator_key=cur.calculator_key,
                            formula_text=cur.formula_text, numerator_def=cur.numerator_def,
                            denominator_def=cur.denominator_def, is_current=True, change_note="pre-ADR-024 (simulated)",
                            parameters=params)
    db.add(old)
    db.commit()
    db.info.clear()
    run = engine.run_calculation(db, _ctx(wp, **{"cost.read": None}), [code], *window, entity_ids={wp["e"][entity]})
    row = db.execute(select(KpiResult).where(KpiResult.run_id == run.id)).scalar_one()
    assert (float(row.value), row.formula_version_id) == (pytest.approx(old_value, abs=1e-3), old.id)
    assert code in [c["code"] for c in sync_kpi_catalog(db) if c["new_formula_version"]]
    sync_kpi_catalog(db, apply=True)
    db.info.clear()
    now = ev(db, wp, code, window, entity)
    assert now["value"] == pytest.approx(new_value, abs=1e-3) and now["formula"]["version"] == old.version + 1
    assert ev(db, wp, code, window, entity, version=old.version)["value"] == pytest.approx(old_value, abs=1e-3)
    db.expire_all()
    kept = db.get(KpiResult, row.id)
    assert (float(kept.value), kept.formula_version_id) == (pytest.approx(old_value, abs=1e-3), old.id)
    assert sync_kpi_catalog(db) == []


# ---------------------------------------------------------------- absence denominator (WP-4 4.2, condition 2)
def test_absence_denominator_adds_leave_hours_when_source_excludes_leave_days(db, wp):
    """E9 scenario B: 3.45 % / 0.86 % today -> 3.33 % / 0.83 % (= scenario A); E10: KPI-054 11.11 % -> 10.0 %."""
    k3, k53 = ev(db, wp, "KPI-003", FEB, "A9"), ev(db, wp, "KPI-053", FEB, "A9")
    assert k3["value"] == pytest.approx(3.3333, abs=1e-3) and k53["value"] == pytest.approx(0.8333, abs=1e-3)
    assert k3["denominator"] == pytest.approx(1800.0) and k3["quality_score"] == 100.0
    assert k3["breakdown"]["denominator"] == {"basis": "planned_capacity", "planned_hours": 1740.0,
                                              "leave_addback_hours": 60.0}
    assert k3["lineage"]["leave_addback_days"] == 8
    old = {"absence_types": k3["formula"]["parameters"]["absence_types"]}
    assert v1(db, wp, calculators.absenteeism_rate, FEB, "A9", old).value == pytest.approx(3.4483, abs=1e-3)
    assert v1(db, wp, calculators.absenteeism_rate, FEB, "A9", {"absence_types": ["unexcused", "sick"]}).value \
        == pytest.approx(0.8621, abs=1e-3)
    a = ev(db, wp, "KPI-003", FEB, "A9A")
    assert a["value"] == pytest.approx(3.3333, abs=1e-3) and a["breakdown"]["denominator"]["leave_addback_hours"] == 0
    k54 = ev(db, wp, "KPI-054", FEB, "A10")
    assert k54["value"] == pytest.approx(10.0) and k54["denominator"] == pytest.approx(9000.0)
    assert v1(db, wp, calculators.protected_leave_share, FEB, "A10", {"absence_types": ["parental"]}).value \
        == pytest.approx(11.1111, abs=1e-3)
    k3_10 = ev(db, wp, "KPI-003", FEB, "A10")  # D-6: parental enlarges the denominator only, never the numerator
    assert k3_10["value"] == 0.0 and "parental" not in k3_10["breakdown"]["included_hours_by_type"]


def test_absence_denominator_addback_only_for_days_without_planned_row(db, wp):
    """Review condition 2: rows with planned hours on 3 of P0's 6 leave days -> only the other 3 days (22.5 h) and P1's
    2 days (15 h) are added back: 1,762.5 + 37.5 = 1,800 -> 3.33 %. A cell declared 'included' adds nothing back."""
    m = ev(db, wp, "KPI-003", FEB, "A9M")
    assert m["value"] == pytest.approx(3.3333, abs=1e-3)
    assert m["breakdown"]["denominator"] == {"basis": "planned_capacity", "planned_hours": 1762.5,
                                             "leave_addback_hours": 37.5}
    assert m["lineage"]["leave_addback_days"] == 5
    inc = ev(db, wp, "KPI-003", FEB, "A9I")
    assert inc["value"] == pytest.approx(3.4483, abs=1e-3) and inc["breakdown"]["denominator"]["leave_addback_hours"] == 0


def test_absence_denominator_unknown_profile_halves_quality_score(db, wp):
    """E11: weekly period rows (no daily granularity) -> today's denominator 18 / 900 = 2.0 %, quality 50 + note."""
    r = ev(db, wp, "KPI-003", FEB, "A11")
    assert r["value"] == pytest.approx(2.0) and r["quality_score"] == 50.0
    assert "could not be verified" in r["note"] and r["lineage"]["unverified_leave_days"] == 2
    assert r["breakdown"]["denominator"]["leave_addback_hours"] == 0


# ---------------------------------------------------------------- leave import (WP-4 3.2)
LEAVE_HDR = "Sicil;Firma;İzin Türü;Başlangıç;Bitiş;Saat;Gün"


def _import(db, w, header, rows, name, entity_type="leave", options=None):
    ctx = _ctx(w)
    content = (header + "\n" + "\n".join(rows) + "\n").encode()
    up = imports.upload(db, ctx, entity_type=entity_type, filename=name, content=content, options=options)
    v = imports.validate(db, ctx, up["batch_id"], None)
    issues = list(db.execute(select(DataQualityIssue).where(
        DataQualityIssue.import_batch_id == up["batch_id"])).scalars())
    c = imports.commit(db, ctx, up["batch_id"]) if v["valid_rows"] else None
    db.expire_all()
    return v, issues, c


def test_leave_import_days_only_derives_hours_from_pattern(db, wp):
    """E12: 12 sick days given, 6-day pattern -> 12 x 7.5 = 90 h (v1: 12 x 9 = 108 h), day_basis source_days."""
    v, issues, _ = _import(db, wp, LEAVE_HDR, ["IMP-0;IMP;Raporlu;2026-03-02;2026-03-14;;12"], "e12.csv")
    assert v["valid_rows"] == 1
    rec = db.execute(select(LeaveRecord).where(LeaveRecord.employment_id == wp["emps"]["IMP"][0])).scalar_one()
    assert (rec.leave_type, float(rec.days), float(rec.hours), rec.day_basis, rec.work_pattern_id) == (
        "sick", 12.0, 90.0, "source_days", wp["p675"])
    assert "hours_derived_from_pattern" in {i.rule_code for i in issues}


def test_leave_import_keeps_source_days_and_flags_days_exceeding_pattern(db, wp):
    """Source hours and days are never overwritten: 7 days in a range of 6 pattern working days (Mon-Sun) -> warning;
    hours 100 vs 6 days x 7.5 = 45 -> hours_days_inconsistent; a partial day (3.75 h on one date) -> 0.5 day
    hours_divided."""
    v, issues, _ = _import(db, wp, LEAVE_HDR, [
        "IMP-1;IMP;Yıllık izin;2026-04-06;2026-04-12;;7",        # Mon-Sun = 6 pattern days < 7 (<= 7 calendar days)
        "IMP-1;IMP;Ücretsiz izin;2026-04-13;2026-04-18;100;6",   # 6 x 7.5 = 45 h vs 100 h
        "IMP-2;IMP;Yıllık izin;2026-04-20;2026-04-20;3,75;",     # half day
    ], "src.csv")
    assert v["valid_rows"] == 3
    rules = {(i.row_number, i.rule_code) for i in issues}
    assert (2, "days_exceed_pattern_working_days") in rules and (3, "hours_days_inconsistent") in rules
    recs = {(r.employment_id, str(r.start_date)): r for r in db.execute(select(LeaveRecord).where(
        LeaveRecord.employment_id.in_(wp["emps"]["IMP"][1:]))).scalars()}
    a = recs[(wp["emps"]["IMP"][1], "2026-04-06")]
    assert (float(a.days), float(a.hours), a.day_basis) == (7.0, 52.5, "source_days")
    b = recs[(wp["emps"]["IMP"][1], "2026-04-13")]
    assert (float(b.days), float(b.hours)) == (6.0, 100.0)
    c = recs[(wp["emps"]["IMP"][2], "2026-04-20")]
    assert (float(c.days), float(c.hours), c.day_basis) == (0.5, 3.75, "hours_divided")


def test_leave_import_without_pattern_keeps_default_9h(db, wp):
    """WP-4 3.2 step 5: no pattern anywhere -> Mon-Fri x 9 (unchanged), day_basis default_9h, warning."""
    _v, issues, _ = _import(db, wp, LEAVE_HDR, ["IMN-0;IMN;Yıllık izin;2026-03-02;2026-03-03;;"], "imn.csv")
    rec = db.execute(select(LeaveRecord).where(LeaveRecord.employment_id == wp["emps"]["IMN"][0])).scalar_one()
    assert (float(rec.days), float(rec.hours), rec.day_basis, rec.work_pattern_id) == (2.0, 18.0, "default_9h", None)
    assert "pattern_unknown_default_9h" in {i.rule_code for i in issues}


def test_protected_leave_still_parental_after_pattern_conversion(db, wp):
    """D-6 regression: the protected-leave label still maps to parental first; the conversion is type independent."""
    _v, _issues, _ = _import(db, wp, LEAVE_HDR, ["IMP-2;IMP;Analık izni;2026-05-04;2026-05-09;;"], "d6.csv")
    rec = db.execute(select(LeaveRecord).where(LeaveRecord.employment_id == wp["emps"]["IMP"][2],
                                               LeaveRecord.start_date == D(2026, 5, 4))).scalar_one()
    assert (rec.leave_type, float(rec.days), float(rec.hours)) == ("parental", 6.0, 45.0)


# ---------------------------------------------------------------- pattern resolution and definitions (WP-4 3.1)
def test_pattern_resolution_order_employment_project_entity_unknown(db, wp):
    tid = wp["tenant_id"]
    le = LegalEntity(tenant_id=tid, code="RES", name="RES", entity_type="employer")
    db.add(le)
    db.flush()
    prj = Project(tenant_id=tid, code="RES-P", name="RES-P", owner_entity_id=le.id)
    p = Person(tenant_id=tid, person_code="RES-0", first_name="R", last_name="R")
    db.add_all([prj, p])
    db.flush()
    e = EmploymentRelationship(tenant_id=tid, person_id=p.id, legal_entity_id=le.id, start_date=D(2024, 1, 1))
    db.add(e)
    db.flush()
    db.add(Assignment(tenant_id=tid, employment_id=e.id, project_id=prj.id, valid_from=D(2025, 1, 1),
                      valid_to=D(2025, 7, 1), is_primary=True))
    p59, p675 = db.get(WorkPattern, wp["p59"]), db.get(WorkPattern, wp["p675"])
    p58 = define_pattern(db, tid, "TR-5x8", "1-5", 8)
    r0 = PatternResolver(db, tid)
    assert r0.resolve(e.id, le.id, D(2024, 6, 1)) is None  # nothing known (work_calendars 45 h is not a pattern)
    assign_pattern(db, tid, p59, "legal_entity", le.id, D(2024, 1, 1), invalidate=False)
    assign_pattern(db, tid, p675, "project", prj.id, D(2024, 1, 1), invalidate=False)
    assign_pattern(db, tid, p58, "employment", e.id, D(2025, 3, 1), D(2025, 4, 1), invalidate=False)
    r = PatternResolver(db, tid)
    assert r.resolve(e.id, le.id, D(2024, 6, 1)).code == "TR-5x9"     # no assignment on a project yet -> entity
    assert r.resolve(e.id, le.id, D(2025, 2, 1)).code == "TR-6x7.5"   # primary assignment's project
    assert r.resolve(e.id, le.id, D(2025, 3, 15)).code == "TR-5x8"    # employment wins
    assert r.resolve(e.id, le.id, D(2025, 4, 1)).code == "TR-6x7.5"   # valid_to is exclusive
    assert r.resolve(e.id, le.id, D(2025, 8, 1)).code == "TR-5x9"     # project assignment ended
    with pytest.raises(PatternError) as ex:
        assign_pattern(db, tid, p59, "employment", e.id, D(2025, 3, 15), invalidate=False)
    assert ex.value.rule == "pattern_assignment_overlap"
    with pytest.raises(PatternError) as ex:
        define_pattern(db, tid, "TR-5x9", "1-6", 9)
    assert ex.value.rule == "pattern_definition_changed"
    db.rollback()


def test_work_pattern_import_definitions_and_assignments(db, wp):
    hdr = "Düzen Kodu;Çalışma Günleri;Günlük Saat;Haftalık Saat;Düzey;Sicil;Firma;Proje;Geçerlilik Başlangıç"
    v, issues, c = _import(db, wp, hdr, [
        "SITE-6x7.5;Pzt-Cmt;7,5;45;işveren;;E8a;;2027-01-01",   # 2: closes nothing - overlaps the open entity row
        "OFF-5x8;1-5;8;40;;;;;",                                # 3: definition only
        "LONG;1-5;12;;;;;;",                                    # 4: daily > 11
        "BADW;1-5;9;50;;;;;",                                   # 5: weekly != 5 x 9
        "SIX8;1-6;8;48;kişi;IMP-0;IMP;;2027-01-01",             # 6: valid, > 45 h warning, employment level
        "NOPE;;;;proje;;;TUNNEL;2027-01-01",                     # 7: unknown pattern
        "TR-5x9;1-6;9;;;;;;",                                   # 8: changed definition
        "OFF-5x8;;;;proje;;;TUNNEL;2027-01-01",                  # 9: assignment of a pattern defined in this file
    ], "patterns.csv", entity_type="work_pattern")
    rules = {(i.row_number, i.rule_code) for i in issues}
    assert {(2, "pattern_assignment_overlap"), (4, "daily_hours_above_11"), (5, "weekly_hours_inconsistent"),
            (6, "weekly_hours_above_45"), (7, "unknown_pattern"), (8, "pattern_definition_changed")} <= rules
    assert v["valid_rows"] == 3 and c["status"] == "committed"
    six8 = db.execute(select(WorkPattern).where(WorkPattern.tenant_id == wp["tenant_id"], WorkPattern.code == "SIX8")
                      ).scalar_one()
    assert (six8.working_weekdays, float(six8.weekly_hours)) == ([1, 2, 3, 4, 5, 6], 48.0)
    a = db.execute(select(WorkPatternAssignment).where(WorkPatternAssignment.work_pattern_id == six8.id)).scalar_one()
    assert (a.level, a.employment_id, a.valid_from) == ("employment", wp["emps"]["IMP"][0], D(2027, 1, 1))
    off = db.execute(select(WorkPattern).where(WorkPattern.tenant_id == wp["tenant_id"],
                                               WorkPattern.code == "OFF-5x8")).scalar_one()
    pa = db.execute(select(WorkPatternAssignment).where(WorkPatternAssignment.work_pattern_id == off.id)).scalar_one()
    assert (pa.level, pa.project_id) == ("project", wp["tunnel"])


def test_new_pattern_parameters_validated_per_calculator():
    """Review condition 1: min_pattern_coverage is its own key; KPI-004's min_coverage is rejected on KPI-055."""
    validate_parameters({"day_basis": "leave_days", "min_pattern_coverage": 0.8, "hours_per_day": 9},
                        "annual_leave_usage")
    validate_parameters({"denominator_basis": "planned_capacity", "absence_types": ["parental"]},
                        "protected_leave_share")
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"min_coverage": 0.8}, "annual_leave_usage")
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"min_pattern_coverage": 0.8}, "capacity_utilization")
    with pytest.raises(KpiError, match="Invalid value"):
        validate_parameters({"denominator_basis": "excluded"}, "absenteeism_rate")
    with pytest.raises(KpiError, match="Invalid value"):
        validate_parameters({"day_basis": "hours"}, "annual_leave_nonuse")


# ---------------------------------------------------------------- timesheet import checks (ADR-022 open items)
TS_HDR = "sicil;firma;tarih;planlı saat;çalışılan saat;fazla mesai;gün tipi"


def test_timesheet_checks_use_work_pattern(db, wp):
    """Weekly overtime against the pattern's weekly hours (5 x 8 = 40: 45 worked + 5 overtime is consistent, the old
    45 h rule would flag it); planned hours on Saturdays of a 5-day pattern in every week -> pattern_timesheet_mismatch;
    6-day leave Mon-Sat counts 6 leave working days (Mon-Fri rule: 5)."""
    tid = wp["tenant_id"]
    p58 = db.execute(select(WorkPattern).where(WorkPattern.tenant_id == tid, WorkPattern.code == "TR-5x8")).scalar()
    p58 = p58 or define_pattern(db, tid, "TR-5x8", "1-5", 8)
    le = LegalEntity(tenant_id=tid, code="TSW", name="TSW", entity_type="employer")
    db.add(le)
    db.flush()
    assign_pattern(db, tid, db.get(WorkPattern, wp["p59"]), "legal_entity", le.id, D(2020, 1, 1), invalidate=False)
    es = []
    for i in range(3):
        p = Person(tenant_id=tid, person_code=f"TSW-{i}", first_name="T", last_name=str(i))
        db.add(p)
        db.flush()
        e = EmploymentRelationship(tenant_id=tid, person_id=p.id, legal_entity_id=le.id, employee_number=f"TSW-{i}",
                                   start_date=D(2024, 1, 1))
        db.add(e)
        db.flush()
        es.append(e)
    assign_pattern(db, tid, p58, "employment", es[0].id, D(2024, 1, 1), invalidate=False)
    assign_pattern(db, tid, db.get(WorkPattern, wp["p675"]), "employment", es[2].id, D(2024, 1, 1), invalidate=False)
    db.add(LeaveRecord(tenant_id=tid, employment_id=es[2].id, leave_type="annual", start_date=D(2026, 3, 2),
                       end_date=D(2026, 3, 7), hours=45))
    db.commit()
    rows = []
    for d in range(2, 16):
        day = D(2026, 3, d)
        wd = day.isoweekday()
        rows.append(f"TSW-0;TSW;{day};{8 if wd <= 5 else 0};{9 if wd <= 5 else 0};{1 if wd <= 5 else 0};"
                    f"{'work' if wd <= 5 else 'weekly_rest'}")
        rows.append(f"TSW-1;TSW;{day};{9 if wd <= 6 else 0};{9 if wd <= 6 else 0};{9 if wd == 6 else 0};"
                    f"{'work' if wd <= 6 else 'weekly_rest'}")  # Saturday = 9 h overtime above the 45 h week
        if day <= D(2026, 3, 7):
            rows.append(f"TSW-2;TSW;{day};{7.5 if wd <= 6 else 0};0;0;{'leave' if wd <= 6 else 'weekly_rest'}")
    v, issues, _ = _import(db, wp, TS_HDR, rows, "tsw.csv", entity_type="timesheet",
                           options={"approved_source": True})
    group = {i.rule_code: i.message for i in issues if i.row_number is None}
    assert "overtime_weekly_inconsistent" not in group
    assert group["pattern_timesheet_mismatch"].startswith("1 employee(s)")
    batch_opts = imports.summary(db, _ctx(wp), v["batch_id"])["options"]
    assert batch_opts["observed_planned_on_leave_days"] == {"profile": "included", "leave_workdays": 6,
                                                           "with_planned_hours": 6}


def test_employment_import_collective_agreement_and_leave_seniority_date(db, wp):
    hdr = "Sicil;Ad;Soyad;Firma;İşe Giriş;TİS;İzin Kıdem Tarihi;Sözleşme Tipi"
    v, issues, _ = _import(db, wp, hdr, ["NEW-1;A;B;IMP;2024-03-01;TIS-1;2019-01-01;permanent",
                                         "NEW-2;C;D;IMP;2024-03-01;yok;2025-01-01;permanent"], "emp.csv",
                           entity_type="employment")
    assert v["valid_rows"] == 1 and "leave_seniority_after_start" in {i.rule_code for i in issues}
    e = db.execute(select(EmploymentRelationship).where(EmploymentRelationship.tenant_id == wp["tenant_id"],
                                                        EmploymentRelationship.source_record_id == "NEW-1")).scalar_one()
    assert (e.collective_agreement_code, e.collective_agreement_known, e.leave_seniority_date) == (
        "TIS-1", True, D(2019, 1, 1))
