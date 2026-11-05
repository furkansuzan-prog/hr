"""Import pipeline: preview/mapping, validation, idempotency, retry, identity matching, late data."""
import io
import time

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import EmploymentRelationship, IdentityReviewItem, KpiResult, Person, PersonIdentifier

ADMIN = "ex.admin@exact.local"
DEMO_ADMIN = "admin@demo.local"


def _csv(rows, header="Sicil;Ad;Soyad;TC Kimlik;Firma;İşe Giriş;Çıkış;Çıkış Nedeni"):
    return (header + "\n" + "\n".join(";".join(r) for r in rows) + "\n").encode()


def _upload(client, auth, user, name, content):
    r = client.post("/api/v1/imports", headers=auth(user), files={"file": (name, content)},
                    data={"entity_type": "employment"})
    assert r.status_code == 201, r.text
    return r.json()


def test_preview_mapping_validation_and_error_report(client, auth):
    content = _csv([
        ["N1", "Ada", "Yeni", "", "A", "2025-05-01", "", ""],
        ["N2", "Bora", "Yeni", "", "ZZZ", "2025-05-01", "", ""],          # unknown entity
        ["N3", "Cem", "Yeni", "", "A", "01.05.2025", "2025-04-01", ""],    # end before start
        ["N3", "Cem", "Yeni", "", "A", "01.05.2025", "2025-04-01", ""],    # duplicate in file
        ["N4", "Can", "", "", "A", "2025-05-01", "2025-06-01", ""],        # missing last name; missing reason
    ])
    up = _upload(client, auth, ADMIN, "people.csv", content)
    m = up["suggested_mapping"]
    assert m["external_id"] == "Sicil" and m["legal_entity_code"] == "Firma" and m["start_date"] == "İşe Giriş"
    assert len(up["preview"]) == 5
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN)).json()
    assert v["valid_rows"] == 1 and v["error_rows"] == 4
    rep = client.get(f"/api/v1/imports/{up['batch_id']}/errors.csv", headers=auth(ADMIN)).text
    for rule in ("unknown_legal_entity", "date_order", "duplicate_in_file", "required", "termination_reason_missing"):
        assert rule in rep


def test_contractor_cannot_import_into_other_entity(client, auth):
    up = _upload(client, auth, "ex.b@exact.local", "x.csv", _csv([["Q1", "Q", "Q", "", "C", "2025-05-01", "", ""]]))
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth("ex.b@exact.local")).json()
    assert v["error_rows"] == 1


def test_commit_idempotent_and_backdated_termination_marks_stale(client, auth, ids, db):
    """Scenarios 6 & 7: late data + back-dated termination invalidate persisted KPI results."""
    a = ids["exact"]["entities"]["A"]
    # uses Q3 2025 so the hand-computed Q1 2025 expectations in test_kpi_exact stay valid
    q3 = {"period_start": "2025-07-01", "period_end": "2025-09-30"}
    run = client.post("/api/v1/kpi/runs", json={"codes": ["KPI-001"], **q3, "entity_ids": [a]}, headers=auth(ADMIN))
    assert run.status_code == 201
    run_id = run.json()["run_id"]
    pid = ids["exact"]["stable_a_person"]
    last = db.get(Person, pid).last_name
    content = _csv([
        ["S1", "Test", last, "10000000000", "A", "2024-01-01", "2025-09-15", "voluntary"],  # existing person via TC
        ["L1", "Late", "Arrival", "", "A", "2025-08-01", "", ""],                          # late-arriving hire
        ["X1", "Test", "Different", "10000000001", "A", "2025-08-01", "", ""],              # TC match, name mismatch
    ])
    up = _upload(client, auth, ADMIN, "late.csv", content)
    client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN))
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(ADMIN)).json()
    assert c["status"] == "committed" and c["identity_reviews"] == 1
    emp = db.execute(select(EmploymentRelationship).where(EmploymentRelationship.person_id == pid)).scalar_one()
    db.refresh(emp)
    assert str(emp.end_date) == "2025-09-15" and emp.record_version == 2 and emp.termination_category == "voluntary"
    assert db.execute(select(func.count()).select_from(IdentityReviewItem)).scalar() >= 1
    stale = db.execute(select(KpiResult.is_stale).where(KpiResult.run_id == run_id)).scalars().all()
    assert stale and all(stale)
    # same file again -> rejected (idempotency)
    r = client.post("/api/v1/imports", headers=auth(ADMIN), files={"file": ("late.csv", content)},
                    data={"entity_type": "employment"})
    assert r.status_code == 409
    # recalculation now reflects the back-dated termination: separations 0 -> 1
    new = client.get("/api/v1/kpi/evaluate/KPI-001", params={"entity_ids": a, **q3}, headers=auth(ADMIN)).json()
    assert new["numerator"] == 1


