"""Timesheet import `timesheet` (wave-2 item 12, ADR-022).

Hand-calculated expectations: docs/hr/data/WP-6_import_field_dictionaries.md 3.1-3.2 (+ HR Director review) and the
WP-4 review condition 2 (period rows = no daily granularity).

Tenant TSI. ANA-01 (main contractor) owns PRJ-A / PRJ-B, ALT-07 is its subcontractor on PRJ-B.
  Sample file 3.2.3 (Oct 2026): row 2 P-0001 05.10 PRJ-A 7,5 / 10 / 2,5 / 0 / 8 work -> valid;
      row 3 P-0001 11.10 weekly rest 0 h, productive blank -> valid, productive NULL (not 0);
      row 4 P-0002 05.10 night 8 > worked 6 -> error night_exceeds_worked (+ warning night_over_7_5).
  Weekly overtime (Aug 2026, ISO week 03.08-09.08): P-0006 5 x 10 h worked, 0 overtime -> expected
      max(0, 50 - 45) = 5, |0 - 5| = 5 > 1 -> 1 employee-week; P-0001 5 x 10 h with 1 h overtime a day -> 5 = 5, no
      warning.
  Paper leave: P-0005 sick 03.08 + 8 h worked that day -> leave_and_work_same_day 1 employee-day.
  Planned on leave: P-0002 annual leave 10.08-14.08 (5 Mon-Fri days), the file has 7,5 planned h on 4 of them and a
      0 h row on one -> 4/5 = 0.8 -> 'mixed'; declared 'excluded' -> planned_on_leave_mismatch.
"""
import json
from datetime import date

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError

from app.analytics import summary
from app.analytics.calculators import Scope, _hours_query
from app.models import (
    TIMESHEET_CORRECT_ACTION,
    Assignment,
    AuditLog,
    ContractorRelationship,
    DataFeedCoverage,
    DataQualityIssue,
    EmploymentRelationship,
    ImportBatch,
    ImportStagingRow,
    LeaveRecord,
    LegalEntity,
    PeriodClose,
    Person,
    Project,
    Tenant,
    Timesheet,
    User,
    UserRoleAssignment,
)
from app.security.auth import hash_password
from app.seed.reference import seed_permissions_and_roles, seed_reference
from app.services import coverage as cov
from app.services import timesheet_import as tsi
from app.services.imports import suggest_mapping

D = date
ADMIN = "ts.admin@tsi.local"
CM = "ts.cm@tsi.local"  # CONTRACTOR_MANAGER on ALT-07 only
HDR = "sicil;firma;tarih;proje;planlı saat;çalışılan saat;fazla mesai;gece saati;üretken saat;gün tipi"
SAMPLE = [  # WP-6 data 3.2.3, verbatim
    "P-0001;ANA-01;2026-10-05;PRJ-A;7,5;10;2,5;0;8;work",
    "P-0001;ANA-01;2026-10-11;PRJ-A;0;0;0;0;;weekly_rest",
    "P-0002;ANA-01;2026-10-05;PRJ-A;7,5;6;0;8;;work",
]


