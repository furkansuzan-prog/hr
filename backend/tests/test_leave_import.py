"""Leave import (B-07 / B-27, decision D-6): every leave row is mapped through normalize_leave_type, diagnosis
columns never reach storage, rows are matched to the right employment, re-imports never duplicate records."""
from datetime import date

import pytest
from sqlalchemy import select, text

from app.models import DataQualityIssue, ImportStagingRow, LeaveRecord, Tenant

ADMIN = "admin@demo.local"
HDR = "Sicil;Firma;İzin Türü;Rapor Türü;Başlangıç;Bitiş;Saat;Tanı"


@pytest.fixture(scope="module")
def emp(database):
    """An active SUB01 employee of the DEMO tenant (employee number = sicil)."""
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        tid = db.execute(select(Tenant.id).where(Tenant.code == "DEMO")).scalar_one()
        row = db.execute(text(
            "SELECT e.id, e.employee_number FROM employment_relationships e JOIN legal_entities le "
            "ON le.id = e.legal_entity_id WHERE le.tenant_id = :t AND le.code = 'SUB01' AND e.end_date IS NULL "
            "AND e.start_date <= '2026-01-01' ORDER BY e.id LIMIT 1"), {"t": tid}).one()
        return {"tenant_id": tid, "employment_id": row[0], "sicil": row[1]}


def _csv(rows, header=HDR):
    return (header + "\n" + "\n".join(";".join(r) for r in rows) + "\n").encode()


def _upload(client, auth, content, user=ADMIN, name="izin.csv"):
    r = client.post("/api/v1/imports", headers=auth(user), files={"file": (name, content)},
                    data={"entity_type": "leave"})
    assert r.status_code == 201, r.text
    return r.json()


def test_leave_import_end_to_end(client, auth, db, emp):
    s = emp["sicil"]
    content = _csv([
        [s, "SUB01", "Yıllık izin", "", "2026-03-02", "2026-03-03", "", "-"],                # 2 working days -> 18 h
        [s, "SUB01", "İstirahat raporu", "4", "2026-03-09", "2026-03-13", "45", "O80"],      # SGK analık -> parental
        [s, "SUB01", "Doğum sonrası ücretsiz izin", "", "2026-04-01", "2026-04-30", "180", ""],  # -> parental
        [s, "SUB01", "İstirahat raporu", "3", "2026-05-04", "2026-05-04", "9", "J06"],       # SGK hastalık -> sick
        [s, "SUB01", "Evlilik izni", "", "2026-05-11", "2026-05-13", "27", ""],              # unmapped -> other
        ["NOPE999", "SUB01", "Yıllık izin", "", "2026-03-02", "2026-03-02", "9", ""],        # unknown employee
        [s, "SUB01", "Yıllık izin", "", "2026-06-05", "2026-06-01", "9", ""],                # end before start
        [s, "SUB01", "Yıllık izin", "", "2026-03-02", "2026-03-03", "", ""],                 # duplicate in file
    ])
    up = _upload(client, auth, content)
    assert up["dropped_columns"] == ["Tanı"] and "Tanı" not in up["headers"]
    assert "O80" not in str(up["preview"])
    m = up["suggested_mapping"]
    assert (m["external_id"], m["leave_type"], m["sgk_report_code"], m["hours"]) == ("Sicil", "İzin Türü",
                                                                                   "Rapor Türü", "Saat")
    staged = db.execute(select(ImportStagingRow.raw).where(ImportStagingRow.batch_id == up["batch_id"])).scalars().all()
    assert all("Tanı" not in r and "O80" not in str(r) and "J06" not in str(r) for r in staged)  # never stored

    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN)).json()
    assert v["valid_rows"] == 5 and v["error_rows"] == 3
    rules = {i.rule_code for i in db.execute(select(DataQualityIssue).where(
        DataQualityIssue.import_batch_id == up["batch_id"])).scalars()}
    assert {"no_employment", "date_order", "duplicate_in_file", "unmapped_leave_label", "hours_derived"} <= rules

    dv = db.execute(select(Tenant.data_version).where(Tenant.id == emp["tenant_id"])).scalar()
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(ADMIN)).json()
    assert c["status"] == "committed" and c["committed_rows"] == 5
    db.expire_all()
    assert db.execute(select(Tenant.data_version).where(Tenant.id == emp["tenant_id"])).scalar() > dv
    got = {(r.leave_type, str(r.start_date), float(r.hours)) for r in db.execute(select(LeaveRecord).where(
        LeaveRecord.employment_id == emp["employment_id"], LeaveRecord.start_date >= date(2026, 3, 1),
        LeaveRecord.source_system == "import")).scalars()}
    assert got == {("annual", "2026-03-02", 18.0), ("parental", "2026-03-09", 45.0),
                   ("parental", "2026-04-01", 180.0), ("sick", "2026-05-04", 9.0), ("other", "2026-05-11", 27.0)}

    # same file again -> rejected; same rows in a different file -> validated as already imported, nothing duplicated
    assert client.post("/api/v1/imports", headers=auth(ADMIN), files={"file": ("izin.csv", content)},
                       data={"entity_type": "leave"}).status_code == 409
    up2 = _upload(client, auth, _csv([[s, "SUB01", "Yıllık izin", "", "2026-03-02", "2026-03-03", "18", ""]]),
                  name="izin2.csv")
    v2 = client.post(f"/api/v1/imports/{up2['batch_id']}/validate", json={}, headers=auth(ADMIN)).json()
    assert v2["valid_rows"] == 1
    client.post(f"/api/v1/imports/{up2['batch_id']}/commit", headers=auth(ADMIN))
    n = db.execute(select(LeaveRecord).where(LeaveRecord.employment_id == emp["employment_id"],
                                             LeaveRecord.start_date == date(2026, 3, 2),
                                             LeaveRecord.leave_type == "annual")).scalars().all()
    assert len(n) == 1


def test_parental_leave_never_reaches_absenteeism(client, auth, emp):
    """The imported SGK maternity report (code 4) must not raise KPI-003/028/053 for the employee's entity."""
    p = {"period_start": "2026-03-01", "period_end": "2026-03-31"}
    h = auth(ADMIN)
    ents = {e["code"]: e["id"] for e in client.get("/api/v1/org/legal-entities", headers=h).json()}
    for code in ("KPI-003", "KPI-028", "KPI-053"):
        r = client.get(f"/api/v1/kpi/evaluate/{code}", params={**p, "entity_ids": ents["SUB01"]}, headers=h).json()
        assert r["status"] == "ok", (code, r["status"], r.get("note"))
        assert "parental" not in r["breakdown"]["included_hours_by_type"], code
        assert r["breakdown"]["excluded_hours_by_type"].get("parental", 0) >= 45, code


def test_contractor_cannot_import_leave_for_other_firm(client, auth, emp):
    up = _upload(client, auth, _csv([[emp["sicil"], "SUB01", "Yıllık izin", "", "2026-07-01", "2026-07-01", "9", ""]]),
                 user="sub02.manager@demo.local", name="izin-sub02.csv")
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={},
                    headers=auth("sub02.manager@demo.local")).json()
    assert v["error_rows"] == 1 and v["valid_rows"] == 0


def test_unknown_entity_type_rejected(client, auth):
    r = client.post("/api/v1/imports", headers=auth(ADMIN), files={"file": ("x.csv", b"a;b\n1;2\n")},
                    data={"entity_type": "payroll"})
    assert r.status_code == 400 and "leave" in r.text
