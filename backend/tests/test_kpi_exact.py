"""KPI engine against the hand-computed EXACT tenant (see tests/factories.py for the arithmetic)."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models import KpiFormulaVersion, KpiResult, User
from app.security.access import build_access_context
from app.services.timekeeping import ingest_attendance

ADMIN = "ex.admin@exact.local"
Q1 = {"period_start": "2025-01-01", "period_end": "2025-03-31"}
JAN = {"period_start": "2025-01-01", "period_end": "2025-01-31"}


def ev(client, auth, code, user=ADMIN, **params):
    r = client.get(f"/api/v1/kpi/evaluate/{code}", params=params, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_turnover_tenant_excludes_internal_transfer(client, auth):
    """Scenario 2: P2 moves A -> B the next day: not a separation at group level."""
    r = ev(client, auth, "KPI-001", **Q1)
    assert r["status"] == "ok"
    assert r["numerator"] == 2 and r["denominator"] == pytest.approx(27.5)
    assert r["value"] == pytest.approx(7.2727, abs=1e-3)
    # category counts below the publishable minimum (5) are masked, never shown
    assert r["breakdown"]["by_termination_category"] == {"voluntary": None, "unknown": None}
    assert r["quality_score"] == 50.0  # one of two separations lacks a reason
    assert "not assumed" in r["note"]
    assert r["formula"]["version"] == 1 and r["kind"] == "actual"


def test_turnover_entity_counts_transfer_out(client, auth, ids):
    a = ids["exact"]["entities"]["A"]
    r = ev(client, auth, "KPI-001", entity_ids=str(a), **Q1)
    assert r["numerator"] == 3 and r["denominator"] == pytest.approx(12.25)
    assert r["value"] == pytest.approx(24.4898, abs=1e-3)
    assert r["annualized_value"] == pytest.approx(24.4898 * 365 / 90, abs=0.01)


def test_headcount_counts_persons_once(client, auth, ids):
    """Scenario 1: P1 has two spells; headcount is distinct persons."""
    a = ids["exact"]["entities"]["A"]
    r = ev(client, auth, "KPI-016", entity_ids=str(a), period_start="2025-06-01", period_end="2025-06-30")
    assert r["value"] == 11.0  # 10 stable + P1 (second spell)
    h = client.get(f"/api/v1/people/{ids['exact']['p1']}/history", headers=auth(ADMIN)).json()
    assert [e["start_date"] for e in h["employments"]] == ["2025-01-01", "2025-06-01"]


def test_absenteeism_and_overtime_exact(client, auth, ids):
    a = str(ids["exact"]["entities"]["A"])
    r = ev(client, auth, "KPI-003", entity_ids=a, **JAN)
    assert r["value"] == pytest.approx(2.0)
    assert r["breakdown"]["excluded_hours_by_type"] == {"annual": 18.0}
    r = ev(client, auth, "KPI-007", entity_ids=a, **JAN)
    assert r["value"] == pytest.approx(10.0)


def test_missing_timesheets_is_insufficient_not_zero(client, auth, ids):
    """Scenario 4."""
    a = str(ids["exact"]["entities"]["A"])
    r = ev(client, auth, "KPI-003", entity_ids=a, period_start="2025-02-01", period_end="2025-02-28")
    assert r["status"] == "insufficient_data" and r["value"] is None


def test_zero_denominator_via_api(client, auth, ids):
    """Scenario 9: timesheet rows exist but planned hours are 0."""
    c = str(ids["exact"]["entities"]["C"])
    for code in ("KPI-003", "KPI-007"):
        r = ev(client, auth, code, entity_ids=c, **JAN)
        assert r["status"] == "not_computable" and r["value"] is None


def test_small_group_suppressed(client, auth, ids):
    f = str(ids["exact"]["entities"]["F"])
    r = ev(client, auth, "KPI-001", entity_ids=f, **Q1)
    assert r["status"] == "suppressed" and r["value"] is None and r["numerator"] is None


def test_multi_currency_cost(client, auth, ids):
    """Scenario 15: EUR costs converted with the stored FX rate."""
    b = str(ids["exact"]["entities"]["B"])
    r = ev(client, auth, "KPI-019", entity_ids=b, period_start="2025-03-01", period_end="2025-03-31")
    assert r["status"] == "ok"
    assert r["value"] == pytest.approx(45000.0)
    assert r["lineage"]["source_currencies"] == ["EUR"]


def test_missing_fx_and_missing_cost(client, auth, ids):
    """Scenario 8: missing cost data / FX rate -> insufficient_data with explanation, never a silent 0."""
    c = str(ids["exact"]["entities"]["C"])
    a = str(ids["exact"]["entities"]["A"])
    r = ev(client, auth, "KPI-019", entity_ids=c, period_start="2025-03-01", period_end="2025-03-31")
    assert r["status"] == "insufficient_data" and "RUB->TRY" in r["note"]
    r = ev(client, auth, "KPI-019", entity_ids=a, period_start="2025-03-01", period_end="2025-03-31")
    assert r["status"] == "insufficient_data" and r["note"] == "No cost data"


def test_draft_kpi_cannot_run(client, auth):
    r = client.get("/api/v1/kpi/evaluate/KPI-012", params=Q1, headers=auth(ADMIN))
    assert r.status_code == 409


def test_catalog_has_50_plus_and_only_validated_active(client, auth):
    defs = client.get("/api/v1/kpi/definitions", headers=auth(ADMIN)).json()
    assert len(defs) >= 50
    for d in defs:
        assert (d["status"] == "active") == d["validated"]
        for f in ("code", "description", "purpose", "unit", "data_sources", "data_owner_role", "frequency",
                  "inclusion_rules", "exclusion_rules", "missing_data_behavior", "standard_refs"):
            assert d[f] is not None
    ltifr = next(d for d in defs if d["code"] == "KPI-014A")
    trir = next(d for d in defs if d["code"] == "KPI-014B")
    assert ltifr["unit"] != trir["unit"]  # LTIFR and TRIR are distinct, never mixed


def test_formula_change_preserves_old_results(client, auth, ids, db):
    """Scenario 12."""
    a = ids["exact"]["entities"]["A"]
    r = client.post("/api/v1/kpi/runs", json={"codes": ["KPI-003"], "period_start": "2025-01-01",
                                              "period_end": "2025-01-31", "entity_ids": [a]}, headers=auth(ADMIN))
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    try:
        r = client.post("/api/v1/kpi/definitions/KPI-003/versions", headers=auth(ADMIN), json={
            "parameters": {"absence_types": ["sick", "unexcused", "unpaid", "annual"]},
            "change_note": "Test: include annual leave"})
        assert r.status_code == 201 and r.json()["version"] == 2
        new = ev(client, auth, "KPI-003", entity_ids=str(a), **JAN)
        assert new["formula"]["version"] == 2 and new["value"] == pytest.approx(6.0)
        old = ev(client, auth, "KPI-003", entity_ids=str(a), version=1, **JAN)
        assert old["value"] == pytest.approx(2.0)
        stored = client.get(f"/api/v1/kpi/runs/{run_id}", headers=auth(ADMIN)).json()["results"][0]
        assert stored["formula_version"] == 1 and stored["value"] == pytest.approx(2.0)
    finally:
        # restore v1 semantics as a NEW version (history is append-only)
        client.post("/api/v1/kpi/definitions/KPI-003/versions", headers=auth(ADMIN), json={
            "parameters": {"absence_types": ["sick", "unexcused", "unpaid"]}, "change_note": "Restore default"})
    versions = db.execute(select(KpiFormulaVersion.version).join_from(KpiFormulaVersion, KpiResult, isouter=True)).all()
    assert len(versions) >= 3


def test_period_close_blocks_recalculation(client, auth):
    body = {"codes": ["KPI-016"], "period_start": "2025-04-01", "period_end": "2025-04-30"}
    assert client.post("/api/v1/kpi/periods/2025-04-01/close", headers=auth("ex.hrdir@exact.local")).status_code == 200
    assert client.post("/api/v1/kpi/runs", json=body, headers=auth(ADMIN)).status_code == 409
    r = client.post("/api/v1/kpi/periods/2025-04-01/reopen", json={}, headers=auth("ex.hrdir@exact.local"))
    assert r.status_code == 422  # reason mandatory
    r = client.post("/api/v1/kpi/periods/2025-04-01/reopen", json={"reason": "late payroll correction"},
                    headers=auth("ex.hrdir@exact.local"))
    assert r.status_code == 200
    assert client.post("/api/v1/kpi/runs", json=body, headers=auth(ADMIN)).status_code == 201


def test_hierarchy_change_over_time(db, ids):
    """Scenario 3: D's relationship to A ended on 2025-01-01; A users saw D before, not after."""
    u = db.get(User, ids["exact"]["users"]["ex.aops@exact.local"])
    d = ids["exact"]["entities"]["D"]
    before = build_access_context(db, u, on=date(2024, 6, 1)).entity_filter("kpi.read")
    after = build_access_context(db, u, on=date(2025, 6, 1)).entity_filter("kpi.read")
    assert d in before and d not in after
    assert ids["exact"]["entities"]["C"] not in after  # no data sharing granted -> never visible


def test_duplicate_badge_events_ingested_once(db, ids):
    """Scenario 5."""
    from datetime import UTC, datetime

    pid = ids["exact"]["stable_a_person"]
    t = datetime(2025, 1, 6, 7, 58, 3, tzinfo=UTC)
    evs = [{"person_id": pid, "device_id": "G1", "event_time": t, "direction": "in"},
           {"person_id": pid, "device_id": "G1", "event_time": t.replace(second=40), "direction": "in"}]
    r1 = ingest_attendance(db, ids["exact"]["tenant_id"], evs)
    r2 = ingest_attendance(db, ids["exact"]["tenant_id"], evs)
    assert r1["inserted"] == 1 and r1["duplicates"] == 1
    assert r2["inserted"] == 0