@pytest.fixture(scope="module")
def ts(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="TSI", name="Timesheet import test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        ents = {"A": LegalEntity(tenant_id=t.id, code="ANA-01", name="Ana", entity_type="main_contractor"),
                "B": LegalEntity(tenant_id=t.id, code="ALT-07", name="Alt", entity_type="subcontractor")}
        db.add_all(ents.values())
        db.flush()
        prj = {c: Project(tenant_id=t.id, code=c, name=c, owner_entity_id=ents["A"].id) for c in ("PRJ-A", "PRJ-B")}
        db.add_all(prj.values())
        db.flush()
        db.add(ContractorRelationship(tenant_id=t.id, parent_entity_id=ents["A"].id, child_entity_id=ents["B"].id,
                                      project_id=prj["PRJ-B"].id, valid_from=D(2020, 1, 1), data_sharing_granted=True))
        emps = {}
        for code, ent in [*((f"P-000{i}", "A") for i in range(1, 9)), ("P-0101", "B")]:
            p = Person(tenant_id=t.id, person_code=code, first_name="Ts", last_name=code)
            db.add(p)
            db.flush()
            e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ents[ent].id,
                                       employee_number=code, start_date=D(2024, 1, 1))
            db.add(e)
            db.flush()
            emps[code] = e.id
        db.add(Assignment(tenant_id=t.id, employment_id=emps["P-0003"], project_id=prj["PRJ-A"].id,
                          valid_from=D(2026, 1, 1)))
        for p in ("PRJ-A", "PRJ-B"):  # two concurrent projects -> no single attribution
            db.add(Assignment(tenant_id=t.id, employment_id=emps["P-0004"], project_id=prj[p].id,
                              valid_from=D(2026, 1, 1)))
        db.add(LeaveRecord(tenant_id=t.id, employment_id=emps["P-0005"], leave_type="sick", start_date=D(2026, 8, 3),
                           end_date=D(2026, 8, 3), hours=9))
        db.add(LeaveRecord(tenant_id=t.id, employment_id=emps["P-0002"], leave_type="annual",
                           start_date=D(2026, 8, 10), end_date=D(2026, 8, 14), hours=37.5))
        db.add(PeriodClose(tenant_id=t.id, period_month=D(2026, 6, 1), status="closed"))
        pw = hash_password("Demo!2345")
        for email, role, ent in [(ADMIN, "SYSTEM_ADMIN", None), (CM, "CONTRACTOR_MANAGER", "B")]:
            u = User(tenant_id=t.id, email=email, full_name=email.split("@")[0], password_hash=pw, locale="en")
            db.add(u)
            db.flush()
            db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role], legal_entity_id=ents[ent].id if ent else None,
                                      scope_type="legal_entity" if ent else "tenant"))
        db.commit()
        return {"tenant_id": t.id, "entities": {k: v.id for k, v in ents.items()},
                "projects": {k: v.id for k, v in prj.items()}, "emps": emps}


def _csv(rows, header=HDR):
    return (header + "\n" + "\n".join(rows) + "\n").encode()


def _upload(client, auth, content, name, user=ADMIN, coverage=None, status=201, **form):
    data = {"entity_type": form.pop("entity_type", "timesheet"), **{k: str(v).lower() if isinstance(v, bool) else v
                                                                    for k, v in form.items()}}
    if coverage is not None:
        data["coverage"] = json.dumps(coverage)
    r = client.post("/api/v1/imports", headers=auth(user), files={"file": (name, content)}, data=data)
    assert r.status_code == status, r.text
    return r.json()


def _run(client, auth, content, name, user=ADMIN, coverage=None, mapping=None, **form):
    up = _upload(client, auth, content, name, user, coverage, **form)
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={"mapping": mapping}, headers=auth(user))
    assert v.status_code == 200, v.text
    v = v.json()
    c = None
    if v["valid_rows"]:
        c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(user))
        assert c.status_code == 200, c.text
        c = c.json()
    return up, v, c


def _issues(db, batch_id):
    db.expire_all()
    return list(db.execute(select(DataQualityIssue).where(DataQualityIssue.import_batch_id == batch_id)
                           .order_by(DataQualityIssue.row_number)).scalars())


def _codes(db, batch_id, row=None):
    return {(i.row_number, i.rule_code) if row is None else i.rule_code
            for i in _issues(db, batch_id) if row is None or i.row_number == row}


def _row(db, ts, emp, day, project="PRJ-A"):
    db.expire_all()
    pid = ts["projects"][project] if project else None
    return db.execute(select(Timesheet).where(Timesheet.employment_id == ts["emps"][emp], Timesheet.work_date == day,
                                              Timesheet.project_id.is_not_distinct_from(pid))).scalar_one_or_none()


