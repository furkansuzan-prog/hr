"""ADR-025: firm-based product (D-9 - subcontractor feature flag, default off) and the health.read permission (D-12).

Subcontractor behaviour with the flag ON is covered by the existing tests (they request the `subcontractors_on`
fixture); this module checks the default (flag off) behaviour and the health.read rules.
"""
from datetime import date

import pytest
from sqlalchemy import func, select

from app.analytics import dashboards, engine
from app.analytics.catalog import KPI_CATALOG
from app.models import AuditLog, LegalEntity, Role, RolePermission, User, UserRoleAssignment
from app.security.access import AccessContext, build_access_context
from app.security.auth import hash_password
from app.security.catalog import HEALTH_DERIVED_KPIS, ROLE_TEMPLATES
from app.services import reports as rep

P = {"period_start": "2025-11-01", "period_end": "2026-10-31"}
HEALTH = sorted(HEALTH_DERIVED_KPIS)


def _audits(db, action, entity_id=None):
    db.expire_all()
    q = select(func.count()).select_from(AuditLog).where(AuditLog.action == action, AuditLog.outcome == "denied")
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == entity_id)
    return db.execute(q).scalar()


def _demo_user(db, email):
    return db.execute(select(User).where(User.email == email)).scalar_one()


# ------------------------------------------------------------------ D-9: subcontractor feature off (default)


def test_flag_default_is_off_and_exposed_on_me(client, auth):
    from app.core.config import get_settings

    assert get_settings().feature_subcontractors is False  # shipped default: firm-based (D-9)
    me = client.get("/api/v1/auth/me", headers=auth("admin@demo.local")).json()
    assert me["features"] == {"subcontractors": False}


def test_me_reports_flag_on(client, auth, subcontractors_on):
    assert client.get("/api/v1/auth/me", headers=auth("admin@demo.local")).json()["features"] == {"subcontractors": True}


def test_no_expansion_through_contractor_relationships(db, subcontractors_off, monkeypatch):
    """mc.ops is scoped to the main contractor: flag off -> exactly that entity; flag on -> + shared subcontractors."""
    from app.core.config import get_settings

    u = _demo_user(db, "mc.ops@demo.local")
    own = db.execute(select(UserRoleAssignment.legal_entity_id).where(UserRoleAssignment.user_id == u.id)).scalar()
    off = build_access_context(db, u)
    assert off.entity_filter("kpi.read") == {own} and off.project_limits == {}
    monkeypatch.setattr(get_settings(), "feature_subcontractors", True)
    on = build_access_context(db, u)
    assert own in on.entity_filter("kpi.read") and len(on.entity_filter("kpi.read")) > 1


def test_subcontractor_manager_sees_only_own_firm(client, auth, db):
    rows = client.get("/api/v1/kpi/by-entity/KPI-016", params=P, headers=auth("sub01.manager@demo.local")).json()
    assert [r["legal_entity"]["code"] for r in rows] == ["SUB01"]
    me = client.get("/api/v1/auth/me", headers=auth("sub01.manager@demo.local")).json()
    assert me["project_limits"] == {} and all(len(s) == 1 for s in me["scopes"].values())


def test_tenant_wide_user_unchanged(client, auth, db):
    rows = client.get("/api/v1/kpi/by-entity/KPI-016", params=P, headers=auth("admin@demo.local")).json()
    n = db.execute(select(func.count()).select_from(LegalEntity).where(
        LegalEntity.tenant_id == _demo_user(db, "admin@demo.local").tenant_id)).scalar()
    assert len(rows) == n


def test_contractor_endpoints_hidden(client, auth):
    h = auth("admin@demo.local")
    assert client.get("/api/v1/org/contractor-tree", headers=h).status_code == 404
    body = {"parent_entity_id": 1, "child_entity_id": 2, "tier": 1, "valid_from": "2026-01-01",
            "data_sharing_granted": True}
    assert client.post("/api/v1/manage/contractor-relationships", json=body, headers=h).status_code == 404
    assert client.post("/api/v1/manage/contractor-relationships/1/end", json={"valid_to": "2026-02-01"},
                       headers=h).status_code == 404


def test_contractor_tree_available_with_flag(client, auth, subcontractors_on):
    assert client.get("/api/v1/org/contractor-tree", headers=auth("admin@demo.local")).status_code == 200


