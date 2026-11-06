"""WP-1 work-injury leave type (HR sprint 1, wave 1 items 5 and 6; ADR-019). Hand calculations from
docs/hr/hse/WP-1_work_injury_leave.md section 4 (all examples fictitious).

WINJ tenant. Entity WA (main contractor): persons W0..W9 (employee number = sicil), employed since 2020-01-01.
Timesheets: every test month has 10 persons x 20 days x 50 planned h = 10,000 planned hours (WP-1 "planlı 10.000").
  Example 1   (2025-03): unexcused 50, sick 150, work_injury 80 (ONE person -> breakdown masked), annual 400,
              unpaid 30, other 20.
              KPI-053 = 200 / 10,000 = 2.00 % ; KPI-028 = 150 / 10,000 = 1.50 % ;
              KPI-003 (PO-1 not decided -> work_injury explicitly INCLUDED, option A) = 730 / 10,000 = 7.30 %
              KPI-003 with the PO-1 = C parameters (work_injury moved to excluded_types) = 650 / 10,000 = 6.50 %
  Example 1 "today" (2025-04): same hours but the 80 h are typed 'sick' (pre-ADR-019 import):
              KPI-053 = 280 / 10,000 = 2.80 % ; KPI-028 = 230 / 10,000 = 2.30 % ; KPI-003 = 7.30 %
  Example 1b  (2025-05): unexcused 100, sick 230, work_injury 120 (5 persons x 24 h -> visible):
              KPI-053 = 330 / 10,000 = 3.30 % (good) ; KPI-028 = 230 / 10,000 = 2.30 % (good)
  Example 1b "today" (2025-06): the 120 h typed 'sick': KPI-053 4.50 % (warning) ; KPI-028 3.50 % (warning)
  Example 2   (2026-03): others unexcused 50 + sick 150; person R = W9 imported with report no RPR-7781,
              02.03.2026 (Mon) - 11.03.2026 (Wed): 5x9 work pattern (HR Director condition 3) -> 8 working days x 9 =
              72 h. SGK code 1 -> work_injury: KPI-053 = 200 / 10,000 = 2.00 %. Re-import with code 3 -> reclassified
              in place (no second record): KPI-053 = 272 / 10,000 = 2.72 %.
Entity WB: 20 persons employed since 2020-01-01 (eligible for the whole of 2025).
  Example 3   (2025): person 0 on work_injury 2025-02-01..2025-08-31 (152 working days x 9 = 1,368 h > half of the
              window's 260.7 working days), no annual leave; person 1 no annual leave; persons 2..19 annual 9 h.
              KPI-056 = 1 / 19 = 5.263 % (good) ; the previous version (no work_injury in the long-absence list)
              = 2 / 20 = 10.0 %.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.analytics import calculators, engine
from app.analytics.calculators import Scope
from app.analytics.catalog import KPI_CATALOG
from app.analytics.engine import KpiError, validate_parameters
from app.models import (
    LEAVE_RECLASSIFY_ACTION,
    LEAVE_TYPES,
    AuditLog,
    DataQualityIssue,
    EmploymentRelationship,
    LeaveRecord,
    LegalEntity,
    PeriodClose,
    Person,
    Tenant,
    Timesheet,
)
from app.security.access import AccessContext
from app.seed.reference import sync_kpi_catalog
from app.services import imports
from app.services.timekeeping import classify_leave
from tests.factories import assign_default_pattern, declare_all_feeds

D = date
MONTHS = [D(2025, 3, 1), D(2025, 4, 1), D(2025, 5, 1), D(2025, 6, 1), D(2026, 3, 1)]


def _month(m: date) -> tuple[date, date]:
    nxt = D(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return m, nxt - timedelta(days=1)


@pytest.fixture(scope="module")
def winj(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        t = Tenant(code="WINJ", name="Work injury test tenant")
        db.add(t)
        db.flush()
        a = LegalEntity(tenant_id=t.id, code="WA", name="WA", entity_type="main_contractor")
        b = LegalEntity(tenant_id=t.id, code="WB", name="WB", entity_type="main_contractor")
        db.add_all([a, b])
        db.flush()

        def emp(entity, code):
            p = Person(tenant_id=t.id, person_code=code, first_name="W", last_name=code, birth_year=1990)
            db.add(p)
            db.flush()
            e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=entity.id,
                                       start_date=D(2020, 1, 1), employee_number=code)
            db.add(e)
            db.flush()
            return e

        wa = [emp(a, f"W{i}") for i in range(10)]
        for e in wa:
            for m in MONTHS:
                for d in range(1, 21):
                    db.add(Timesheet(tenant_id=t.id, employment_id=e.id, work_date=m.replace(day=d), planned_hours=50,
                                     worked_hours=50, overtime_hours=0))

        def leave(e, lt, sd, ed, h):
            db.add(LeaveRecord(tenant_id=t.id, employment_id=e.id, leave_type=lt, start_date=sd, end_date=ed, hours=h))

        for m, wi_type in ((3, "work_injury"), (4, "sick")):  # Example 1 / Example 1 "today"
            leave(wa[0], "unexcused", D(2025, m, 3), D(2025, m, 7), 50)
            leave(wa[1], "sick", D(2025, m, 10), D(2025, m, 14), 150)
            leave(wa[2], wi_type, D(2025, m, 17), D(2025, m, 21), 80)
            leave(wa[3], "annual", D(2025, m, 3), D(2025, m, 28), 400)
            leave(wa[4], "unpaid", D(2025, m, 3), D(2025, m, 5), 30)
            leave(wa[5], "other", D(2025, m, 6), D(2025, m, 7), 20)
        for m, wi_type in ((5, "work_injury"), (6, "sick")):  # Example 1b / 1b "today"
            leave(wa[0], "unexcused", D(2025, m, 5), D(2025, m, 9), 100)
            leave(wa[1], "sick", D(2025, m, 12), D(2025, m, 16), 230)
            for i in range(5, 10):
                leave(wa[i], wi_type, D(2025, m, 19), D(2025, m, 21), 24)
        leave(wa[0], "unexcused", D(2026, 3, 16), D(2026, 3, 20), 50)  # Example 2: everybody except R
        leave(wa[1], "sick", D(2026, 3, 16), D(2026, 3, 20), 150)

        wb = [emp(b, f"B{i}") for i in range(20)]  # Example 3
        leave(wb[0], "work_injury", D(2025, 2, 1), D(2025, 8, 31), 152 * 9)
        for e in wb[2:]:
            leave(e, "annual", D(2025, 7, 1), D(2025, 7, 1), 9)
        declare_all_feeds(db, t.id)  # ADR-020: every feed complete
        # ADR-024: entity B needs a known work pattern for the leave-days KPI-056 version (5 x 9 h = same values, WP-4
        # E2). Entity A stays without a pattern: example 2's import keeps the 9 h fallback (8 days x 9 = 72 h).
        assign_default_pattern(db, t.id, [b.id])
        db.commit()
        yield {"tenant_id": t.id, "A": a.id, "B": b.id}


def _ctx(w):
    return AccessContext(user_id=None, tenant_id=w["tenant_id"], email="t", locale="en",
                         grants={"kpi.read": None, "health.read": None, "import.write": None,
                                 "timekeeping.read": None})


def ev(db, w, code, m, entity="A", **kw):
    db.info.pop("kpi_memo", None)
    return engine.evaluate(db, _ctx(w), code, *(_month(m) if isinstance(m, date) else m), entity_ids={w[entity]}, **kw)


# ---------------------------------------------------------------- mapping (WP-1 3.2 table + acceptance 1-3)
@pytest.mark.parametrize("label,code,expected,warnings", [
    ("Rapor", "1", "work_injury", []),
    ("Hastalık raporu", "1", "work_injury", ["leave_label_code_conflict"]),
    ("İş kazası istirahati", "3", "sick", ["leave_label_code_conflict"]),
    ("Analık raporu", "4", "parental", []),
    ("Rapor", "2", "work_injury", []),
    ("İş kazası", None, "work_injury", []), ("İŞ KAZASI", None, "work_injury", []),
    ("is kazasi", None, "work_injury", []), ("İş Kazası Raporu", None, "work_injury", []),
    ("Meslek hastalığı", None, "work_injury", []), ("MESLEK HASTALIĞI RAPORU", None, "work_injury", []),
    ("work injury", None, "work_injury", []), ("Occupational disease", None, "work_injury", []),
    ("Industrial accident sick leave", None, "work_injury", []),
    ("Производственная травма", None, "work_injury", []),
    ("Несчастный случай на производстве", None, "work_injury", []), ("Профзаболевание", None, "work_injury", []),
    ("Raporlu", None, "sick", []), ("istirahat", None, "sick", []),
    ("Trafik kazası raporu", None, "sick", []),  # the label does not say work accident; a code would decide
    ("Несчастный случай", None, "other", []),  # review condition 4: no bare "несчастн"
    ("Occupational health check", None, "other", []),  # no bare "occupational"
    ("Analık – iş kazası sonrası", None, "parental", ["leave_label_ambiguous"]),  # D-6 precedence
    ("XYZ", "7", "other", ["unmapped_sgk_report_code"]),
    ("İstirahat raporu", "4", "parental", []), ("İstirahat raporu", "3", "sick", []),
])
def test_work_injury_mapping_table(label, code, expected, warnings):
    assert classify_leave(label, code) == (expected, warnings)


# ---------------------------------------------------------------- KPI values (WP-1 3.3, acceptance 4)
def test_kpi053_and_kpi028_exclude_work_injury_exact(db, winj):
    assert ev(db, winj, "KPI-053", MONTHS[0])["value"] == pytest.approx(2.00)
    assert ev(db, winj, "KPI-028", MONTHS[0])["value"] == pytest.approx(1.50)
    assert ev(db, winj, "KPI-053", MONTHS[1])["value"] == pytest.approx(2.80)  # same rest typed 'sick' (before)
    assert ev(db, winj, "KPI-028", MONTHS[1])["value"] == pytest.approx(2.30)
    b = ev(db, winj, "KPI-053", MONTHS[2])
    assert b["value"] == pytest.approx(3.30) and b["threshold_status"] == "good"
    assert b["breakdown"]["excluded_hours_by_type"]["work_injury"] == pytest.approx(120)
    s = ev(db, winj, "KPI-028", MONTHS[2])
    assert s["value"] == pytest.approx(2.30) and s["threshold_status"] == "good"
    before = ev(db, winj, "KPI-053", MONTHS[3]), ev(db, winj, "KPI-028", MONTHS[3])
    assert [r["value"] for r in before] == [pytest.approx(4.50), pytest.approx(3.50)]
    assert [r["threshold_status"] for r in before] == ["warning", "warning"]


def test_kpi003_work_injury_explicit_parameter_until_po1(db, winj):
    """PO-1 not decided: work_injury is INCLUDED by name (option A) - no silent change of KPI-003 (review cond. 1)."""
    r = ev(db, winj, "KPI-003", MONTHS[0])
    assert r["value"] == pytest.approx(7.30)
    assert ev(db, winj, "KPI-003", MONTHS[1])["value"] == pytest.approx(7.30)  # identical to the pre-ADR-019 typing
    params = r["formula"]["parameters"]
    assert "work_injury" in params["absence_types"] and "work_injury" not in params["excluded_types"]
    assert r["breakdown"]["excluded_types"] == ["public_holiday", "parental"]
    kpi = next(k for k in KPI_CATALOG if k["code"] == "KPI-003")
    assert "work_injury" in kpi["inclusion_rules"] and "PO-1" in kpi["inclusion_rules"]


def test_po1_option_c_is_a_parameter_change_only(db, winj, monkeypatch):
    """Deciding PO-1 = C: move work_injury to excluded_types in the catalog -> catalog sync opens a new formula
    version; the calculator is unchanged. 650 / 10,000 = 6.50 %."""
    item = next(k for k in KPI_CATALOG if k["code"] == "KPI-003")
    cur = item["formula"]["parameters"]
    option_c = {"absence_types": [t for t in cur["absence_types"] if t != "work_injury"],
                "excluded_types": [*cur["excluded_types"], "work_injury"]}
    validate_parameters(option_c, "absenteeism_rate")
    res = calculators.absenteeism_rate(db, Scope(winj["tenant_id"], frozenset({winj["A"]})), *_month(MONTHS[0]),
                                       option_c)
    assert res.value == pytest.approx(6.50)
    monkeypatch.setitem(item["formula"], "parameters", option_c)
    changes = [c for c in sync_kpi_catalog(db) if c["code"] == "KPI-003"]
    assert changes and changes[0]["new_formula_version"] is True


def test_absence_kpis_classify_every_leave_type():
    """ADR-019: every absenteeism_rate catalog entry lists each leave type as included or excluded, so a new type can
    never fall out of an absence KPI silently (WP-1 2.2); the validator enforces the same for new versions."""
    for k in KPI_CATALOG:
        f = k["formula"]
        if f["calculator_key"] == "absenteeism_rate":
            p = f["parameters"]
            assert set(p["absence_types"]) | set(p["excluded_types"]) == LEAVE_TYPES, k["code"]
    for bad in ({"absence_types": ["sick"], "excluded_types": ["unexcused"]},                      # incomplete
                {"absence_types": ["sick"], "excluded_types": sorted(LEAVE_TYPES)},                # overlapping
                {"excluded_types": sorted(LEAVE_TYPES - {"sick"})}):                               # no absence_types
        with pytest.raises(KpiError):
            validate_parameters(bad, "absenteeism_rate")
    with pytest.raises(KpiError):
        validate_parameters({"long_absence_types": ["annual"]}, "annual_leave_nonuse")
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"long_absence_types": ["sick"]}, "annual_leave_usage")


def test_work_injury_breakdown_masked_below_min_group(db, winj):
    """Acceptance 9 (KVKK md.6): one person's work-injury rest is not readable from the KPI-003 breakdown."""
    r = ev(db, winj, "KPI-003", MONTHS[0])
    assert r["breakdown"]["included_hours_by_type"]["work_injury"] is None
    assert r["breakdown"]["masked_types"] == ["work_injury"]
    assert r["value"] == pytest.approx(7.30)  # the rate itself is unchanged
    assert ev(db, winj, "KPI-053", MONTHS[0])["breakdown"]["excluded_hours_by_type"]["work_injury"] is None
    shown = ev(db, winj, "KPI-003", MONTHS[2])  # 5 people -> visible
    assert shown["breakdown"]["included_hours_by_type"]["work_injury"] == pytest.approx(120)
    assert "masked_types" not in shown["breakdown"]


