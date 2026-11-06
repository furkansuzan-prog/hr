"""Data-feed coverage metadata and the KPI coverage gate (B-20, audit finding #19, ADR-020).

Hand calculations from docs/hr/data/WP-6_import_field_dictionaries.md section 4 (fictitious data):
firm A = 100 persons, firm B = 50 persons, constant from 2025-01-01; min_feed_coverage 0.8 (safety 0.9).

  K1 KPI-008 Q3 2026 (92 days): A 1,200 h complete, B 0 rows complete + confirmed_empty
     -> 1,200 / 150 = 8.0 h; annualised 8.0 x 365 / 92 = 31.7391 -> good (threshold 30/20/16); C = 1, quality 100
  K2 same, B undeclared: required person-months 100x3 + 50x3 = 450, covered 300 -> C = 0.6667 < 0.8
     warn (PO-6 open, shipped): value 8.0 kept, NO colour (status_reason low_coverage), quality 66.7
     enforce (config): insufficient_data, value null, quality 66.7
  K3 Q2 2026 (91 days): A 1,200 h complete x 3 months; B 90 h, Apr + May complete, Jun undeclared
     covered 300 + 50x2 = 400 -> C = 0.8889 >= 0.8 -> colour kept, quality 88.9, note "1 entity-month(s) not covered"
     value today 1,290 / 150 = 8.6 (the covered-subset value 9.675 of the spec is the enforce-mode rule, open item)
  K9 warn, nothing declared: K2 input -> 8.0 kept, quality <= 50 (C = 0 -> 0.0), note, no colour
  K6 KPI-003 / 053 A March 2026: 100 persons, planned 18,000 h (timesheet complete), leave feed undeclared
     -> today 0.0 % "green"; warn: 0.0 without colour (low_coverage); enforce: insufficient_data
  K7 same, leave complete: absence 900 h (sick 200 + unexcused 100 + annual 600) -> KPI-003 900/18,000 = 5.0 %
     (good, 7/9/11), KPI-053 300/18,000 = 1.6667 % (good, 2.5/3.5/5.0) - unchanged by the gate
  K4 KPI-014A project P October 2026: 90,000 h worked (timesheet complete), safety complete + confirmed_empty
     -> 0.0 real zero, no colour because of WP-2 R1 (low_exposure, 90,000 < 1,000,000 h)
  K5 same, safety undeclared -> warn: 0.0 without colour, reason low_coverage (wins over low_exposure);
     enforce (HR Director recommends safety=enforce): insufficient_data
  Fatality (WP-6 review condition 3): September 2026, 1 fatality, 90,000 h, safety undeclared -> warn 1/90,000 x 1e6
     = 11.1111 without colour; enforce insufficient_data - the fatality alert stays in both modes.
"""
from datetime import date

import pytest
from sqlalchemy import delete, func, select

from app.analytics import coverage as gate
from app.analytics import engine
from app.analytics.calculators import Scope
from app.analytics.catalog import KPI_CATALOG, REQUIRED_FEEDS
from app.core.config import get_settings
from app.models import (
    Assignment,
    AuditLog,
    DataFeedCoverage,
    EmploymentRelationship,
    LeaveRecord,
    LegalEntity,
    Person,
    Project,
    SafetyEvent,
    Tenant,
    Timesheet,
    TrainingRecord,
    User,
    UserRoleAssignment,
)
from app.security.access import AccessContext
from app.security.auth import hash_password
from app.seed.reference import seed_permissions_and_roles, seed_reference
from app.services import coverage as cov

D = date
ADMIN = "cx.admin@covx.local"
SUB_B = "cx.b@covx.local"  # CONTRACTOR_MANAGER on B: import.write + hse.read on B only
HRMAN_A = "cx.hrman@covx.local"  # HR_MANAGER on A: import.write, no hse.read
Q3 = (D(2026, 7, 1), D(2026, 9, 30))
Q2 = (D(2026, 4, 1), D(2026, 6, 30))
MAR = (D(2026, 3, 1), D(2026, 3, 31))
OCT = (D(2026, 10, 1), D(2026, 10, 31))
SEP = (D(2026, 9, 1), D(2026, 9, 30))