def test_commit_failure_and_resume_without_duplicates(client, auth, db, monkeypatch):
    """Scenario 14: crash mid-commit; retry continues from last durable chunk."""
    from app.models import User
    from app.security.access import build_access_context
    from app.services import imports as imp

    monkeypatch.setattr(get_settings(), "import_chunk_size", 10)
    rows = [[f"R{i:03d}", "Retry", f"Person{i}", "", "SUB01", "2026-09-01", "", ""] for i in range(30)]
    up = _upload(client, auth, DEMO_ADMIN, "retry.csv", _csv(rows))
    client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(DEMO_ADMIN))
    user = db.execute(select(User).where(User.email == DEMO_ADMIN)).scalar_one()
    ctx = build_access_context(db, user)
    with pytest.raises(imp.ImportError_):
        imp.commit(db, ctx, up["batch_id"], fail_after_rows=15)
    s = client.get(f"/api/v1/imports/{up['batch_id']}", headers=auth(DEMO_ADMIN)).json()
    assert s["status"] == "failed" and "simulated" in s["error_message"]
    from app.models import ImportStagingRow
    done = db.execute(select(func.count()).select_from(ImportStagingRow).where(
        ImportStagingRow.batch_id == up["batch_id"], ImportStagingRow.committed.is_(True))).scalar()
    assert done == 10  # first chunk is durable, second chunk rolled back
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(DEMO_ADMIN)).json()
    assert c["status"] == "committed" and c["committed_rows"] == 30 and c["attempt_count"] == 2
    n = db.execute(select(func.count()).select_from(PersonIdentifier).where(
        PersonIdentifier.external_id.like("R0%"))).scalar()
    assert n == 30


def test_large_excel_import(client, auth, db):
    """Scenario 13: 5,000-row xlsx through the full pipeline."""
    wb = Workbook()
    ws = wb.active
    ws.append(["Employee ID", "First Name", "Last Name", "Company", "Hire Date", "Contract"])
    for i in range(5000):
        ws.append([f"BIG{i:05d}", "Big", f"Import{i}", "SUB02", "2026-08-01", "project"])
    buf = io.BytesIO()
    wb.save(buf)
    t0 = time.perf_counter()
    up = _upload(client, auth, DEMO_ADMIN, "big.xlsx", buf.getvalue())
    assert up["total_rows"] == 5000
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={"mapping": {
        **up["suggested_mapping"], "contract_type": "Contract"}}, headers=auth(DEMO_ADMIN)).json()
    assert v["valid_rows"] == 5000, v
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(DEMO_ADMIN)).json()
    elapsed = time.perf_counter() - t0
    assert c["committed_rows"] == 5000
    assert elapsed < 120, f"import took {elapsed:.1f}s"
    print(f"\n5000-row xlsx import: {elapsed:.1f}s")


def test_raw_national_id_never_persisted_or_echoed(client, auth, db):
    """KVKK: the identity number is pseudonymised before staging and is not returned in the preview."""
    from app.models import ImportStagingRow

    raw_tc = "12345678950"
    up = _upload(client, auth, ADMIN, "tc.csv", _csv([["T1", "Tc", "Test", raw_tc, "A", "2025-05-01", "", ""]]))
    assert raw_tc not in str(up["preview"])
    client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN))
    rows = db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == up["batch_id"])).scalars().all()
    assert rows and all(raw_tc not in str(r.raw) and raw_tc not in str(r.mapped) for r in rows)
    # a column mapped manually as national id (not auto-suggested) is pseudonymised at validation
    up2 = _upload(client, auth, ADMIN, "tc2.csv", _csv([["T2", "Tc", "Test", "", "A", "2025-05-01", "", "", raw_tc]],
                  header="Sicil;Ad;Soyad;TC Kimlik;Firma;İşe Giriş;Çıkış;Çıkış Nedeni;Kimlik Belgesi"))
    client.post(f"/api/v1/imports/{up2['batch_id']}/validate",
                json={"mapping": {**up2["suggested_mapping"], "national_id": "Kimlik Belgesi"}}, headers=auth(ADMIN))
    db.expire_all()
    rows = db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == up2["batch_id"])).scalars().all()
    assert all(raw_tc not in str(r.raw) for r in rows)