def test_kpi056_long_absence_includes_work_injury(db, winj):
    """Example 3: 2 / 20 = 10.0 % (yellow) -> 1 / 19 = 5.263 % (green); previous version keeps 10.0 %."""
    year = (D(2025, 1, 1), D(2025, 12, 31))
    r = ev(db, winj, "KPI-056", year, entity="B")
    assert r["value"] == pytest.approx(100 / 19, abs=1e-3) and r["sample_size"] == 19
    assert r["lineage"]["excluded_long_absence"] == 1 and r["threshold_status"] == "good"
    assert "work_injury" in r["formula"]["parameters"]["long_absence_types"]
    old = calculators.annual_leave_nonuse(db, Scope(winj["tenant_id"], frozenset({winj["B"]})), *year,
                                          {"hours_per_day": 9, "threshold_min_days": 360})
    assert old.value == pytest.approx(10.0) and old.sample_size == 20


# ---------------------------------------------------------------- reclassification (WP-1 3.4, acceptance 6-7)
HDR = "Sicil;Firma;İzin Türü;Rapor Türü;Rapor No;Başlangıç;Bitiş;Saat"


def _import(db, w, rows, name):
    ctx = _ctx(w)
    content = (HDR + "\n" + "\n".join(";".join(r) for r in rows) + "\n").encode()
    up = imports.upload(db, ctx, entity_type="leave", filename=name, content=content)
    assert up["suggested_mapping"]["record_id"] == "Rapor No"
    v = imports.validate(db, ctx, up["batch_id"], None)
    issues = list(db.execute(select(DataQualityIssue).where(
        DataQualityIssue.import_batch_id == up["batch_id"])).scalars())
    c = imports.commit(db, ctx, up["batch_id"]) if v["valid_rows"] else None
    db.expire_all()
    return v, issues, c