@pytest.fixture(scope="module")
def cx(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="COVX", name="Feed coverage test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        a = LegalEntity(tenant_id=t.id, code="CX-A", name="CX A", entity_type="main_contractor")
        b = LegalEntity(tenant_id=t.id, code="CX-B", name="CX B", entity_type="subcontractor")
        db.add_all([a, b])
        db.flush()
        p = Project(tenant_id=t.id, code="CX-P", name="CX P", owner_entity_id=a.id)
        db.add(p)
        db.flush()
        emps = {"A": [], "B": []}
        for code, ent, n in (("A", a, 100), ("B", b, 50)):
            for i in range(n):
                person = Person(tenant_id=t.id, person_code=f"CX{code}{i:03d}", first_name="T", last_name=f"{code}{i}")
                db.add(person)
                db.flush()
                e = EmploymentRelationship(tenant_id=t.id, person_id=person.id, legal_entity_id=ent.id,
                                           employee_number=f"{code}{i:03d}", start_date=D(2025, 1, 1))
                db.add(e)
                db.flush()
                emps[code].append(e)
        for e in emps["A"]:
            db.add(Assignment(tenant_id=t.id, employment_id=e.id, project_id=p.id, valid_from=D(2025, 1, 1)))
            db.add(TrainingRecord(tenant_id=t.id, employment_id=e.id, course_code="Q3", course_name="x",
                                  category="technical", hours=12, completed_on=D(2026, 8, 10)))  # K1: 1,200 h
            db.add(TrainingRecord(tenant_id=t.id, employment_id=e.id, course_code="Q2", course_name="x",
                                  category="technical", hours=12, completed_on=D(2026, 5, 11)))  # K3: 1,200 h
            db.add(Timesheet(tenant_id=t.id, employment_id=e.id, project_id=p.id, work_date=D(2026, 3, 2),
                             planned_hours=180, worked_hours=180))  # K6/K7: 18,000 planned
            for day in (D(2026, 9, 7), D(2026, 10, 5)):  # K4/K5 + fatality: 90,000 h per month
                db.add(Timesheet(tenant_id=t.id, employment_id=e.id, project_id=p.id, work_date=day,
                                 planned_hours=900, worked_hours=900))
        for e in emps["B"][:9]:  # K3: 9 x 10 h = 90 h in April
            db.add(TrainingRecord(tenant_id=t.id, employment_id=e.id, course_code="Q2B", course_name="x",
                                  category="technical", hours=10, completed_on=D(2026, 4, 15)))
        for i, (lt, h) in enumerate([("sick", 10)] * 20 + [("unexcused", 10)] * 10 + [("annual", 10)] * 60):  # K7
            db.add(LeaveRecord(tenant_id=t.id, employment_id=emps["A"][i].id, leave_type=lt, start_date=D(2026, 3, 3),
                               end_date=D(2026, 3, 3), hours=h))
        db.add(SafetyEvent(tenant_id=t.id, legal_entity_id=a.id, project_id=p.id, event_date=D(2026, 9, 9),
                           event_type="fatality", lost_days=0))
        pw = hash_password("Demo!2345")
        users = {}
        for email, role, ent in ((ADMIN, "SYSTEM_ADMIN", None), (SUB_B, "CONTRACTOR_MANAGER", b),
                                 (HRMAN_A, "HR_MANAGER", a)):
            u = User(tenant_id=t.id, email=email, full_name=email, password_hash=pw, locale="en")
            db.add(u)
            db.flush()
            users[email] = u.id
            db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role], scope_type="legal_entity" if ent else "tenant",
                                      legal_entity_id=ent.id if ent else None))
        db.commit()
        from app.analytics import summary

        summary.refresh(db, t.id)
        return {"tenant_id": t.id, "A": a.id, "B": b.id, "P": p.id, "users": users}


def _ctx(cx) -> AccessContext:
    return AccessContext(user_id=None, tenant_id=cx["tenant_id"], email="t", locale="en",
                         grants={"kpi.read": None, "cost.read": None})


