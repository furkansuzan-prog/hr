"""D-16 / ADR-028: with HRSWI_FEATURE_SUBCONTRACTORS off (default) the subcontractor side is completely off.

- The DEMO test tenant keeps contractor relationships in the database (contractor-shaped seed): every flag-off test here
  proves that stored relationship data never reaches a response.
- The FIRM test tenant is the flag-off demo shape (python -m app.seed.synthetic with the flag off): a firm group with
  HQ + GC01.. group companies, no contractor relationships and no subcontractor demo users.
"""
import pytest
from sqlalchemy import func, select, text

from app.models import ContractorRelationship, LegalEntity, Project, User, UserRoleAssignment
from app.security.access import build_access_context
from app.security.features import public_entity_type
from app.services import safety_import as sfi

P = {"period_start": "2025-11-01", "period_end": "2026-10-31"}
RELATIONSHIP_KEYS = {"parent_entity_id", "child_entity_id", "data_sharing_granted", "scope_of_work", "tier", "edges",
                     "parent_id", "child_id", "subcontractors"}
CONTRACTOR_VALUES = {"main_contractor", "subcontractor", "KPI-050", "KPI-051", "subcontractor_workforce",
                     "contractor_risk"}
# historical audit entries and import-issue codes are records, not contractor-relationship data
SKIP = {"/api/v1/audit", "/api/v1/data-quality/issues", "/health/live", "/health/ready"}


def _walk(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, None
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, None, obj


def _violations(body) -> list[str]:
    out = []
    for path, key, value in _walk(body):
        if key in RELATIONSHIP_KEYS and not (path == "$.features" and key == "subcontractors"):  # the flag itself
            out.append(f"{path}: key {key}")
        if isinstance(value, str) and value in CONTRACTOR_VALUES:
            out.append(f"{path}: value {value}")
    return out


def _get_paths(client) -> list[str]:
    spec = client.get("/openapi.json").json()
    return [p for p, ops in spec["paths"].items() if "get" in ops and "{" not in p and p not in SKIP]


@pytest.mark.parametrize("email", ["admin@demo.local", "mc.ops@demo.local", "sub01.manager@demo.local",
                                   "admin@firm.local", "gc01.hr@firm.local"])
def test_flag_off_no_endpoint_returns_contractor_relationship_data(client, auth, email):
    """Scan every parameterless GET endpoint (plus per-code KPI / report endpoints and the assistant) for
    contractor-relationship data: relationship fields, main/sub contractor entity types, KPI-050/051, the subcontractor
    report or the contractor_risk intent."""
    h = auth(email)
    bad: dict[str, list[str]] = {}
    scanned = 0
    for path in _get_paths(client):
        r = client.get(path, params=P, headers=h)
        if path == "/api/v1/org/contractor-tree":
            assert r.status_code == 404
            continue
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            continue
        scanned += 1
        if v := _violations(r.json()):
            bad[path] = v[:5]
    defs = client.get("/api/v1/kpi/definitions", headers=h).json()
    for code in ("KPI-016", "KPI-001", "KPI-003", "KPI-014A"):
        for path in (f"/api/v1/kpi/definitions/{code}", f"/api/v1/kpi/by-entity/{code}", f"/api/v1/kpi/evaluate/{code}"):
            r = client.get(path, params=P, headers=h)
            if r.status_code == 200 and (v := _violations(r.json())):
                bad[path] = v[:5]
    assert not {"KPI-050", "KPI-051"} & {d["code"] for d in defs}
    for rep in client.get("/api/v1/reports", headers=h).json():
        r = client.get(f"/api/v1/reports/{rep['code']}", params=P, headers=h)
        if r.status_code == 200 and (v := _violations(r.json())):
            bad[f"/api/v1/reports/{rep['code']}"] = v[:5]
    for q in ("Which subcontractors show operational risks?", "Alt yükleniciler arasında hangi riskler var?",
              "Firmalar arasında personel devri karşılaştırması"):
        r = client.post("/api/v1/assistant/ask", json={"question": q}, headers=h)
        if r.status_code == 200 and (v := _violations(r.json())):
            bad[f"assistant: {q}"] = v[:5]
    assert scanned >= 5
    assert bad == {}


def test_flag_on_scan_detects_relationship_data(client, auth, subcontractors_on):
    """Control: with the feature on the same scan finds the contractor data (the scan is not vacuous)."""
    h = auth("admin@demo.local")
    assert _violations(client.get("/api/v1/org/contractor-tree", headers=h).json())
    assert _violations(client.get("/api/v1/org/legal-entities", headers=h).json())


def test_public_entity_type(subcontractors_off):
    assert [public_entity_type(t) for t in ("client", "main_contractor", "subcontractor", "employer")] == [
        "client", "employer", "employer", "employer"]


def test_public_entity_type_flag_on(subcontractors_on):
    assert public_entity_type("subcontractor") == "subcontractor" and public_entity_type("main_contractor") == \
        "main_contractor"


# ------------------------------------------------------------------ flag-off demo seed (FIRM tenant)


def test_flag_off_seed_is_a_firm_group(db, ids):
    t = ids["firm"]["tenant_id"]
    ents = {e.code: e.entity_type for e in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == t)).scalars()}
    assert ents == {"OWNER": "client", "HQ": "employer", "GC01": "employer", "GC02": "employer", "GC03": "employer"}
    assert db.execute(select(func.count()).select_from(ContractorRelationship)
                      .where(ContractorRelationship.tenant_id == t)).scalar() == 0
    emails = set(db.execute(select(User.email).where(User.tenant_id == t)).scalars())
    assert "gc01.hr@firm.local" in emails
    assert not {e for e in emails if e.startswith(("sub0", "mc."))}
    # the contractor-shaped DEMO tenant still has its relationships and users (for subcontractors_on tests)
    demo = ids["demo"]["tenant_id"]
    assert db.execute(select(func.count()).select_from(ContractorRelationship)
                      .where(ContractorRelationship.tenant_id == demo)).scalar() > 0