def _r_records(db, w):
    return list(db.execute(select(LeaveRecord).join(EmploymentRelationship).where(
        EmploymentRelationship.employee_number == "W9", LeaveRecord.tenant_id == w["tenant_id"],
        LeaveRecord.start_date == D(2026, 3, 2))).scalars())


def test_leave_reclassification_updates_in_place_no_double_count(db, winj):
    r_row = ["W9", "WA", "Rapor", "1", "RPR-7781", "2026-03-02", "2026-03-11", ""]
    v, _, _ = _import(db, winj, [r_row], "r1.csv")
    assert v["valid_rows"] == 1
    [rec] = _r_records(db, winj)
    assert (rec.leave_type, float(rec.hours), rec.source_record_id) == ("work_injury", 72.0, "RPR-7781")
    assert ev(db, winj, "KPI-053", MONTHS[4])["value"] == pytest.approx(2.00)

    # May: SGK review re-issues the same report as illness (code 3) -> same record, type changes, no second record
    v, issues, c = _import(db, winj, [[*r_row[:3], "3", *r_row[4:]]], "r2.csv")
    assert v["valid_rows"] == 1 and c["committed_rows"] == 1
    assert {i.rule_code for i in issues} >= {"reclassified_down"}
    [rec] = _r_records(db, winj)
    assert (rec.leave_type, float(rec.hours), rec.record_version) == ("sick", 72.0, 2)
    audit = db.execute(select(AuditLog).where(AuditLog.action == LEAVE_RECLASSIFY_ACTION,
                                              AuditLog.entity_id == str(rec.id))).scalars().all()
    assert [(a.details["from"], a.details["to"], a.details["direction"]) for a in audit] == [
        ("work_injury", "sick", "reclassified_down")]
    assert ev(db, winj, "KPI-053", MONTHS[4])["value"] == pytest.approx(2.72)
    assert ev(db, winj, "KPI-003", MONTHS[4])["value"] == pytest.approx(2.72)  # 72 h counted once, not 144

    # reverse direction without a record number: same employee and dates inside {sick, work_injury} = same record
    v, issues, _ = _import(db, winj, [["W9", "WA", "İş kazası istirahati", "", "", "2026-03-02", "2026-03-11", ""]],
                           "r3.csv")
    assert {i.rule_code for i in issues} >= {"reclassified_up"}
    [rec] = _r_records(db, winj)
    assert (rec.leave_type, rec.record_version) == ("work_injury", 3)
    assert ev(db, winj, "KPI-053", MONTHS[4])["value"] == pytest.approx(2.00)

    # the same file content again: nothing changes (idempotent)
    v, issues, _ = _import(db, winj, [["W9", "WA", "İş kazası", "", "", "2026-03-02", "2026-03-11", "72"]], "r4.csv")
    assert {i.rule_code for i in issues} >= {"already_imported"}
    assert len(_r_records(db, winj)) == 1 and _r_records(db, winj)[0].record_version == 3