def _declare(db, cx, rows: list[tuple]) -> None:
    """Replace the tenant's coverage with exactly `rows` = (feed, entity key, first, last, completeness[, extra])."""
    db.execute(delete(DataFeedCoverage).where(DataFeedCoverage.tenant_id == cx["tenant_id"]))
    for feed, ent, first, last, completeness, *extra in rows:
        cov.declare(db, tenant_id=cx["tenant_id"], user_id=None, basis="manual_declaration", decl={
            "feed_type": feed, "period_start": first.isoformat(), "period_end": last.isoformat(),
            "legal_entity_ids": [cx[ent]], "project_ids": [None], "completeness": completeness,
            "coverage_ratio": None, "confirmed_empty": bool(extra and extra[0].get("confirmed_empty")),
            "attributes": None, "planned_on_leave_days": None, "note": "test"})
    engine.mark_stale_from(db, cx["tenant_id"], D(2020, 1, 1))
    db.commit()


def _ev(db, cx, code, window, **kw):
    db.info.pop("kpi_memo", None)
    return engine.evaluate(db, _ctx(cx), code, *window, **kw)


@pytest.fixture()
def mode(monkeypatch):
    def set_mode(value: str):
        monkeypatch.setattr(get_settings(), "coverage_modes", value)
    return set_mode


# ------------------------------------------------------------------ K1 / K2 / K3 / K9 (KPI-008)


def test_training_true_zero_vs_missing_feed(db, cx, mode):
    """K1 (real zero: B complete + confirmed_empty) and K2 (B undeclared) in warn and enforce mode."""
    both = [("employment", "A", D(2026, 7, 1), D(2026, 9, 30), "complete"),
            ("employment", "B", D(2026, 7, 1), D(2026, 9, 30), "complete"),
            ("training", "A", D(2026, 7, 1), D(2026, 9, 30), "complete")]
    _declare(db, cx, [*both, ("training", "B", D(2026, 7, 1), D(2026, 9, 30), "complete", {"confirmed_empty": True})])
    k1 = _ev(db, cx, "KPI-008", Q3)
    assert k1["value"] == pytest.approx(8.0) and k1["annualized_value"] == pytest.approx(31.7391, abs=1e-4)
    assert k1["threshold_status"] == "good" and k1["status_reason"] is None and k1["quality_score"] == 100.0
    assert k1["lineage"]["coverage"]["feeds"] == {"employment": 1.0, "training": 1.0}

    _declare(db, cx, both)  # K2: B's training feed never declared
    k2 = _ev(db, cx, "KPI-008", Q3)
    assert k2["status"] == "ok" and k2["value"] == pytest.approx(8.0)  # warn: value stays
    assert k2["threshold_status"] is None and k2["status_reason"] == "low_coverage"
    assert k2["quality_score"] == pytest.approx(66.7) and "coverage below the minimum" in k2["note"]
    assert k2["lineage"]["coverage"]["feeds"]["training"] == pytest.approx(0.6667, abs=1e-4)
    assert k2["lineage"]["coverage"]["uncovered_entity_months"] == 3

    mode("default=warn,training=enforce")  # PO-6 decided for training = configuration change only
    k2e = _ev(db, cx, "KPI-008", Q3)
    assert k2e["status"] == "insufficient_data" and k2e["value"] is None and k2e["annualized_value"] is None
    assert k2e["quality_score"] == pytest.approx(66.7) and k2e["status_reason"] is None
    assert "not a zero" in k2e["note"]


def test_partial_coverage_above_minimum_keeps_colour(db, cx, mode):
    """K3: C = 400/450 = 0.8889 >= 0.8. Warn keeps the value and colour, caps quality, names the gap."""
    _declare(db, cx, [("employment", "A", D(2026, 4, 1), D(2026, 6, 30), "complete"),
                      ("employment", "B", D(2026, 4, 1), D(2026, 6, 30), "complete"),
                      ("training", "A", D(2026, 4, 1), D(2026, 6, 30), "complete"),
                      ("training", "B", D(2026, 4, 1), D(2026, 5, 31), "complete")])
    r = _ev(db, cx, "KPI-008", Q2)
    assert r["value"] == pytest.approx(8.6) and r["annualized_value"] == pytest.approx(8.6 * 365 / 91, abs=1e-3)
    assert r["threshold_status"] == "good" and r["status_reason"] is None
    assert r["quality_score"] == pytest.approx(88.9) and "1 entity-month(s) not covered" in r["note"]
    assert r["lineage"]["coverage"]["feeds"]["training"] == pytest.approx(0.8889, abs=1e-4)
    # enforce has no covered-subset recomputation yet (WP-6 K3 9.675 h): it withholds the colour instead
    mode("default=enforce")
    e = _ev(db, cx, "KPI-008", Q2)
    assert e["value"] == pytest.approx(8.6) and e["threshold_status"] is None and e["status_reason"] == "low_coverage"