def _assert_summary_exact(db, tid):
    """ADR-016: after any import the summary, once refreshed, equals the raw table cell by cell, and both KPI read
    paths return the same aggregates."""
    db.expire_all()
    db.info.pop("ts_dirty", None)
    summary.refresh(db, tid)
    agg = ("SELECT date_trunc('month', work_date)::date, employment_id, project_id, count(*), sum(planned_hours), "
           "sum(worked_hours), sum(overtime_hours), coalesce(sum(productive_hours), 0), count(productive_hours), "
           "sum(night_hours) FROM timesheets WHERE tenant_id = :t AND status = 'approved' GROUP BY 1, 2, 3")
    smry = ("SELECT month, employment_id, project_id, n, planned, worked, overtime, productive, n_productive, night "
            "FROM timesheet_monthly WHERE tenant_id = :t")
    raw = sorted(tuple(r) for r in db.execute(text(agg), {"t": tid}))
    summ = sorted(tuple(r) for r in db.execute(text(smry), {"t": tid}))
    assert raw == summ
    w = (D(2026, 1, 1), D(2026, 12, 31))
    assert _hours_query(db, Scope(tid), [w], summary=True) == _hours_query(db, Scope(tid), [w], summary=False)


# ---------------------------------------------------------------- dictionary


def test_timesheet_import_field_set_and_aliases():
    m = suggest_mapping(HDR.split(";"), tsi.TIMESHEET_FIELDS)
    assert m == {"external_id": "sicil", "source_system": None, "legal_entity_code": "firma", "work_date": "tarih",
                 "project_code": "proje", "shift_code": None, "planned_hours": "planlı saat",
                 "worked_hours": "çalışılan saat", "overtime_hours": "fazla mesai", "night_hours": "gece saati",
                 "productive_hours": "üretken saat", "day_type": "gün tipi", "status": None, "period_days": None}
    en = suggest_mapping(["Employee ID", "Company", "Work Date", "Planned Hours", "Worked Hours", "Day Type",
                          "Status", "Period Days"], tsi.TIMESHEET_FIELDS)
    assert en["external_id"] == "Employee ID" and en["day_type"] == "Day Type" and en["period_days"] == "Period Days"
    ru = suggest_mapping(["Табельный номер", "Компания", "Дата", "Плановые часы", "Отработано"], tsi.TIMESHEET_FIELDS)
    assert ru["external_id"] == "Табельный номер" and ru["worked_hours"] == "Отработано"


def test_prohibited_columns_name_identity_health():
    assert tsi.prohibited_columns(["sicil", "Ad Soyad", "TCKN", "Tanı", "Proje", "Gün tipi", "Name"]) == [
        "Ad Soyad", "TCKN", "Tanı", "Name"]


# ---------------------------------------------------------------- sample file (3.2.3)


def test_sample_file_night_exceeding_worked_rejected_and_blank_productive_null(client, auth, db, ts):
    up, v, c = _run(client, auth, _csv(SAMPLE), "sample.csv", approved_source=True)
    assert up["options"] == {"mode": "skip", "approved_source": True}
    assert (v["valid_rows"], v["error_rows"], c["committed_rows"]) == (2, 1, 2)
    assert _codes(db, up["batch_id"], 4) == {"night_exceeds_worked", "night_over_7_5"}
    assert not {c for c in _codes(db, up["batch_id"], 2) | _codes(db, up["batch_id"], 3)} - {"entity_not_on_project"}
    r2 = _row(db, ts, "P-0001", D(2026, 10, 5))
    assert (float(r2.planned_hours), float(r2.worked_hours), float(r2.overtime_hours), float(r2.night_hours),
            float(r2.productive_hours), r2.day_type, r2.status, r2.period_days) == (7.5, 10, 2.5, 0, 8, "work",
                                                                                     "approved", 1)
    r3 = _row(db, ts, "P-0001", D(2026, 10, 11))
    assert r3.productive_hours is None and r3.day_type == "weekly_rest" and float(r3.planned_hours) == 0
    assert _row(db, ts, "P-0002", D(2026, 10, 5)) is None
    # KVKK: the stored source reference is the employee number, never a name
    assert r2.source_record_id == "P-0001" and r2.source_system == "import"
    # coverage without a declaration: 'partial' (row presence is not completeness)
    cell = db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == ts["tenant_id"], DataFeedCoverage.feed_type == "timesheet",
        DataFeedCoverage.legal_entity_id == ts["entities"]["A"], DataFeedCoverage.period_month == D(2026, 10, 1)
    )).scalar_one()
    assert cell.completeness == "partial" and cell.coverage_ratio is None
    _assert_summary_exact(db, ts["tenant_id"])


