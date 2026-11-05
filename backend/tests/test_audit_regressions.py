"""Regression tests for the findings of the independent audit (docs/AUDIT_REPORT.md)."""
import pytest
from sqlalchemy import select, text

from app.models import AuditLog

ADMIN = "ex.admin@exact.local"
Q1 = {"period_start": "2025-01-01", "period_end": "2025-03-31"}


def test_f2_suppressed_result_leaks_nothing_in_lineage_or_breakdown(client, auth, ids):
    f = ids["exact"]["entities"]["F"]
    for code in ("KPI-001", "KPI-002", "KPI-018"):
        r = client.get(f"/api/v1/kpi/evaluate/{code}", params={"entity_ids": f, "period_start": "2025-01-01",
                                                               "period_end": "2025-06-30"}, headers=auth(ADMIN)).json()
        assert r["status"] in ("suppressed", "insufficient_data"), code
        assert r["breakdown"] == {}
        assert not ({"separations", "start_population", "retained", "hires", "early_leavers"} & set(r["lineage"]))


def test_f1_run_results_not_readable_outside_scope(client, auth, ids):
    run = client.post("/api/v1/kpi/runs", json={"codes": ["KPI-016"], "period_start": "2025-02-01",
                                                "period_end": "2025-02-28"}, headers=auth(ADMIN)).json()["run_id"]
    assert client.get(f"/api/v1/kpi/runs/{run}", headers=auth("ex.b@exact.local")).status_code == 404
    assert client.get(f"/api/v1/kpi/runs/{run}", headers=auth(ADMIN)).status_code == 200


def test_f4_safety_rate_not_mixed_with_occupation_filter(client, auth):
    r = client.get("/api/v1/kpi/evaluate/KPI-014A", params={"occupation_group": "office", "period_start": "2026-01-01",
                                                            "period_end": "2026-09-30"}, headers=auth("admin@demo.local")).json()
    assert r["status"] == "insufficient_data" and "occupation" in r["note"]


def test_f5_unit_labour_cost_refuses_mixed_units(client, auth):
    r = client.get("/api/v1/kpi/evaluate/KPI-010", params={"period_start": "2026-09-01", "period_end": "2026-09-30"},
                   headers=auth("admin@demo.local")).json()
    assert r["status"] == "insufficient_data" and "several units" in r["note"]


def test_f6_closed_period_is_frozen_and_flags_late_data(client, auth, ids, db):
    from app.analytics.engine import mark_stale_from

    a = ids["exact"]["entities"]["A"]
    p = {"entity_ids": a, "period_start": "2025-06-01", "period_end": "2025-06-30"}
    run = client.post("/api/v1/kpi/runs", json={"codes": ["KPI-016"], "period_start": "2025-06-01",
                                                "period_end": "2025-06-30", "entity_ids": [a]}, headers=auth(ADMIN))
    assert run.status_code == 201
    hr = auth("ex.hrdir@exact.local")
    assert client.post("/api/v1/kpi/periods/2025-06-01/close", headers=hr).status_code == 200
    try:
        r = client.get("/api/v1/kpi/evaluate/KPI-016", params=p, headers=auth(ADMIN)).json()
        assert r["frozen"] is True and r["is_stale"] is False and r["value"] == 11.0
        mark_stale_from(db, ids["exact"]["tenant_id"], __import__("datetime").date(2025, 6, 1))
        db.commit()
        r = client.get("/api/v1/kpi/evaluate/KPI-016", params=p, headers=auth(ADMIN)).json()
        assert r["frozen"] is True and r["is_stale"] is True and "reopen" in r["note"]
    finally:
        client.post("/api/v1/kpi/periods/2025-06-01/reopen", json={"reason": "test cleanup"}, headers=hr)


def test_f7_formula_parameters_validated_and_rows_immutable(client, auth, db):
    r = client.post("/api/v1/kpi/definitions/KPI-007/versions", headers=auth(ADMIN),
                    json={"parameters": {"multiplier": 7}, "change_note": "bad param"})
    assert r.status_code == 400
    r = client.post("/api/v1/kpi/definitions/KPI-007/versions", headers=auth(ADMIN),
                    json={"parameters": {"evil": 1}, "change_note": "unknown param"})
    assert r.status_code == 400
    with pytest.raises(Exception, match="immutable"):
        db.execute(text("UPDATE kpi_formula_versions SET parameters = '{}' WHERE version = 1"))
    db.rollback()


def test_f9_import_batches_scoped_to_creator(client, auth):
    up = client.post("/api/v1/imports", headers=auth(ADMIN), data={"entity_type": "employment"},
                     files={"file": ("s.csv", b"Sicil;Ad;Soyad;Firma;Ise Giris\nZ1;A;B;A;2025-05-01\n")}).json()
    h = auth("ex.b@exact.local")
    assert client.get(f"/api/v1/imports/{up['batch_id']}", headers=h).status_code in (400, 404)
    assert client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=h).status_code in (400, 404)
    assert up["batch_id"] not in [b["batch_id"] for b in client.get("/api/v1/imports", headers=h).json()]


def test_f14_assistant_permission_and_paraphrased_refusals(client, auth):
    r = client.post("/api/v1/assistant/ask", json={"question": "How many additional employees will we need? forecast"},
                    headers=auth("ex.b@exact.local")).json()
    assert r["refused"] is True and r["kind"] != "forecast"
    for q in ("Which employees should we terminate first?", "Should we let go of Mehmet Yilmaz?",
              "Who should be dismissed for low productivity?", "how much should we pay Ayse"):
        assert client.post("/api/v1/assistant/ask", json={"question": q},
                           headers=auth("admin@demo.local")).json()["intent"] == "prohibited", q
    assert client.post("/api/v1/assistant/ask", json={"question": "Kovid döneminde devamsızlık nasıldı?"},
                       headers=auth("admin@demo.local")).json()["intent"] != "prohibited"


def test_f15_kpi_scope_denial_is_audited(client, auth, ids, db):
    c = ids["exact"]["entities"]["C"]
    assert client.get("/api/v1/kpi/evaluate/KPI-016", params={"entity_ids": c, **Q1},
                      headers=auth("ex.b@exact.local")).status_code == 403
    assert db.execute(select(AuditLog).where(AuditLog.action == "authz.scope_denied",
                                             AuditLog.entity_type == "kpi")).first()


def test_f18_turkish_cp1254_csv_and_bad_files(client, auth):
    content = "Sicil;Ad;Soyad;Firma;İşe Giriş\nC1;Şükrü;Öztürk;A;2025-05-01\n".encode("cp1254")
    r = client.post("/api/v1/imports", headers=auth(ADMIN), data={"entity_type": "employment"},
                    files={"file": ("tr.csv", content)})
    assert r.status_code == 201 and r.json()["suggested_mapping"]["start_date"] == "İşe Giriş"
    r = client.post("/api/v1/imports", headers=auth(ADMIN), data={"entity_type": "employment"},
                    files={"file": ("broken.xlsx", b"not a zip")})
    assert r.status_code == 400


def test_f20_person_history_does_not_reveal_existence(client, auth, ids):
    # a person employed only by A: B's manager gets 404, same as a non-existent id
    pid = ids["exact"]["stable_a_person"]
    assert client.get(f"/api/v1/people/{pid}/history", headers=auth("ex.b@exact.local")).status_code == 404
    assert client.get("/api/v1/people/999999/history", headers=auth("ex.b@exact.local")).status_code == 404