def test_coverage_enforcement_warn_mode_caps_quality(db, cx):
    """K9: nothing declared, warn -> value 8.0 kept, quality <= 50, note, no colour."""
    _declare(db, cx, [])
    r = _ev(db, cx, "KPI-008", Q3)
    assert r["value"] == pytest.approx(8.0) and r["quality_score"] <= 50
    assert r["threshold_status"] is None and r["status_reason"] == "low_coverage"
    assert "not covered or not declared" in r["note"]


# ------------------------------------------------------------------ K6 / K7 (KPI-003 / 053)


def test_absenteeism_missing_leave_feed_insufficient(db, cx, mode):
    ts = ("timesheet", "A", D(2026, 3, 1), D(2026, 3, 31), "complete")
    _declare(db, cx, [ts])  # K6: leave feed undeclared - the absence rows exist but nobody vouched for them
    for code in ("KPI-003", "KPI-053"):
        r = _ev(db, cx, code, MAR, entity_ids={cx["A"]})
        assert r["status"] == "ok" and r["threshold_status"] is None and r["status_reason"] == "low_coverage", code
    mode("default=warn,leave=enforce")
    r = _ev(db, cx, "KPI-003", MAR, entity_ids={cx["A"]})
    assert r["status"] == "insufficient_data" and r["value"] is None
    mode("default=warn")

    _declare(db, cx, [ts, ("leave", "A", D(2026, 3, 1), D(2026, 3, 31), "complete")])  # K7
    k3 = _ev(db, cx, "KPI-003", MAR, entity_ids={cx["A"]})
    k53 = _ev(db, cx, "KPI-053", MAR, entity_ids={cx["A"]})
    assert k3["value"] == pytest.approx(5.0) and k3["threshold_status"] == "good" and k3["status_reason"] is None
    assert k53["value"] == pytest.approx(1.6667, abs=1e-4) and k53["threshold_status"] == "good"
    assert k3["note"] is None and k3["quality_score"] == 100.0


def test_missing_leave_feed_never_green_zero(db, cx):
    """K6 'today %0 / green': A has October timesheets but no leave rows. Without a leave declaration the 0.0 is
    shown without colour; with 'complete' + confirmed_empty it is a real, coloured zero."""
    _declare(db, cx, [("timesheet", "A", D(2026, 10, 1), D(2026, 10, 31), "complete")])
    r = _ev(db, cx, "KPI-053", OCT, entity_ids={cx["A"]})
    assert r["value"] == 0.0 and r["threshold_status"] is None and r["status_reason"] == "low_coverage"
    _declare(db, cx, [("timesheet", "A", D(2026, 10, 1), D(2026, 10, 31), "complete"),
                      ("leave", "A", D(2026, 10, 1), D(2026, 10, 31), "complete", {"confirmed_empty": True})])
    r = _ev(db, cx, "KPI-053", OCT, entity_ids={cx["A"]})
    assert r["value"] == 0.0 and r["threshold_status"] == "good" and r["status_reason"] is None  # real zero


# ------------------------------------------------------------------ K4 / K5 + fatality (KPI-014A/B)