def test_blank_overtime_and_night_are_zero_other_blanks_null_or_error(client, auth, db, ts):
    rows = ["P-0007;ANA-01;2026-07-01;PRJ-A;7,5;7,5;;;;",
            "P-0007;ANA-01;2026-07-02;PRJ-A;;7,5;;;;",            # planned blank -> required
            "P-0007;ANA-01;2026-07-03;PRJ-A;7,5;6;7;0;;work",      # overtime > worked
            "P-0007;ANA-01;2026-07-06;PRJ-A;7,5;6;0;0;6,5;work",   # productive > worked
            "P-0007;ANA-01;2026-07-07;PRJ-A;7,5;25;0;0;;work",     # > 24 h
            "P-0007;ANA-01;2026-07-08;PRJ-A;abc;5;0;0;;work",      # number format
            "P-0007;ANA-01;2026-07-09;PRJ-X;7,5;5;0;0;;work",      # unknown project
            "P-0007;ANA-01;2027-01-04;PRJ-A;7,5;5;0;0;;work",      # future
            "P-0007;ANA-01;2026-06-15;PRJ-A;7,5;5;0;0;;work",      # closed month (June 2026)
            "P-0007;ANA-01;2026-07-10;PRJ-A;7,5;12;0;0;;tatilx",   # > 11 h warning, unknown day type
            "P-0099;ANA-01;2026-07-13;PRJ-A;7,5;7,5;0;0;;work"]    # no employment
    up, v, _c = _run(client, auth, _csv(rows), "blanks.csv", approved_source=True)
    b = up["batch_id"]
    assert (v["valid_rows"], v["error_rows"]) == (2, 9)
    assert "required" in _codes(db, b, 3)
    assert "overtime_exceeds_worked" in _codes(db, b, 4)
    assert "productive_exceeds_worked" in _codes(db, b, 5)
    assert "hours_range" in _codes(db, b, 6)
    assert "hours_format" in _codes(db, b, 7)
    assert "unknown_project" in _codes(db, b, 8)
    assert "future_date" in _codes(db, b, 9)
    assert "period_closed" in _codes(db, b, 10)
    assert {"daily_max_exceeded", "unknown_day_type"} <= _codes(db, b, 11)
    assert "no_employment" in _codes(db, b, 12)
    r = _row(db, ts, "P-0007", D(2026, 7, 1))
    assert (float(r.overtime_hours), float(r.night_hours), r.productive_hours, r.day_type) == (0, 0, None, None)
    assert _row(db, ts, "P-0007", D(2026, 7, 10)).day_type == "unknown"


# ---------------------------------------------------------------- cross-row checks (3.2.2)


def test_timesheet_weekly_overtime_consistency_warning_and_paper_leave(client, auth, db, ts):
    days = [f"2026-08-0{d}" for d in range(3, 8)]  # Mon 03.08 - Fri 07.08
    rows = [f"P-0006;ANA-01;{d};PRJ-A;9;10;0;0;;work" for d in days]          # 50 h worked, 0 overtime
    rows += [f"P-0001;ANA-01;{d};PRJ-A;9;10;1;0;;work" for d in days]         # 50 h worked, 5 overtime
    rows += ["P-0006;ANA-01;2026-08-08;PRJ-A;0;0;0;0;;weekly_rest",          # Sat/Sun rows close the ISO week
             "P-0006;ANA-01;2026-08-09;PRJ-A;0;0;0;0;;weekly_rest",
             "P-0005;ANA-01;2026-08-03;PRJ-A;9;8;0;0;;work"]                 # sick that day + 8 h worked
    up, v, _ = _run(client, auth, _csv(rows), "weekly.csv", approved_source=True)
    group = {i.rule_code: i.message for i in _issues(db, up["batch_id"]) if i.row_number is None}
    assert group["overtime_weekly_inconsistent"].startswith("1 employee-week(s)")
    assert group["leave_and_work_same_day"].startswith("1 employee-day(s)")
    assert v["error_rows"] == 0  # warnings only (L-15 / L-16: the source record is loaded)
    _assert_summary_exact(db, ts["tenant_id"])