def test_reclassification_closed_period_frozen_with_note(db, winj):
    """D-2: a closed month keeps its frozen value; the view says how many records were reclassified after closing."""
    [rec] = _r_records(db, winj)
    assert rec.leave_type == "work_injury"  # state left by the previous test (KPI-053 March 2026 = 2.00)
    ctx = _ctx(winj)
    engine.freeze_period(db, ctx, MONTHS[4])
    db.add(PeriodClose(tenant_id=winj["tenant_id"], period_month=MONTHS[4], status="closed"))
    db.commit()
    _import(db, winj, [["W9", "WA", "Rapor", "3", "RPR-7781", "2026-03-02", "2026-03-11", "72"]], "r5.csv")
    assert _r_records(db, winj)[0].leave_type == "sick"
    r = ev(db, winj, "KPI-053", MONTHS[4])
    assert r["frozen"] is True and r["is_stale"] is True and r["value"] == pytest.approx(2.00)
    assert "1 leave record(s) reclassified after closing" in r["note"]


def test_sick_work_injury_overlap_is_error(db, winj):
    """Acceptance 7: a certified rest overlapping another one with other dates is never committed; other overlaps
    keep today's warning; inside one file the same rule applies."""
    v, issues, _ = _import(db, winj, [["W9", "WA", "Hastalık raporu", "", "", "2026-03-10", "2026-03-12", "27"]],
                           "o1.csv")
    assert v["error_rows"] == 1 and v["valid_rows"] == 0
    assert "sick_note_overlap" in {i.rule_code for i in issues if i.severity == "error"}
    v, issues, _ = _import(db, winj, [["W9", "WA", "Yıllık izin", "", "", "2026-03-10", "2026-03-12", "27"]], "o2.csv")
    assert v["valid_rows"] == 1 and "overlapping_leave" in {i.rule_code for i in issues}
    v, issues, _ = _import(db, winj, [
        ["W8", "WA", "Hastalık raporu", "", "", "2026-04-01", "2026-04-03", "27"],
        ["W8", "WA", "İş kazası", "", "", "2026-04-02", "2026-04-06", "27"],
        ["W7", "WA", "Hastalık raporu", "", "", "2026-04-01", "2026-04-03", "27"],
        ["W7", "WA", "İş kazası", "", "", "2026-04-01", "2026-04-03", "27"],
    ], "o3.csv")
    assert v["valid_rows"] == 2 and v["error_rows"] == 2
    assert {i.rule_code for i in issues if i.severity == "error"} == {"sick_note_overlap", "duplicate_in_file"}