def test_safety_rate_missing_feed_insufficient_not_zero(db, cx, mode):
    ts = ("timesheet", "A", D(2026, 9, 1), D(2026, 10, 31), "complete")
    _declare(db, cx, [ts, ("safety", "A", D(2026, 10, 1), D(2026, 10, 31), "complete", {"confirmed_empty": True})])
    k4 = _ev(db, cx, "KPI-014A", OCT, project_id=cx["P"])
    assert k4["value"] == 0.0 and k4["breakdown"]["hours_worked"] == 90000.0
    assert k4["status_reason"] == "low_exposure" and k4["threshold_status"] is None  # real zero, WP-2 R1

    _declare(db, cx, [ts])  # K5
    k5 = _ev(db, cx, "KPI-014A", OCT, project_id=cx["P"])
    assert k5["value"] == 0.0 and k5["status_reason"] == "low_coverage"  # wins over low_exposure
    assert k5["lineage"]["coverage"]["min_feed_coverage"] == 0.9
    mode("default=warn,safety=enforce")  # HR Director's PO-6 recommendation
    k5e = _ev(db, cx, "KPI-014A", OCT, project_id=cx["P"])
    assert k5e["status"] == "insufficient_data" and k5e["value"] is None


def test_coverage_gate_never_hides_fatality_alert(db, cx, mode):
    _declare(db, cx, [("timesheet", "A", D(2026, 9, 1), D(2026, 10, 31), "complete")])  # safety undeclared
    w = _ev(db, cx, "KPI-014A", SEP, project_id=cx["P"])
    assert w["value"] == pytest.approx(11.1111, abs=1e-4) and w["status_reason"] == "low_coverage"
    assert w["alerts"] == [{"type": "fatality", "count": 1, "by_month": {"2026-09": 1}}]
    mode("default=warn,safety=enforce")
    for code in ("KPI-014A", "KPI-014B"):
        e = _ev(db, cx, code, SEP, project_id=cx["P"])
        assert e["status"] == "insufficient_data" and e["value"] is None
        assert e["alerts"] == [{"type": "fatality", "count": 1, "by_month": {"2026-09": 1}}]


def test_mode_change_is_part_of_cache_key(db, cx, mode):
    """PO-6 is a configuration change: the same data version must not serve a result cached under the other mode."""
    _declare(db, cx, [])
    assert _ev(db, cx, "KPI-016", (D(2026, 8, 1), D(2026, 8, 31)))["status_reason"] == "low_coverage"
    mode("default=enforce")
    assert _ev(db, cx, "KPI-016", (D(2026, 8, 1), D(2026, 8, 31)))["status"] == "insufficient_data"
    mode("default=off")
    off = _ev(db, cx, "KPI-016", (D(2026, 8, 1), D(2026, 8, 31)))
    assert off["value"] == 150 and off["status_reason"] is None and "coverage" not in off["lineage"]


def test_project_scope_uses_project_declaration_then_entity_fallback(db, cx):
    """ADR-017 / project filter: a declaration for the project counts; otherwise the whole-entity row is used."""
    s = Scope(cx["tenant_id"], frozenset({cx["A"]}), project_id=cx["P"])
    _declare(db, cx, [])
    cov.declare(db, tenant_id=cx["tenant_id"], user_id=None, basis="manual_declaration", decl={
        "feed_type": "safety", "period_start": "2026-10-01", "period_end": "2026-10-31", "legal_entity_ids": [cx["A"]],
        "project_ids": [cx["P"]], "completeness": "complete", "coverage_ratio": None, "confirmed_empty": False,
        "attributes": None, "planned_on_leave_days": None, "note": None})
    db.commit()
    db.info.pop("coverage_memo", None)
    assert gate.feed_coverage(db, s, *OCT, ["safety"])["safety"]["ratio"] == 1.0
    # whole-entity scope needs the entity-level row: the project row alone does not vouch for other projects
    db.info.pop("coverage_memo", None)
    assert gate.feed_coverage(db, Scope(cx["tenant_id"], frozenset({cx["A"]})), *OCT, ["safety"])["safety"]["ratio"] == 0
    # project-limited entity (ADR-017): its allowed project's row counts
    lim = Scope(cx["tenant_id"], frozenset({cx["A"]}), project_limits=((cx["A"], (cx["P"],)),))
    db.info.pop("coverage_memo", None)
    assert gate.feed_coverage(db, lim, *OCT, ["safety"])["safety"]["ratio"] == 1.0