def test_planned_on_leave_mismatch_warning_and_leave_day_row_creates_no_leave(client, auth, db, ts):
    before = db.execute(select(LeaveRecord.id).where(LeaveRecord.tenant_id == ts["tenant_id"])).all()
    rows = [f"P-0002;ANA-01;2026-08-1{d};PRJ-A;7,5;0;0;0;;leave" for d in range(0, 4)]  # Mon-Thu: planned 7,5
    rows.append("P-0002;ANA-01;2026-08-14;PRJ-A;0;0;0;0;;leave")                       # Fri: planned 0
    decl = {"period_start": "2026-08-01", "period_end": "2026-08-31", "legal_entity_codes": ["ANA-01"],
            "completeness": "partial", "coverage_ratio": 0.5, "planned_on_leave_days": "excluded"}
    up, _v, _ = _run(client, auth, _csv(rows), "onleave.csv", coverage=decl, approved_source=True)
    group = {i.rule_code: i.message for i in _issues(db, up["batch_id"]) if i.row_number is None}
    assert "'excluded' but the file shows 'mixed' (4 of 5" in group["planned_on_leave_mismatch"]
    db.expire_all()
    batch = db.get(ImportBatch, up["batch_id"])
    assert batch.options["observed_planned_on_leave_days"] == {"profile": "mixed", "leave_workdays": 5,
                                                               "with_planned_hours": 4}
    # D-6 / WP-6 3.2.4: a 'leave' day row is evidence for the WP-4 scenario only, never a leave record
    assert db.execute(select(LeaveRecord.id).where(LeaveRecord.tenant_id == ts["tenant_id"])).all() == before
    cell = db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == ts["tenant_id"], DataFeedCoverage.feed_type == "timesheet",
        DataFeedCoverage.legal_entity_id == ts["entities"]["A"], DataFeedCoverage.period_month == D(2026, 8, 1))
    ).scalar_one()
    assert (cell.completeness, float(cell.coverage_ratio), cell.planned_on_leave_days) == ("partial", 0.5, "excluded")


def test_period_rows_flagged_non_daily_and_profile_unknown(client, auth, db, ts):
    rows = ["P-0008;ANA-01;2026-05-04;PRJ-A;45;50;5;0;;work;7"]
    up, _v, c = _run(client, auth, _csv(rows, HDR + ";dönem gün"), "weekly_total.csv", approved_source=True)
    assert c["committed_rows"] == 1
    assert "non_daily_granularity" in _codes(db, up["batch_id"], 2)
    assert "daily_max_exceeded" not in _codes(db, up["batch_id"], 2)  # daily checks do not apply to a 7-day total
    r = _row(db, ts, "P-0008", D(2026, 5, 4))
    assert r.period_days == 7 and float(r.worked_hours) == 50
    db.expire_all()
    assert db.get(ImportBatch, up["batch_id"]).options["observed_planned_on_leave_days"]["profile"] == "unknown"
    bad = _run(client, auth, _csv(["P-0008;ANA-01;2026-05-11;PRJ-A;45;50;5;0;;work;0"], HDR + ";dönem gün"),
               "bad_period.csv", approved_source=True)[0]
    assert "period_days_format" in _codes(db, bad["batch_id"], 2)


# ---------------------------------------------------------------- re-import / correction semantics (3.1)