def test_entity_scoped_firm_user_sees_only_its_firm(client, auth, db, ids):
    """The flag-off demo's entity-scoped user (GC01 HR manager) sees exactly GC01 everywhere."""
    h = auth("gc01.hr@firm.local")
    gc01 = db.execute(select(LegalEntity.id).where(LegalEntity.tenant_id == ids["firm"]["tenant_id"],
                                                   LegalEntity.code == "GC01")).scalar_one()
    assert [e["code"] for e in client.get("/api/v1/org/legal-entities", headers=h).json()] == ["GC01"]
    assert [r["legal_entity"]["code"] for r in client.get("/api/v1/kpi/by-entity/KPI-016", params=P,
                                                          headers=h).json()] == ["GC01"]
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["project_limits"] == {} and all(s == [gc01] for s in me["scopes"].values())
    other = db.execute(select(LegalEntity.id).where(LegalEntity.tenant_id == ids["firm"]["tenant_id"],
                                                    LegalEntity.code == "GC02")).scalar_one()
    assert client.get("/api/v1/kpi/evaluate/KPI-016", params={**P, "entity_ids": other},
                      headers=h).status_code == 403
    # D-16: an HR manager has no period.close; a tenant-wide HR director of the firm group does
    assert client.post("/api/v1/kpi/periods/2026-01-01/close", headers=h).status_code == 403


# ------------------------------------------------------------------ access, imports, data management


def test_access_ignores_stored_relationships(db, subcontractors_off):
    for email in ("mc.ops@demo.local", "sub01.manager@demo.local"):
        u = db.execute(select(User).where(User.email == email)).scalar_one()
        own = db.execute(select(UserRoleAssignment.legal_entity_id).where(UserRoleAssignment.user_id == u.id)).scalar()
        ctx = build_access_context(db, u)
        assert ctx.entity_filter("kpi.read") == {own} and ctx.project_limits == {}