def test_partial_without_ratio_counts_half(db, cx):
    _declare(db, cx, [("employment", "A", D(2026, 8, 1), D(2026, 8, 31), "partial")])
    db.info.pop("coverage_memo", None)
    c = gate.feed_coverage(db, Scope(cx["tenant_id"], frozenset({cx["A"]})), D(2026, 8, 1), D(2026, 8, 31),
                           ["employment"])
    assert c["employment"]["ratio"] == 0.5


# ------------------------------------------------------------------ configuration, parameters, catalog


def test_mode_parsing_and_validation():
    assert set(gate.parse_modes("default=warn").values()) == {"warn"}
    m = gate.parse_modes("default=warn, safety=enforce")
    assert m["safety"] == "enforce" and m["leave"] == "warn"
    for bad in ("safety=strict", "payroll=warn"):
        with pytest.raises(ValueError):
            gate.parse_modes(bad)
    assert get_settings().coverage_modes == "default=warn"  # shipped default: PO-6 not decided


def test_coverage_parameters_validated():
    engine.validate_parameters({"required_feeds": ["leave", "timesheet"], "min_feed_coverage": 0.8}, "absenteeism_rate")
    for bad in ({"required_feeds": ["payroll"]}, {"required_feeds": []}, {"required_feeds": ["leave", "leave"]},
                {"min_feed_coverage": 0}, {"min_feed_coverage": 1.5}, {"min_feed_coverage": True}):
        with pytest.raises(engine.KpiError):
            engine.validate_parameters(bad, "absenteeism_rate")


def test_catalog_required_feeds_follow_spec_table():
    by_code = {k["code"]: k for k in KPI_CATALOG}
    for code, feeds in REQUIRED_FEEDS.items():
        p = by_code[code]["formula"]["parameters"]
        assert p["required_feeds"] == list(feeds), code
        assert p["min_feed_coverage"] == (0.9 if "safety" in feeds else 0.8), code
    assert by_code["KPI-003"]["formula"]["parameters"]["required_feeds"] == ["timesheet", "leave"]
    assert "required_feeds" not in by_code["KPI-009"]["formula"]["parameters"]  # no feed rule in the spec table


# ------------------------------------------------------------------ imports and the manual API

LEAVE_HDR = "Sicil;Firma;İzin Türü;Başlangıç;Bitiş;Saat"


def _leave_csv(rows):
    return (LEAVE_HDR + "\n" + "".join(";".join(r) + "\n" for r in rows)).encode()


def _upload(client, auth, content, coverage=None, user=ADMIN, entity_type="leave", name="izin.csv"):
    import json

    data = {"entity_type": entity_type}
    if coverage is not None:
        data["coverage"] = json.dumps(coverage)
    return client.post("/api/v1/imports", headers=auth(user), files={"file": (name, content)}, data=data)


def _rows(db, cx, feed):
    db.expire_all()
    return {(r.legal_entity_id, r.period_month): r for r in db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == cx["tenant_id"], DataFeedCoverage.feed_type == feed)).scalars()}


def test_coverage_declared_on_commit_per_entity_month(client, auth, db, cx):
    _declare(db, cx, [])
    dv0 = db.execute(select(Tenant.data_version).where(Tenant.id == cx["tenant_id"])).scalar()
    decl = {"period_start": "2026-01-01", "period_end": "2026-02-28", "legal_entity_codes": ["CX-A"],
            "completeness": "complete"}
    content = _leave_csv([["A050", "CX-A", "Yıllık izin", "2026-01-12", "2026-01-12", "9"],
                          ["A051", "CX-A", "Yıllık izin", "2026-05-12", "2026-05-12", "9"]])  # outside the period
    up = _upload(client, auth, content, decl)
    assert up.status_code == 201, up.text
    assert up.json()["coverage"]["legal_entity_ids"] == [cx["A"]]
    bid = up.json()["batch_id"]
    v = client.post(f"/api/v1/imports/{bid}/validate", json={}, headers=auth(ADMIN)).json()
    assert v["valid_rows"] == 2
    rep = client.get(f"/api/v1/imports/{bid}/errors.csv", headers=auth(ADMIN)).text
    assert "row_outside_declared_coverage" in rep and rep.count("row_outside_declared_coverage") == 1
    c = client.post(f"/api/v1/imports/{bid}/commit", headers=auth(ADMIN)).json()
    assert c["status"] == "committed" and c["coverage"]["completeness"] == "complete"
    rows = _rows(db, cx, "leave")
    assert set(rows) == {(cx["A"], D(2026, 1, 1)), (cx["A"], D(2026, 2, 1))}  # the May row creates no coverage
    jan, feb = rows[(cx["A"], D(2026, 1, 1))], rows[(cx["A"], D(2026, 2, 1))]
    assert jan.completeness == feb.completeness == "complete" and jan.basis == "import_batch"
    assert jan.confirmed_empty is False and feb.confirmed_empty is True and jan.import_batch_id == bid
    assert db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.tenant_id == cx["tenant_id"], AuditLog.action == "coverage.declare",
        AuditLog.details["import_batch_id"].as_integer() == bid)).scalar() == 1
    assert db.execute(select(Tenant.data_version).where(Tenant.id == cx["tenant_id"])).scalar() > dv0