def test_reimport_skip_then_replace_corrects_in_place_and_summary_stays_exact(client, auth, db, ts):
    tid = ts["tenant_id"]
    base = ["P-0003;ANA-01;2026-09-01;PRJ-A;9;9;0;0;;work", "P-0003;ANA-01;2026-09-02;PRJ-A;9;10;1;0;;work"]
    _run(client, auth, _csv(base), "sep_v1.csv", approved_source=True)
    _assert_summary_exact(db, tid)
    changed = [base[0], "P-0003;ANA-01;2026-09-02;PRJ-A;9;11;2;0;;work"]
    up, _, _ = _run(client, auth, _csv(changed), "sep_v2.csv", approved_source=True)
    assert _codes(db, up["batch_id"], 2) == {"already_imported"}
    assert _codes(db, up["batch_id"], 3) == {"already_imported_changed"}
    assert float(_row(db, ts, "P-0003", D(2026, 9, 2)).worked_hours) == 10  # default mode keeps the stored row
    up, _v, _c = _run(client, auth, _csv([*changed, "P-0003;ANA-01;2026-09-03;PRJ-A;9;9;0;0;;work"]), "sep_v3.csv",
                    approved_source=True, mode="replace")
    assert _codes(db, up["batch_id"], 3) == {"timesheet_corrected"}
    db.info.pop("ts_dirty", None)
    assert summary.dirty_from(db, tid) <= D(2026, 9, 1)  # trigger marked the month before any refresh
    r = _row(db, ts, "P-0003", D(2026, 9, 2))
    assert (float(r.worked_hours), float(r.overtime_hours), r.record_version) == (11, 2, 2)
    assert db.execute(select(Timesheet.id).where(Timesheet.employment_id == ts["emps"]["P-0003"],
                                                 Timesheet.work_date == D(2026, 9, 2))).all().__len__() == 1
    audit = db.execute(select(AuditLog).where(AuditLog.tenant_id == tid,
                                              AuditLog.action == TIMESHEET_CORRECT_ACTION)).scalars().all()
    assert len(audit) == 1 and audit[0].entity_id == str(r.id)
    assert audit[0].details["changed"] == {"worked_hours": {"from": 10.0, "to": 11.0},
                                           "overtime_hours": {"from": 1.0, "to": 2.0}}
    _assert_summary_exact(db, tid)
    # Sept 2026 raw = summary: 9 + 11 + 9 = 29 worked h for P-0003
    sep = (D(2026, 9, 1), D(2026, 9, 30))
    raw = _hours_query(db, Scope(tid, frozenset({ts["entities"]["A"]})), [sep], summary=False)[sep]
    assert float(raw[2]) == 29


def test_draft_rows_not_counted_until_corrected_to_approved(client, auth, db, ts):
    tid = ts["tenant_id"]
    hdr = "sicil;firma;tarih;proje;planlı saat;çalışılan saat"
    up, _v, c = _run(client, auth, _csv(["P-0004;ANA-01;2026-04-06;PRJ-B;9;9"], hdr), "draft.csv")
    assert c["committed_rows"] == 1
    assert "approval_unknown" in {i.rule_code for i in _issues(db, up["batch_id"]) if i.row_number is None}
    assert _row(db, ts, "P-0004", D(2026, 4, 6), "PRJ-B").status == "draft"
    apr = (D(2026, 4, 1), D(2026, 4, 30))
    assert _hours_query(db, Scope(tid), [apr], summary=False).get(apr) is None  # draft rows never enter KPIs
    _assert_summary_exact(db, tid)
    _run(client, auth, _csv(["P-0004;ANA-01;2026-04-06;PRJ-B;9;9", "P-0004;ANA-01;2026-04-07;PRJ-B;9;9"], hdr),
         "approved.csv", approved_source=True, mode="replace")
    assert _row(db, ts, "P-0004", D(2026, 4, 6), "PRJ-B").status == "approved"
    assert float(_hours_query(db, Scope(tid), [apr], summary=False)[apr][2]) == 18
    _assert_summary_exact(db, tid)
    st = _run(client, auth, _csv(["P-0004;ANA-01;2026-04-08;PRJ-B;9;9;beklemez"], hdr + ";durum"), "status.csv")[0]
    assert "status_unknown" in _codes(db, st["batch_id"], 2)