def test_subcontractor_kpis_hidden(client, auth):
    h = auth("admin@demo.local")
    codes = {d["code"] for d in client.get("/api/v1/kpi/definitions", headers=h).json()}
    assert "KPI-050" not in codes and "KPI-051" not in codes and "KPI-016" in codes
    assert client.get("/api/v1/kpi/definitions/KPI-050", headers=h).status_code == 404
    for path in ("evaluate", "series", "by-entity"):
        assert client.get(f"/api/v1/kpi/{path}/KPI-050", params=P, headers=h).status_code == 404, path
    assert client.post("/api/v1/kpi/runs", json={"codes": ["KPI-050"], "period_start": "2025-01-01",
                                                  "period_end": "2025-01-31"}, headers=h).status_code == 404


def test_subcontractor_report_hidden(client, auth):
    h = auth("admin@demo.local")
    assert "subcontractor_workforce" not in {r["code"] for r in client.get("/api/v1/reports", headers=h).json()}
    assert client.get("/api/v1/reports/subcontractor_workforce", params=P, headers=h).status_code == 404
    assert client.get("/api/v1/reports/monthly_hr_performance", params=P, headers=h).status_code == 200


def test_assistant_has_no_subcontractor_analysis(client, auth):
    r = client.post("/api/v1/assistant/ask", json={"question": "Which subcontractors show operational risks?"},
                    headers=auth("admin@demo.local")).json()
    assert r["intent"] == "unknown" and "subcontractors" not in r["data"]


def _exact_director(db, ids, email, entity_code):
    t = ids["exact"]["tenant_id"]
    role = db.execute(select(Role).where(Role.tenant_id == t, Role.code == "HR_DIRECTOR")).scalar_one()
    u = db.execute(select(User).where(User.email == email)).scalar()
    if u is None:
        u = User(tenant_id=t, email=email, full_name=email, password_hash=hash_password("Demo!2345"), locale="en")
        db.add(u)
        db.flush()
        db.add(UserRoleAssignment(user_id=u.id, role_id=role.id, scope_type="legal_entity",
                                  legal_entity_id=ids["exact"]["entities"][entity_code]))
        db.commit()
    return email


def test_firm_scoped_period_close_holder_cannot_close(client, auth, ids, db, monkeypatch):
    """D-16 / ADR-028 (supersedes ADR-025 item 4): flag off - an entity-scoped period.close holder may NOT close or
    reopen, whatever the entity type (B is typed 'subcontractor' in the EXACT tenant); flag on - D-2 unchanged (403
    for a subcontractor-scoped holder)."""
    from app.core.config import get_settings

    h = auth(_exact_director(db, ids, "ex.hrdir.firm@exact.local", "B"))
    assert client.post("/api/v1/kpi/periods/2025-06-01/close", headers=h).status_code == 403
    assert client.post("/api/v1/kpi/periods/2025-06-01/reopen", json={"reason": "x"}, headers=h).status_code == 403
    monkeypatch.setattr(get_settings(), "feature_subcontractors", True)
    assert client.post("/api/v1/kpi/periods/2025-06-01/close", headers=h).status_code == 403


def test_freeze_skips_hidden_kpis(client, auth, ids, db):
    active = [k for k in KPI_CATALOG if k["status"] == "active" and k["family"] != "contractor"]
    n_entities = len(ids["kpi"]["entities"])
    h = auth("kx.hrdir@kpix.local")
    r = client.post("/api/v1/kpi/periods/2025-04-01/close", headers=h)
    assert r.status_code == 200, r.text
    try:
        assert r.json()["frozen_results"] == len(active) * (1 + n_entities)
    finally:
        assert client.post("/api/v1/kpi/periods/2025-04-01/reopen", json={"reason": "test cleanup"},
                           headers=h).status_code == 200


# ------------------------------------------------------------------ D-12: health.read


def test_role_templates_health_read():
    holders = {r for r, (_, perms) in ROLE_TEMPLATES.items() if "health.read" in perms}
    assert holders == {"SYSTEM_ADMIN", "HR_DIRECTOR", "HR_MANAGER", "HSE_SPECIALIST"}  # HR_MANAGER: D-15
    assert HEALTH_DERIVED_KPIS == {"KPI-028", "KPI-053", "KPI-054"}