def test_header_only_file_confirms_empty_feed(client, auth, db, cx):
    _declare(db, cx, [])
    empty = (LEAVE_HDR + "\n").encode()
    assert _upload(client, auth, empty).status_code == 400  # no declaration: an empty file proves nothing
    decl = {"period_start": "2026-03-01", "period_end": "2026-03-31", "legal_entity_codes": ["CX-B"],
            "completeness": "partial"}
    assert _upload(client, auth, empty, decl).status_code == 400  # only 'complete' confirms an empty month
    up = _upload(client, auth, empty, {**decl, "completeness": "complete"})
    assert up.status_code == 201, up.text
    bid = up.json()["batch_id"]
    client.post(f"/api/v1/imports/{bid}/validate", json={}, headers=auth(ADMIN))
    assert client.post(f"/api/v1/imports/{bid}/commit", headers=auth(ADMIN)).json()["status"] == "committed"
    r = _rows(db, cx, "leave")[(cx["B"], D(2026, 3, 1))]
    assert r.completeness == "complete" and r.confirmed_empty is True
    # employment is not an event feed: a header-only employment file is rejected even with a declaration
    emp_hdr = "Sicil;Ad;Soyad;Firma;İşe Giriş\n".encode()
    assert _upload(client, auth, emp_hdr, {**decl, "completeness": "complete"}, entity_type="employment",
                   name="emp.csv").status_code == 400


def test_undeclared_import_writes_partial_and_never_downgrades(client, auth, db, cx):
    _declare(db, cx, [("leave", "A", D(2026, 6, 1), D(2026, 6, 30), "complete")])
    content = _leave_csv([["A060", "CX-A", "Yıllık izin", "2026-06-15", "2026-06-15", "9"],
                          ["A061", "CX-A", "Yıllık izin", "2026-07-15", "2026-07-15", "9"]])
    bid = _upload(client, auth, content).json()["batch_id"]
    client.post(f"/api/v1/imports/{bid}/validate", json={}, headers=auth(ADMIN))
    client.post(f"/api/v1/imports/{bid}/commit", headers=auth(ADMIN))
    rows = _rows(db, cx, "leave")
    assert rows[(cx["A"], D(2026, 6, 1))].completeness == "complete"  # kept
    jul = rows[(cx["A"], D(2026, 7, 1))]
    assert jul.completeness == "partial" and jul.coverage_ratio is None and jul.import_batch_id == bid


def test_invalid_or_out_of_scope_declarations_rejected(client, auth, db, cx):
    content = _leave_csv([["B001", "CX-B", "Yıllık izin", "2026-03-10", "2026-03-10", "9"]])
    half = {"period_start": "2026-03-05", "period_end": "2026-03-31", "legal_entity_codes": ["CX-B"],
            "completeness": "complete"}
    assert _upload(client, auth, content, half).status_code == 422  # whole months only
    a_decl = {**half, "period_start": "2026-03-01", "legal_entity_codes": ["CX-A"]}
    assert _upload(client, auth, content, a_decl, user=SUB_B).status_code == 403  # B's manager cannot vouch for A
    bad_ratio = {**half, "period_start": "2026-03-01", "coverage_ratio": 0.5}
    assert _upload(client, auth, content, bad_ratio).status_code == 422  # ratio only with 'partial'