# ---------------------------------------------------------------- project attribution (ADR-017)


def test_project_attribution_from_assignment_and_null_project_unique(client, auth, db, ts):
    tid = ts["tenant_id"]
    rows = ["P-0003;ANA-01;2026-03-02;;9;9;0;0;;work",   # one assignment project -> PRJ-A
            "P-0004;ANA-01;2026-03-02;;9;9;0;0;;work",   # two assignment projects -> NULL
            "P-0006;ANA-01;2026-03-02;;9;9;0;0;;work"]   # no assignment -> NULL
    up, _v, _c = _run(client, auth, _csv(rows), "noproject.csv", approved_source=True)
    b = up["batch_id"]
    assert "project_derived_from_assignment" in _codes(db, b, 2)
    assert "project_code_missing" in _codes(db, b, 3) and "project_code_missing" in _codes(db, b, 4)
    assert _row(db, ts, "P-0003", D(2026, 3, 2)) is not None
    assert _row(db, ts, "P-0004", D(2026, 3, 2), None) is not None
    # a project-limited viewer of PRJ-A sees only the attributed row; NULL-project rows stay invisible (ADR-017)
    mar = (D(2026, 3, 1), D(2026, 3, 31))
    limited = Scope(tid, frozenset({ts["entities"]["A"]}), project_limits=((ts["entities"]["A"], (ts["projects"]["PRJ-A"],)),))
    assert int(_hours_query(db, limited, [mar], summary=False)[mar][0]) == 1
    assert int(_hours_query(db, Scope(tid), [mar], summary=False)[mar][0]) == 3
    # re-importing a NULL-project row never duplicates it (uq_timesheet_day NULLS NOT DISTINCT)
    up2, _, _ = _run(client, auth, _csv([*rows[1:], "P-0006;ANA-01;2026-03-03;;9;9;0;0;;work"]), "noproject2.csv",
                     approved_source=True)
    assert _codes(db, up2["batch_id"], 2) >= {"already_imported"}
    n = db.execute(select(Timesheet.id).where(Timesheet.employment_id == ts["emps"]["P-0004"],
                                              Timesheet.work_date == D(2026, 3, 2))).all()
    assert len(n) == 1
    with pytest.raises(IntegrityError):  # the database itself refuses a second NULL-project row for the same day
        db.execute(insert(Timesheet), [{"tenant_id": tid, "employment_id": ts["emps"]["P-0004"], "project_id": None,
                                        "work_date": D(2026, 3, 2), "planned_hours": 1, "worked_hours": 1,
                                        "overtime_hours": 0, "night_hours": 0, "status": "approved"}])
    db.rollback()
    _assert_summary_exact(db, tid)


# ---------------------------------------------------------------- coverage, scope, KVKK, options


def test_coverage_declared_on_commit_per_entity_month_confirms_empty_for_declared_entity(client, auth, db, ts):
    decl = {"period_start": "2026-02-01", "period_end": "2026-02-28", "legal_entity_codes": ["ANA-01", "ALT-07"],
            "completeness": "complete", "planned_on_leave_days": "included"}
    up, _v, _c = _run(client, auth, _csv(["P-0001;ANA-01;2026-02-02;PRJ-A;9;9;0;0;;work",
                                        "P-0001;ANA-01;2026-03-02;PRJ-A;9;9;0;0;;work"]), "feb.csv", coverage=decl,
                    approved_source=True)
    assert "row_outside_declared_coverage" in _codes(db, up["batch_id"], 3)
    db.expire_all()
    cells = {(r.legal_entity_id, r.period_month): r for r in db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == ts["tenant_id"], DataFeedCoverage.feed_type == "timesheet",
        DataFeedCoverage.import_batch_id == up["batch_id"])).scalars()}
    a, b = cells[(ts["entities"]["A"], D(2026, 2, 1))], cells[(ts["entities"]["B"], D(2026, 2, 1))]
    assert (a.completeness, a.confirmed_empty, a.planned_on_leave_days) == ("complete", False, "included")
    assert (b.completeness, b.confirmed_empty) == ("complete", True)
    assert (ts["entities"]["A"], D(2026, 3, 1)) not in cells  # outside the declaration: no coverage