@pytest.mark.parametrize("code", HEALTH)
def test_health_kpis_denied_without_health_read(client, auth, db, code):
    # D-12 / D-15: executives, finance, operations and project managers still do not see health-derived KPIs
    for email in ("exec@demo.local", "mc.ops@demo.local", "finance@demo.local", "ex.pm@exact.local"):
        h = auth(email)
        before = _audits(db, "authz.scope_denied")
        for path in ("evaluate", "series", "by-entity"):
            assert client.get(f"/api/v1/kpi/{path}/{code}", params=P, headers=h).status_code == 403, (email, path)
        assert _audits(db, "authz.scope_denied") >= before + 3  # every denial audited
        before = _audits(db, "authz.denied", code)
        assert client.get(f"/api/v1/kpi/definitions/{code}", headers=h).status_code == 403
        assert _audits(db, "authz.denied", code) == before + 1
        assert code not in {d["code"] for d in client.get("/api/v1/kpi/definitions", headers=h).json()}
        assert client.get("/api/v1/analytics/anomalies/" + code, params=P, headers=h).status_code in (403, 404)


@pytest.mark.parametrize("email", ["hr.director@demo.local", "hr.manager@demo.local", "hse@demo.local",
                                   "admin@demo.local", "gc01.hr@firm.local"])  # hr.manager / gc01.hr: D-15
def test_health_kpis_allowed_with_health_read(client, auth, email):
    h = auth(email)
    for code in HEALTH:
        assert client.get(f"/api/v1/kpi/evaluate/{code}", params=P, headers=h).status_code == 200, code
        assert client.get(f"/api/v1/kpi/definitions/{code}", headers=h).status_code == 200
    assert set(HEALTH) <= {d["code"] for d in client.get("/api/v1/kpi/definitions", headers=h).json()}


def test_kpi003_breakdown_hides_health_types_without_health_read(client, auth):
    full = client.get("/api/v1/kpi/evaluate/KPI-003", params=P, headers=auth("hr.director@demo.local")).json()
    red = client.get("/api/v1/kpi/evaluate/KPI-003", params=P, headers=auth("exec@demo.local")).json()
    assert full["status"] == "ok" and full["breakdown"]["included_hours_by_type"]["sick"] > 0
    assert red["value"] == full["value"]  # the rate itself stays visible
    assert red["breakdown"]["included_hours_by_type"]["sick"] is None
    assert "sick" in red["breakdown"]["masked_types"] and red["breakdown"]["masked_by_permission"] == "health.read"
    assert "sick" not in red["lineage"]["leave_types_found"]
    assert "masked_by_permission" not in full["breakdown"]
    # the shared per-scope cache entry was not mutated by the redaction
    again = client.get("/api/v1/kpi/evaluate/KPI-003", params=P, headers=auth("hr.director@demo.local")).json()
    assert again["breakdown"]["included_hours_by_type"]["sick"] == full["breakdown"]["included_hours_by_type"]["sick"]


def _ctx(tid, **grants):
    return AccessContext(user_id=None, tenant_id=tid, email="t", locale="en", grants=grants)


def test_health_scope_is_intersection(db, ids):
    """kpi.read tenant-wide + health.read on one entity -> health KPIs only for that entity (like cost.read)."""
    tid, e = ids["exact"]["tenant_id"], ids["exact"]["entities"]
    ctx = _ctx(tid, **{"kpi.read": None, "health.read": {e["A"]}})
    ps, pe = date(2025, 1, 1), date(2025, 12, 31)
    r = engine.evaluate(db, ctx, "KPI-028", ps, pe)
    assert r["scope"]["entity_ids"] == [e["A"]]
    assert [x["legal_entity"]["id"] for x in engine.by_entity(db, ctx, "KPI-053", ps, pe)] == [e["A"]]
    with pytest.raises(engine.ScopeDenied):
        engine.evaluate(db, ctx, "KPI-054", ps, pe, entity_ids={e["B"]})
    # KPI-003: tenant-wide result redacted (health.read does not cover it), entity A result not
    assert engine.evaluate(db, ctx, "KPI-003", ps, pe)["breakdown"].get("masked_by_permission") == "health.read"
    assert "masked_by_permission" not in engine.evaluate(db, ctx, "KPI-003", ps, pe, entity_ids={e["A"]})["breakdown"]
    with pytest.raises(engine.ScopeDenied):
        engine.evaluate(db, _ctx(tid, **{"kpi.read": None}), "KPI-028", ps, pe)