def test_label_warnings_do_not_echo_health_label(db, winj):
    v, issues, _ = _import(db, winj, [
        ["W6", "WA", "Hastalık raporu", "1", "", "2026-05-04", "2026-05-04", "9"],
        ["W6", "WA", "XYZ", "7", "", "2026-05-11", "2026-05-11", "9"],
    ], "l1.csv")
    assert v["valid_rows"] == 2
    by_rule = {i.rule_code: i for i in issues}
    assert {"leave_label_code_conflict", "unmapped_sgk_report_code", "unmapped_leave_label"} <= set(by_rule)
    assert "Hastalık" not in by_rule["leave_label_code_conflict"].message
    got = {r.leave_type for r in db.execute(select(LeaveRecord).join(EmploymentRelationship).where(
        EmploymentRelationship.employee_number == "W6", LeaveRecord.tenant_id == winj["tenant_id"],
        LeaveRecord.start_date >= D(2026, 5, 1))).scalars()}
    assert got == {"work_injury", "other"}


def test_reclassification_direction_counts_group_level(db, winj):
    """WP-1 3.4 step 6: counts only; an entity is listed only from HRSWI_MIN_GROUP_SIZE reclassifications per quarter."""
    ctx = _ctx(winj)
    span = (D(2000, 1, 1), D(2100, 1, 1))
    [q] = imports.leave_reclassification_counts(db, ctx, *span)
    n_up, n_down = q["total"]["reclassified_up"], q["total"]["reclassified_down"]
    assert (n_up, n_down) == (1, 2)  # up: r3 ; down: r2, r5 (tests above)
    assert q["by_legal_entity"] == [] and q["other_entities"] == {"reclassified_up": 1, "reclassified_down": 2}
    for _ in range(2):
        db.add(AuditLog(tenant_id=winj["tenant_id"], action=LEAVE_RECLASSIFY_ACTION, entity_type="leave_record",
                        details={"direction": "reclassified_up", "legal_entity_id": winj["A"]}))
    db.commit()
    [q] = imports.leave_reclassification_counts(db, ctx, *span)
    assert q["by_legal_entity"] == [{"legal_entity_id": winj["A"], "reclassified_up": 3, "reclassified_down": 2}]
    assert q["other_entities"] is None
    assert all(set(e) == {"legal_entity_id", "reclassified_up", "reclassified_down"} for e in q["by_legal_entity"])
    only_b = AccessContext(user_id=None, tenant_id=winj["tenant_id"], email="t", locale="en",
                           grants={"timekeeping.read": frozenset({winj["B"]})})
    assert imports.leave_reclassification_counts(db, only_b, *span) == []