def test_header_only_timesheet_file_rejected_even_with_complete_declaration(client, auth, ts):
    decl = {"period_start": "2026-01-01", "period_end": "2026-01-31", "legal_entity_codes": ["ANA-01"],
            "completeness": "complete"}
    r = _upload(client, auth, (HDR + "\n").encode(), "empty.csv", coverage=decl, status=400)
    assert "event feeds" in r["detail"]


def test_scope_whole_entity_import_write_required(client, auth, db, ts):
    rows = ["P-0101;ALT-07;2026-01-05;PRJ-B;9;9;0;0;;work", "P-0001;ANA-01;2026-01-05;PRJ-A;9;9;0;0;;work"]
    up, v, _c = _run(client, auth, _csv(rows), "cm.csv", user=CM, approved_source=True)
    assert (v["valid_rows"], v["error_rows"]) == (1, 1)
    assert "scope" in _codes(db, up["batch_id"], 3)
    assert _row(db, ts, "P-0101", D(2026, 1, 5), "PRJ-B") is not None


def test_name_and_identity_columns_dropped_before_storage(client, auth, db, ts):
    content = _csv(["P-0005;ANA-01;2026-01-12;PRJ-A;9;9;0;0;;work;Ayşe Yılmaz;12345678901;Hücre A"],
                   HDR + ";Ad Soyad;TCKN;Ekip")
    up, _v, _c = _run(client, auth, content, "names.csv", approved_source=True)
    assert set(up["dropped_columns"]) == {"Ad Soyad", "TCKN"}
    assert all("Ayşe" not in json.dumps(r) for r in up["preview"])
    db.expire_all()
    raws = db.execute(select(ImportStagingRow.raw).where(ImportStagingRow.batch_id == up["batch_id"])).scalars().all()
    assert all(set(r) <= set(HDR.split(";")) for r in raws)  # unmapped 'Ekip' minimised after commit too
    assert "prohibited_columns_dropped" in {i.rule_code for i in _issues(db, up["batch_id"]) if i.row_number is None}
    bad = _upload(client, auth, _csv(["P-0005;ANA-01;2026-01-13;9;9;x"],
                                     "sicil;firma;tarih;planlı saat;çalışılan saat;Name"), "alias.csv")
    assert bad["dropped_columns"] == ["Name"]


def test_upload_options_validated(client, auth, ts):
    r = _upload(client, auth, _csv(SAMPLE[:1]), "opt.csv", mode="overwrite", status=400)
    assert "mode" in r["detail"]
    r = _upload(client, auth, b"sicil;firma;izin;baslangic;bitis\nP-0001;ANA-01;yillik;2026-01-01;2026-01-02\n",
                "leave.csv", entity_type="leave", mode="replace", status=400)
    assert "only for entity_type 'timesheet'" in r["detail"]


def test_mark_stale_and_dirty_month_after_commit(client, auth, db, ts):
    tid = ts["tenant_id"]
    summary.refresh(db, tid)
    v0 = db.execute(text("SELECT data_version FROM tenants WHERE id = :t"), {"t": tid}).scalar()
    _run(client, auth, _csv(["P-0007;ANA-01;2026-02-16;PRJ-A;9;9;0;0;;work"]), "stale.csv", approved_source=True)
    db.expire_all()
    db.info.pop("ts_dirty", None)
    assert db.execute(text("SELECT data_version FROM tenants WHERE id = :t"), {"t": tid}).scalar() > v0
    assert summary.dirty_from(db, tid) == D(2026, 2, 1)
    _assert_summary_exact(db, tid)


def test_coverage_cells_of_period_row_span_months(ts):
    m = {"legal_entity_code": "X", "work_date": "2026-01-29", "period_days": 7}
    assert cov._row_cells(m, {"X": 1}, D(2026, 11, 6)) == {(1, D(2026, 1, 1)), (1, D(2026, 2, 1))}
