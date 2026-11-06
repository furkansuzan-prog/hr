"""WP-2 R1/R2 (HSE safety-rate safeguards) and WP-5 section 3.6 (KPI-002 12-month rule) - ADR-018.

Hand-calculated expectations come from docs/hr/hse/WP-2_safety_rate_safeguards.md section 4 (+ HR Director review:
TRIR minimum exposure 500,000 h, so example H is no longer coloured) and docs/hr/data/WP-5_turnover_definitions.md
section 4.3. Thresholds (D-5): KPI-014A 1.5/3.0/5.0, KPI-014B 1.0/2.0/3.5 (lower_better), KPI-002 75/65/55
(higher_better).

Dedicated tenant SAFX. Every WP-2 scenario is its own project inside entity SE, so the project scope isolates its
hours and events; timesheet rows are 500 h each (synthetic: only the totals matter).

  scenario  window          hours                      events                         expected
  A         Mar 2026        10 p x 16 d x 500 =   80,000  1 LTI                       LTIFR 12.5, no colour
  B         R12             15 p x 140 d x 500 = 1,050,000 2 LTI                       LTIFR 1.9048 good
  C         R12             10 p x 200 d x 500 - 1 = 999,999  2 LTI                    LTIFR 2.0000, no colour
  D         R12             10 p x 200 d x 500 = 1,000,000 3 LTI                       LTIFR 3.0 warning
  F         Mar 2026        12 p x 30 d x 500 =  180,000  1 MTC                        TRIR 1.1111, no colour
  G         R12             15 p x 280 d x 500 = 2,100,000 5 LTI + 5 RWC + 5 MTC       TRIR 1.4286 good
  H         R12             10 p x 42 d x 500 =  210,000  4 recordable                 TRIR 3.8095, no colour
  I         Mar 2026        10 p x 16 d x 500 =   80,000  1 fatality                   LTIFR 12.5 no colour + alert
  J         Mar 2026         4 p x 16 d x 500 =   32,000  1 fatality                   suppressed + alert
  Frozen    Apr 2026  entity SF: 10 p x 16 d x 500 = 80,000  1 LTI + 1 fatality        frozen: 25.0, no colour + alert
R12 = 2025-11-01..2026-10-31 (365 days).

KPI-002: entity RA 200 persons, 72 leave between 2025-11-08 and 2026-10-30 -> 128/200 = 64.0 % on any window that
starts on/before 2025-11-07 and ends 2026-10-31. Entity RB 200 persons, 6 leave on 2026-10-15 -> Oct 2026 194/200 = 97.0.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from app.models import (
    EmploymentRelationship,
    KpiDefinition,
    KpiFormulaVersion,
    LegalEntity,
    Person,
    Project,
    SafetyEvent,
    Tenant,
    Timesheet,
    User,
    UserRoleAssignment,
)
from app.security.auth import hash_password
from app.seed.reference import seed_permissions_and_roles, seed_reference, sync_kpi_catalog
from tests.factories import declare_all_feeds

ADMIN = "sx.admin@safx.local"
EXEC = "sx.exec@safx.local"  # READONLY_EXECUTIVE: kpi.read without hse.read (WP-2 Q7: still sees the fatality alert)
MAR = {"period_start": "2026-03-01", "period_end": "2026-03-31"}
APR = {"period_start": "2026-04-01", "period_end": "2026-04-30"}
R12 = {"period_start": "2025-11-01", "period_end": "2026-10-31"}
D = date

# project code -> (persons, days, first day, events, hours to subtract from the last row)
SCENARIOS = {
    "A": (10, 16, D(2026, 3, 1), ["lost_time_injury"], 0),
    "B": (15, 140, D(2025, 11, 1), ["lost_time_injury"] * 2, 0),
    "C": (10, 200, D(2025, 11, 1), ["lost_time_injury"] * 2, 1),
    "D": (10, 200, D(2025, 11, 1), ["lost_time_injury"] * 3, 0),
    "F": (12, 30, D(2026, 3, 1), ["medical_treatment"], 0),
    "G": (15, 280, D(2025, 11, 1), ["lost_time_injury"] * 5 + ["restricted_work"] * 5 + ["medical_treatment"] * 5, 0),
    "H": (10, 42, D(2025, 11, 1), ["lost_time_injury", "restricted_work", "medical_treatment", "medical_treatment"], 0),
    "I": (10, 16, D(2026, 3, 1), ["fatality"], 0),
    "J": (4, 16, D(2026, 3, 1), ["fatality"], 0),
}


@pytest.fixture(scope="module")
def sx(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="SAFX", name="Safety exposure test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        ents = {}
        for code in ("SE", "SF", "RA", "RB"):
            ents[code] = LegalEntity(tenant_id=t.id, code=f"SX-{code}", name=f"SAFX {code}",
                                     entity_type="main_contractor")
            db.add(ents[code])
        db.flush()
        n = 0

        def employ(ent: str, count: int, ends: list[date | None] | None = None) -> list[int]:
            nonlocal n
            out = []
            for i in range(count):
                n += 1
                p = Person(tenant_id=t.id, person_code=f"SX{n:05d}", first_name="Safx", last_name=f"T{n}")
                db.add(p)
                db.flush()
                end = ends[i] if ends and i < len(ends) else None
                e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ents[ent].id,
                                           start_date=D(2020, 1, 1), end_date=end,
                                           termination_category="voluntary" if end else None)
                db.add(e)
                db.flush()
                out.append(e.id)
            return out

        def timesheets(emp_ids, days, first, project_id=None, minus=0):
            rows = [{"tenant_id": t.id, "employment_id": e, "project_id": project_id, "work_date": first + timedelta(d),
                     "planned_hours": 500, "worked_hours": 500, "overtime_hours": 0, "night_hours": 0,
                     "status": "approved"} for e in emp_ids for d in range(days)]
            rows[-1]["worked_hours"] = 500 - minus
            db.execute(insert(Timesheet), rows)

        se = employ("SE", 15)
        projects = {}
        for code, (persons, days, first, events, minus) in SCENARIOS.items():
            pr = Project(tenant_id=t.id, code=f"SX-P{code}", name=f"SAFX {code}", owner_entity_id=ents["SE"].id)
            db.add(pr)
            db.flush()
            projects[code] = pr.id
            timesheets(se[:persons], days, first, pr.id, minus)
            for i, et in enumerate(events):
                db.add(SafetyEvent(tenant_id=t.id, legal_entity_id=ents["SE"].id, project_id=pr.id,
                                   event_date=first + timedelta(i % days), event_type=et, lost_days=0))
        timesheets(employ("SF", 10), 16, D(2026, 4, 1))
        for et in ("lost_time_injury", "fatality"):
            db.add(SafetyEvent(tenant_id=t.id, legal_entity_id=ents["SF"].id, event_date=D(2026, 4, 7), event_type=et,
                               lost_days=0))
        leave_days = [D(2025, 11, 8) + timedelta(days=i * 4) for i in range(72)]  # 2025-11-08 .. 2026-10-23
        assert leave_days[-1] < D(2026, 10, 31)
        employ("RA", 200, leave_days)
        employ("RB", 200, [D(2026, 10, 15)] * 6)

        pw = hash_password("Demo!2345")
        for email, role in ((ADMIN, "SYSTEM_ADMIN"), (EXEC, "READONLY_EXECUTIVE")):
            u = User(tenant_id=t.id, email=email, full_name=email.split("@")[0], password_hash=pw, locale="en")
            db.add(u)
            db.flush()
            db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role], scope_type="tenant"))
        declare_all_feeds(db, t.id)  # ADR-020: every feed complete
        db.commit()
        return {"tenant_id": t.id, "entities": {k: v.id for k, v in ents.items()}, "projects": projects}


def ev(client, auth, code, user=ADMIN, **params):
    r = client.get(f"/api/v1/kpi/evaluate/{code}", params=params, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def prj(sx, code, window):
    return {"project_id": sx["projects"][code], **window}


def ent(sx, code, window):
    return {"entity_ids": str(sx["entities"][code]), **window}


# ------------------------------------------------------------------ R1: minimum exposure (LTIFR)


def test_ltifr_no_colour_below_min_exposure(client, auth, sx):
    a = ev(client, auth, "KPI-014A", **prj(sx, "A", MAR))  # example A: 1 / 80,000 x 1,000,000
    assert a["status"] == "ok" and a["value"] == pytest.approx(12.5)
    assert a["threshold_status"] is None and a["status_reason"] == "low_exposure"
    assert a["formula"]["parameters"]["threshold_min_exposure_hours"] == 1_000_000
    c = ev(client, auth, "KPI-014A", **prj(sx, "C", R12))  # example C: 2 / 999,999 x 10^6 = 2.000002
    assert c["denominator"] == 999_999 and c["value"] == pytest.approx(2.0, abs=1e-4)
    assert c["threshold_status"] is None and c["status_reason"] == "low_exposure"


def test_ltifr_colour_at_exact_min_exposure(client, auth, sx):
    d = ev(client, auth, "KPI-014A", **prj(sx, "D", R12))  # example D: hours == H_min -> coloured; 3.0 == warning
    assert d["denominator"] == 1_000_000 and d["value"] == pytest.approx(3.0)
    assert d["threshold_status"] == "warning" and d["status_reason"] is None


def test_ltifr_rolling_12m_colour(client, auth, sx):
    b = ev(client, auth, "KPI-014A", **prj(sx, "B", R12))  # example B: 2 / 1,050,000 x 10^6 = 1.9048
    assert b["value"] == pytest.approx(1.9048, abs=1e-4)
    assert b["threshold_status"] == "good" and b["status_reason"] is None


# ------------------------------------------------------------------ R1: minimum exposure (TRIR, 500,000 h)


def test_trir_min_exposure_rule(client, auth, sx):
    f = ev(client, auth, "KPI-014B", **prj(sx, "F", MAR))  # F: 1 / 180,000 x 200,000
    assert f["value"] == pytest.approx(1.1111, abs=1e-4)
    assert f["threshold_status"] is None and f["status_reason"] == "low_exposure"
    assert f["formula"]["parameters"]["threshold_min_exposure_hours"] == 500_000
    g = ev(client, auth, "KPI-014B", **prj(sx, "G", R12))  # G: 15 / 2,100,000 x 200,000
    assert g["value"] == pytest.approx(1.4286, abs=1e-4)
    assert g["threshold_status"] == "good" and g["status_reason"] is None
    # H: 4 / 210,000 x 200,000 = 3.8095 would be critical; below 500,000 h (HR Director WP-2 Q2) -> no colour
    h = ev(client, auth, "KPI-014B", **prj(sx, "H", R12))
    assert h["value"] == pytest.approx(3.8095, abs=1e-4)
    assert h["threshold_status"] is None and h["status_reason"] == "low_exposure"


def test_event_counts_always_returned_with_rate(client, auth, sx):
    a = ev(client, auth, "KPI-014A", **prj(sx, "A", MAR))
    assert a["numerator"] == 1 and a["denominator"] == 80_000
    assert a["breakdown"]["events_by_type"] == {"lost_time_injury": 1}
    assert a["breakdown"]["hours_worked"] == 80_000
    h = ev(client, auth, "KPI-014B", **prj(sx, "H", R12))
    assert h["breakdown"]["events_by_type"] == {"lost_time_injury": 1, "restricted_work": 1, "medical_treatment": 2}


def test_small_scope_no_colour_option_c1(client, auth, sx):
    """Q1 = C1: a small scope never gets colour, whatever the window - no expanding window, no window label."""
    for window in ({"period_start": "2025-11-01", "period_end": "2025-11-30"}, R12,
                   {"period_start": "2023-11-01", "period_end": "2026-10-31"}):
        r = ev(client, auth, "KPI-014A", **prj(sx, "H", window))
        assert r["threshold_status"] is None and r["status_reason"] == "low_exposure"
        assert (r["period_start"], r["period_end"]) == (window["period_start"], window["period_end"])


# ------------------------------------------------------------------ R2: fatality alert


def _fatality(r):
    return [a for a in r["alerts"] if a["type"] == "fatality"]


def test_fatality_alert_independent_of_rate_status(client, auth, sx):
    i = ev(client, auth, "KPI-014A", **prj(sx, "I", MAR))  # example I: LTIFR 12.5, no colour, alert visible
    assert i["value"] == pytest.approx(12.5) and i["threshold_status"] is None
    assert _fatality(i) == [{"type": "fatality", "count": 1, "by_month": {"2026-03": 1}}]
    assert _fatality(ev(client, auth, "KPI-014B", **prj(sx, "I", MAR)))[0]["count"] == 1
    # no fatality in scope -> no alert
    assert ev(client, auth, "KPI-014A", **prj(sx, "A", MAR))["alerts"] == []
    assert _fatality(ev(client, auth, "KPI-014A", **prj(sx, "I", R12)))[0]["by_month"] == {"2026-03": 1}
    # non-safety KPIs carry an empty alert list and no reason when coloured normally
    r = ev(client, auth, "KPI-002", **ent(sx, "RA", R12))
    assert r["alerts"] == [] and r["status_reason"] is None


def test_fatality_alert_visible_when_rate_suppressed(client, auth, sx):
    j = ev(client, auth, "KPI-014A", **prj(sx, "J", MAR))  # example J: 4 persons -> suppressed, alert still shown
    assert j["status"] == "suppressed" and j["value"] is None and j["threshold_status"] is None
    assert j["status_reason"] is None
    assert _fatality(j) == [{"type": "fatality", "count": 1, "by_month": {"2026-03": 1}}]


def test_fatality_alert_visible_with_occupation_filter(client, auth, sx):
    r = ev(client, auth, "KPI-014A", occupation_group="office", **prj(sx, "I", MAR))
    assert r["status"] == "insufficient_data" and r["value"] is None
    assert _fatality(r)[0]["count"] == 1


def test_fatality_alert_visible_to_kpi_readers_without_hse_read(client, auth, sx):
    """HR Director WP-2 Q7: everyone who can see KPI-014A in that scope sees the alert (count + month)."""
    r = ev(client, auth, "KPI-014A", user=EXEC, **prj(sx, "I", MAR))
    assert _fatality(r)[0]["count"] == 1


def test_fatality_alert_tenant_scope_and_by_month(client, auth, sx):
    r = ev(client, auth, "KPI-014A", period_start="2026-03-01", period_end="2026-04-30")
    assert _fatality(r) == [{"type": "fatality", "count": 3, "by_month": {"2026-03": 2, "2026-04": 1}}]


def test_fatality_alert_has_no_person_fields(client, auth, sx):
    r = ev(client, auth, "KPI-014A", period_start="2026-03-01", period_end="2026-04-30")
    for a in r["alerts"]:
        assert set(a) == {"type", "count", "by_month"}
        assert all(re.fullmatch(r"\d{4}-\d{2}", m) for m in a["by_month"])  # month level, never a day


def test_frozen_period_keeps_low_exposure_and_live_alert(client, auth, sx):
    h = auth(ADMIN)
    assert client.post("/api/v1/kpi/periods/2026-04-01/close", headers=h).status_code == 200
    try:
        r = ev(client, auth, "KPI-014A", **ent(sx, "SF", APR))  # (1 LTI + 1 fatality) / 80,000 x 10^6
        assert r["frozen"] is True and r["value"] == pytest.approx(25.0)
        assert r["threshold_status"] is None and r["status_reason"] == "low_exposure"
        assert "_status_reason" not in r["lineage"]
        assert _fatality(r)[0]["count"] == 1
    finally:
        assert client.post("/api/v1/kpi/periods/2026-04-01/reopen", json={"reason": "test cleanup"},
                           headers=h).status_code == 200


def test_no_accident_free_day_counter():
    """WP-2 R2.7: the platform never shows a 'days without accident' / 'days since last fatality' counter."""
    root = Path(__file__).resolve().parents[2]
    pat = re.compile(r"kazas[ıi]z\s*g[üu]n|days?[\s_-]*(without|since)[\s_-]*(an?[\s_-]*|the[\s_-]*|last[\s_-]*)*"
                     r"(accident|incident|injur|lti|lost.?time|fatal)|accident[\s_-]*free", re.IGNORECASE)
    hits = []
    for base in (root / "backend" / "app", root / "frontend" / "src"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix in {".py", ".ts", ".tsx"} and pat.search(path.read_text(encoding="utf-8")):
                hits.append(str(path))
    assert hits == []


# ------------------------------------------------------------------ parameters


def test_min_exposure_parameter_validated():
    from app.analytics.engine import KpiError, validate_parameters

    validate_parameters({"threshold_min_exposure_hours": 500_000}, "trir")
    for bad in (0, -1, 1.5, "500000", True, 20_000_000):
        with pytest.raises(KpiError, match="threshold_min_exposure_hours"):
            validate_parameters({"threshold_min_exposure_hours": bad}, "ltifr")
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"threshold_min_exposure_hours": 500_000}, "retention_rate")


# ------------------------------------------------------------------ KPI-002 12-month rule (WP-5 R1-R3)


def test_retention_no_colour_below_360_days(client, auth, sx):
    r1 = ev(client, auth, "KPI-002", **ent(sx, "RA", R12))  # R1: 365 days, 128 / 200
    assert r1["value"] == pytest.approx(64.0) and r1["threshold_status"] == "warning" and r1["status_reason"] is None
    r2 = ev(client, auth, "KPI-002", **ent(sx, "RB", {"period_start": "2026-10-01", "period_end": "2026-10-31"}))
    assert r2["value"] == pytest.approx(97.0)  # R2: would be 'good' (false comfort) -> no colour
    assert r2["threshold_status"] is None and r2["status_reason"] == "short_window"
    r3a = ev(client, auth, "KPI-002", **ent(sx, "RA", {"period_start": "2025-11-06", "period_end": "2026-10-31"}))
    assert r3a["value"] == pytest.approx(64.0) and r3a["threshold_status"] == "warning"  # 360 days -> coloured
    r3b = ev(client, auth, "KPI-002", **ent(sx, "RA", {"period_start": "2025-11-07", "period_end": "2026-10-31"}))
    assert r3b["value"] == pytest.approx(64.0)  # 359 days -> no colour
    assert r3b["threshold_status"] is None and r3b["status_reason"] == "short_window"


def test_retention_never_annualised(client, auth, sx):
    for window in (R12, {"period_start": "2026-10-01", "period_end": "2026-10-31"}):
        r = ev(client, auth, "KPI-002", **ent(sx, "RB", window))
        assert r["annualized_value"] is None
    assert "annualize" not in ev(client, auth, "KPI-002", **ent(sx, "RA", R12))["formula"]["parameters"]


def test_retention_v1_behaviour_preserved_after_catalog_sync(client, auth, sx, db):
    """A database seeded before this change gets a NEW formula version from catalog sync; the old version keeps its
    behaviour (colour on a 1-month window) and is still evaluable by version number."""
    d = db.execute(select(KpiDefinition).where(KpiDefinition.code == "KPI-002")).scalar_one()
    cur = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == d.id,
                                                     KpiFormulaVersion.is_current.is_(True))).scalar_one()
    cur.is_current = False
    old = KpiFormulaVersion(kpi_id=d.id, version=cur.version + 1, calculator_key="retention_rate",
                            formula_text=cur.formula_text, numerator_def=cur.numerator_def,
                            denominator_def=cur.denominator_def, parameters={}, is_current=True,
                            change_note="simulated pre-WP-5 formula")
    db.add(old)
    db.commit()
    changes = [c for c in sync_kpi_catalog(db) if c["code"] == "KPI-002"]
    assert changes and changes[0]["new_formula_version"]
    sync_kpi_catalog(db, apply=True)
    oct26 = {"period_start": "2026-10-01", "period_end": "2026-10-31"}
    v_old = ev(client, auth, "KPI-002", version=old.version, **ent(sx, "RB", oct26))
    assert v_old["value"] == pytest.approx(97.0) and v_old["threshold_status"] == "good"
    assert v_old["status_reason"] is None
    v_new = ev(client, auth, "KPI-002", **ent(sx, "RB", oct26))
    assert v_new["formula"]["version"] == old.version + 1
    assert v_new["value"] == pytest.approx(97.0) and v_new["threshold_status"] is None
    assert sync_kpi_catalog(db) == []