def test_dashboard_key_includes_health_scope(db, ids):
    tid, e = ids["exact"]["tenant_id"], ids["exact"]["entities"]
    kw = {"entity_ids": None, "project_id": None, "occupation_group": None}
    ps, pe = date(2025, 1, 1), date(2025, 12, 31)
    keys = {dashboards._response_key(db, _ctx(tid, **g), ps, pe, kw) for g in (
        {"kpi.read": None}, {"kpi.read": None, "health.read": None}, {"kpi.read": None, "health.read": {e["A"]}})}
    assert len(keys) == 3


def test_report_omits_health_kpis_without_health_read(db, ids, monkeypatch):
    monkeypatch.setitem(rep.REPORTS, "health_probe", {"name": {"en": "probe"}, "kpis": ["KPI-016", "KPI-028"],
                                                      "by_entity": ["KPI-053"], "status": "available"})
    tid = ids["exact"]["tenant_id"]
    ps, pe = date(2025, 1, 1), date(2025, 12, 31)
    out = rep.build(db, _ctx(tid, **{"kpi.read": None}), "health_probe", ps, pe)
    assert [k["code"] for k in out["kpis"]] == ["KPI-016"]
    assert {"code": "KPI-028", "reason": "requires health.read"} in out["metadata"]["omitted_kpis"]
    assert out["by_entity_columns"] == []
    full = rep.build(db, _ctx(tid, **{"kpi.read": None, "health.read": None}), "health_probe", ps, pe)
    assert [k["code"] for k in full["kpis"]] == ["KPI-016", "KPI-028"] and full["by_entity_columns"] == ["KPI-053"]


def test_assistant_absenteeism_answer_hides_health_breakdown(client, auth):
    r = client.post("/api/v1/assistant/ask", json={"question": "absenteeism last six months?"},
                    headers=auth("exec@demo.local")).json()
    assert r["intent"] == "absenteeism"
    bd = r["data"]["total"]["breakdown"]
    if bd:  # ok result: health-derived per-type hours are hidden
        assert bd["included_hours_by_type"].get("sick") is None and bd["masked_by_permission"] == "health.read"


def test_assistant_scope_denial_is_audited(db, ids, monkeypatch):
    from app.ai import assistant

    monkeypatch.setattr(assistant, "INTENTS", [("absenteeism", ["absence"])])
    monkeypatch.setattr(engine, "evaluate", lambda *a, **k: (_ for _ in ()).throw(engine.ScopeDenied("health.read")))
    user = db.execute(select(User).where(User.email == "exec@demo.local")).scalar_one()
    ctx = build_access_context(db, user)
    before = _audits(db, "authz.scope_denied", "absenteeism")
    out = assistant.handle(db, ctx, "absence?")
    assert out["refused"] is True and _audits(db, "authz.scope_denied", "absenteeism") == before + 1


def test_run_results_hide_health_kpis(client, auth):
    body = {"codes": ["KPI-016", "KPI-028"], "period_start": "2026-09-01", "period_end": "2026-09-30"}
    run = client.post("/api/v1/kpi/runs", json=body, headers=auth("hr.director@demo.local"))
    assert run.status_code == 201, run.text
    rid = run.json()["run_id"]
    own = {r["code"] for r in client.get(f"/api/v1/kpi/runs/{rid}", headers=auth("hr.director@demo.local")).json()["results"]}
    other = {r["code"] for r in client.get(f"/api/v1/kpi/runs/{rid}", headers=auth("exec@demo.local")).json()["results"]}
    assert own == {"KPI-016", "KPI-028"} and other == {"KPI-016"}


def test_migration_0011_grants_system_roles(db):
    """Data migrations: 0012 (D-15) adds HR_MANAGER, its downgrade removes only that grant; 0011 downgrade removes
    health.read everywhere; upgrade grants it back to the system role templates."""
    import os

    from alembic.config import Config

    from alembic import command

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))

    def holders():
        db.expire_all()
        return set(db.execute(select(Role.code).join(RolePermission, RolePermission.role_id == Role.id)
                              .where(RolePermission.permission_code == "health.read")).scalars())

    assert holders() == {"SYSTEM_ADMIN", "HR_DIRECTOR", "HR_MANAGER", "HSE_SPECIALIST"}
    db.commit()
    command.downgrade(cfg, "0011")
    assert holders() == {"SYSTEM_ADMIN", "HR_DIRECTOR", "HSE_SPECIALIST"}
    command.downgrade(cfg, "0010")
    assert holders() == set()
    command.upgrade(cfg, "head")
    assert holders() == {"SYSTEM_ADMIN", "HR_DIRECTOR", "HR_MANAGER", "HSE_SPECIALIST"}