def test_import_project_check_does_not_read_relationships(db, ids, monkeypatch):
    """ADR-025 open item (d), closed by D-16: the timesheet / HSE firm-project check (entity_not_on_project) reads
    contractor_relationships only with the feature on."""
    from app.core.config import get_settings

    t = ids["demo"]["tenant_id"]
    mc = db.execute(select(LegalEntity).where(LegalEntity.tenant_id == t, LegalEntity.code == "MC")).scalar_one()
    prj = db.execute(select(Project).where(Project.tenant_id == t).order_by(Project.code)).scalars().first()
    calls = []
    real = db.execute
    monkeypatch.setattr(db, "execute", lambda *a, **k: calls.append(a) or real(*a, **k))
    assert sfi.project_check_enabled() is False
    assert sfi._entity_on_project(db, t, mc.id, prj) is True and calls == []  # no relationship query at all
    monkeypatch.setattr(get_settings(), "feature_subcontractors", True)
    # flag on: MC is neither the owner (OWNER client) nor a child in a relationship -> not on the project
    assert sfi._entity_on_project(db, t, mc.id, prj) is False and len(calls) == 1


def test_timesheet_import_no_entity_not_on_project_warning(client, auth, db, ids):
    """End to end (flag off): a timesheet row for a firm that is not the project owner and has no relationship gets no
    entity_not_on_project warning."""
    from app.models import DataQualityIssue

    h = auth("admin@firm.local")
    t = ids["firm"]["tenant_id"]
    sicil = db.execute(text(
        "SELECT e.employee_number FROM employment_relationships e JOIN legal_entities le ON le.id = e.legal_entity_id "
        "WHERE le.tenant_id = :t AND le.code = 'GC01' AND e.end_date IS NULL AND e.employee_number IS NOT NULL "
        "ORDER BY e.id LIMIT 1"), {"t": t}).scalar()
    if sicil is None:
        pytest.skip("no employee number in the generated FIRM tenant")
    csv = ("sicil;firma;tarih;proje;planlı saat;çalışılan saat\n"
           f"{sicil};GC01;2026-10-05;PRJ-MTR;9;9\n").encode()
    up = client.post("/api/v1/imports", headers=h, files={"file": ("ts.csv", csv)}, data={"entity_type": "timesheet"})
    assert up.status_code == 201, up.text
    bid = up.json()["batch_id"]
    v = client.post(f"/api/v1/imports/{bid}/validate", headers=h, json={"mapping": None})
    assert v.status_code == 200, v.text
    db.expire_all()
    codes = set(db.execute(select(DataQualityIssue.rule_code).where(DataQualityIssue.import_batch_id == bid)).scalars())
    assert "entity_not_on_project" not in codes and "unknown_project" not in codes


def test_new_legal_entity_cannot_be_typed_as_contractor(client, auth):
    h = auth("admin@demo.local")
    for t in ("main_contractor", "subcontractor"):
        r = client.post("/api/v1/manage/legal-entities", headers=h,
                        json={"code": f"D16-{t[:3]}", "name": "x", "entity_type": t, "country": "TR"})
        assert r.status_code == 422, (t, r.text)


def test_new_legal_entity_contractor_type_allowed_with_flag(client, auth, subcontractors_on):
    import uuid

    r = client.post("/api/v1/manage/legal-entities", headers=auth("admin@demo.local"),
                    json={"code": f"D16S-{uuid.uuid4().hex[:5]}", "name": "x", "entity_type": "subcontractor",
                          "country": "TR"})
    assert r.status_code == 201 and r.json()["entity_type"] == "subcontractor"


def test_assistant_subcontractor_question_is_unknown(client, auth):
    for email in ("admin@firm.local", "admin@demo.local"):
        r = client.post("/api/v1/assistant/ask", json={"question": "Taşeron firmalarda risk var mı?"},
                        headers=auth(email)).json()
        assert r["intent"] != "contractor_risk" and "subcontractors" not in (r.get("data") or {})


def test_period_close_flag_off_seed_director(client, auth):
    """The firm group's tenant-wide HR director (main firm) closes and reopens the whole period."""
    h = auth("hr.director@firm.local")
    r = client.post("/api/v1/kpi/periods/2026-02-01/close", headers=h)
    assert r.status_code == 200, r.text
    assert client.post("/api/v1/kpi/periods/2026-02-01/reopen", json={"reason": "test cleanup"},
                       headers=h).status_code == 200