def test_manual_declaration_permissions_note_and_audit(client, auth, db, cx):
    _declare(db, cx, [])
    body = {"feed_type": "training", "legal_entity_id": cx["B"], "period_start": "2026-08-01",
            "period_end": "2026-08-31", "completeness": "complete"}
    r = client.post("/api/v1/coverage", json=body, headers=auth(SUB_B))
    assert r.status_code == 201 and r.json()["declared_rows"] == 1
    denied = client.post("/api/v1/coverage", json={**body, "legal_entity_id": cx["A"]}, headers=auth(SUB_B))
    assert denied.status_code == 403
    assert db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.tenant_id == cx["tenant_id"], AuditLog.action == "coverage.declare",
        AuditLog.outcome == "denied")).scalar() >= 1
    assert client.post("/api/v1/coverage", json={**body, "completeness": "none"},
                       headers=auth(SUB_B)).status_code == 422  # 'none' needs a reason
    ok = client.post("/api/v1/coverage", json={**body, "completeness": "none", "note": "LMS export not delivered"},
                     headers=auth(SUB_B))
    assert ok.status_code == 201
    assert client.post("/api/v1/coverage", json={**body, "confirmed_empty": True},
                       headers=auth(SUB_B)).status_code == 422  # confirmed_empty needs a reason too
    # safety needs hse.read on the employer: the HR manager of A has import.write but not hse.read
    safety = {**body, "feed_type": "safety", "legal_entity_id": cx["A"]}
    assert client.post("/api/v1/coverage", json=safety, headers=auth(HRMAN_A)).status_code == 403
    assert client.post("/api/v1/coverage", json={**safety, "attributes": {"includes_near_miss": False}},
                       headers=auth(ADMIN)).status_code == 201
    assert client.post("/api/v1/coverage", json={**body, "period_end": "2099-12-31"},
                       headers=auth(ADMIN)).status_code == 422  # no future months
    m = client.get("/api/v1/coverage?period_start=2026-08-01&period_end=2026-08-31", headers=auth(SUB_B)).json()
    assert [e["code"] for e in m["legal_entities"]] == ["CX-B"]  # whole-entity import scope only
    assert [(c["feed_type"], c["state"]) for c in m["cells"]] == [("training", "none")]
    full = client.get("/api/v1/coverage?period_start=2026-08-01&period_end=2026-08-31&feed_types=safety",
                      headers=auth(ADMIN)).json()
    assert full["cells"][0]["attributes"] == {"includes_near_miss": False}


def test_coverage_change_marks_results_stale(client, auth, db, cx):
    _declare(db, cx, [])
    h = auth(ADMIN)
    q = {"period_start": "2026-08-01", "period_end": "2026-08-31"}
    before = client.get("/api/v1/kpi/evaluate/KPI-016", params=q, headers=h).json()
    assert before["status_reason"] == "low_coverage"
    for ent in ("A", "B"):
        assert client.post("/api/v1/coverage", headers=h, json={
            "feed_type": "employment", "legal_entity_id": cx[ent], "period_start": "2026-08-01",
            "period_end": "2026-08-31", "completeness": "complete"}).status_code == 201
    after = client.get("/api/v1/kpi/evaluate/KPI-016", params=q, headers=h).json()
    assert after["value"] == 150 and after["status_reason"] is None and after["quality_score"] in (None, 100.0)


def test_period_close_lists_coverage_gaps_without_blocking(client, auth, db, cx):
    _declare(db, cx, [])
    h = auth(ADMIN)
    r = client.post("/api/v1/kpi/periods/2026-02-01/close", headers=h)
    assert r.status_code == 200, r.text
    try:
        gaps = r.json()["coverage_gaps"]
        assert {"feed_type": "leave", "legal_entity_id": cx["A"], "legal_entity_code": "CX-A",
                "state": "undeclared"} in gaps
        assert {g["feed_type"] for g in gaps} >= {"employment", "leave", "timesheet", "safety", "training", "cost"}
    finally:
        assert client.post("/api/v1/kpi/periods/2026-02-01/reopen", json={"reason": "test cleanup"},
                           headers=h).status_code == 200
