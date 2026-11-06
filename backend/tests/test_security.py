"""Authentication, authorisation, data isolation, field protection, audit."""
from sqlalchemy import select

from app.models import AuditLog

Q1 = {"period_start": "2025-01-01", "period_end": "2025-03-31"}


def test_login_rejects_bad_password(client):
    r = client.post("/api/v1/auth/login", json={"email": "ex.admin@exact.local", "password": "wrong"})
    assert r.status_code == 401


def test_requires_token(client):
    assert client.get("/api/v1/kpi/definitions").status_code == 401
    assert client.get("/api/v1/kpi/definitions", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_tampered_token_rejected(client, auth):
    h = auth("ex.b@exact.local")["Authorization"]
    head, payload, sig = h.split(" ")[1].split(".")
    bad = f"Bearer {head}.{payload}.{sig[::-1]}"
    assert client.get("/api/v1/auth/me", headers={"Authorization": bad}).status_code == 401


def test_subcontractor_isolation(client, auth, ids):
    """Scenario 10: B and C managers can never see each other's data."""
    e = ids["exact"]["entities"]
    hb, hc = auth("ex.b@exact.local"), auth("ex.c@exact.local")
    assert [x["code"] for x in client.get("/api/v1/org/legal-entities", headers=hb).json()] == ["B"]
    assert client.get("/api/v1/people", params={"entity_id": e["C"]}, headers=hb).status_code == 403
    assert client.get("/api/v1/kpi/evaluate/KPI-016", params={"entity_ids": e["C"], **Q1}, headers=hb).status_code == 403
    rows = client.get("/api/v1/kpi/by-entity/KPI-016", params=Q1, headers=hc).json()
    assert [r["legal_entity"]["code"] for r in rows] == ["C"]
    people = client.get("/api/v1/people", params={"status": "all"}, headers=hb).json()
    assert {p["legal_entity_id"] for p in people["items"]} == {e["B"]}
    # B manager's default KPI scope = B only (7 people in March 2025: 6 + transferred P2)
    r = client.get("/api/v1/kpi/evaluate/KPI-016", params={"period_start": "2025-03-01", "period_end": "2025-03-31"},
                   headers=hb).json()
    assert r["value"] == 7.0 and r["scope"]["entity_ids"] == [e["B"]]


def test_main_contractor_sees_only_shared_subcontractors(client, auth, ids, subcontractors_on):
    e = ids["exact"]["entities"]
    codes = {x["code"] for x in client.get("/api/v1/org/legal-entities", headers=auth("ex.aops@exact.local")).json()}
    assert codes == {"A", "B", "F"}  # C: no data sharing; D: relationship ended
    assert e["C"] not in [x["id"] for x in client.get("/api/v1/org/legal-entities",
                                                       headers=auth("ex.aops@exact.local")).json()]


def test_person_history_hides_other_employer_spells(client, auth, ids):
    """P2 worked for A then B: B's manager sees only the B spell."""
    h = client.get(f"/api/v1/people/{ids['exact']['p2']}/history", headers=auth("ex.b@exact.local")).json()
    assert len(h["employments"]) == 1 and "hidden_spells" not in h  # no hint about other employers


def test_cost_kpi_requires_cost_permission(client, auth):
    r = client.get("/api/v1/kpi/evaluate/KPI-019", params=Q1, headers=auth("ex.aops@exact.local"))
    assert r.status_code == 403
    dash = client.get("/api/v1/dashboard/executive", params=Q1, headers=auth("ex.aops@exact.local")).json()
    assert "KPI-019" not in [t["code"] for t in dash["tiles"]]


def test_pii_masked_without_permission(client, auth):
    r = client.get("/api/v1/people", headers=auth("ex.auditor@exact.local")).json()
    assert r["items"] and all(i["name"].endswith("***") and i["employee_number"] is None for i in r["items"])


def test_unauthorised_report_download_denied_and_audited(client, auth, db):
    """Scenario 11."""
    r = client.get("/api/v1/reports/monthly_hr_performance", params={"format": "xlsx", **Q1},
                   headers=auth("ex.pm@exact.local"))
    assert r.status_code == 403
    denied = db.execute(select(AuditLog).where(AuditLog.action == "report.export", AuditLog.outcome == "denied")).scalars().all()
    assert denied


def test_permission_denial_is_audited(client, auth, db):
    assert client.get("/api/v1/audit", headers=auth("ex.b@exact.local")).status_code == 403
    rows = db.execute(select(AuditLog).where(AuditLog.action == "authz.denied", AuditLog.entity_id == "audit.read")).all()
    assert rows


def test_production_config_refuses_default_secret(monkeypatch):
    from app.core.config import Settings

    s = Settings(env="production")
    try:
        s.validate_for_runtime()
        raise AssertionError("expected failure")
    except RuntimeError as e:
        assert "JWT_SECRET" in str(e)


def test_no_raw_national_id_stored(db):
    from sqlalchemy import text

    vals = db.execute(text("SELECT national_id_hash FROM people WHERE national_id_hash IS NOT NULL LIMIT 50")).scalars()
    assert all(len(v) == 64 and all(c in "0123456789abcdef" for c in v) for v in vals)
