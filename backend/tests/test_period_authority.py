"""Period close / reopen authority.

D-16 (2026-11-06, ADR-028; subcontractor feature OFF - default): only tenant-wide period.close holders (the main firm)
may close / reopen; the close freezes the whole tenant period (every firm). Entity-scoped holders get 403 (audited),
whatever the entity type.
D-2 (2026-11-05; feature ON - unchanged): reserved for the main contractor (tenant-wide or main_contractor in scope).
"""
import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Role, User, UserRoleAssignment
from app.security.auth import hash_password


def _user(db, ids, email, entity_code):
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


def test_subcontractor_scoped_director_cannot_close_or_reopen(client, auth, ids, db, subcontractors_on):
    email = _user(db, ids, "ex.hrdir.sub@exact.local", "B")
    h = auth(email)
    assert client.post("/api/v1/kpi/periods/2025-05-01/close", headers=h).status_code == 403
    assert client.post("/api/v1/kpi/periods/2025-05-01/reopen", json={"reason": "x"}, headers=h).status_code == 403
    assert db.execute(select(AuditLog).where(AuditLog.action == "authz.scope_denied",
                                             AuditLog.entity_type == "period")).first()


def test_main_contractor_director_closes_for_everyone(client, auth, ids, db, subcontractors_on):
    """An A-scoped (main contractor) director closes the month; frozen values exist for tenant-wide readers and for
    entities outside the closer's own scope (C has no data sharing), and the system runs are not readable by him."""
    email = _user(db, ids, "ex.hrdir.mc@exact.local", "A")
    h = auth(email)
    r = client.post("/api/v1/kpi/periods/2025-07-01/close", headers=h)
    assert r.status_code == 200, r.text
    try:
        p = {"period_start": "2025-07-01", "period_end": "2025-07-31"}
        admin = auth("ex.admin@exact.local")
        tenant = client.get("/api/v1/kpi/evaluate/KPI-016", params=p, headers=admin).json()
        c_only = client.get("/api/v1/kpi/evaluate/KPI-016", params={**p, "entity_ids": ids["exact"]["entities"]["C"]},
                            headers=admin).json()
        assert tenant.get("frozen") is True and c_only.get("frozen") is True
        # the closer cannot read the system freeze runs (would expose C / cost data)
        assert client.get(f"/api/v1/kpi/runs/{tenant['run_id']}", headers=h).status_code == 404
    finally:
        client.post("/api/v1/kpi/periods/2025-07-01/reopen", json={"reason": "test cleanup"}, headers=h)


def _denials(db, action):
    db.expire_all()
    return db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "authz.scope_denied", AuditLog.entity_type == "period", AuditLog.outcome == "denied",
        AuditLog.details["action"].as_string() == action)).scalar()


@pytest.mark.parametrize("entity_code", ["A", "B", "C"])  # main_contractor / subcontractor / subcontractor (EXACT)
def test_d16_entity_scoped_holder_cannot_close_or_reopen(client, auth, ids, db, entity_code):
    """D-16 (flag off): an entity-scoped period.close holder is refused for every firm type - 403 + audit."""
    h = auth(_user(db, ids, f"ex.hrdir.d16.{entity_code.lower()}@exact.local", entity_code))
    before_close, before_reopen = _denials(db, "period.close"), _denials(db, "period.reopen")
    r = client.post("/api/v1/kpi/periods/2025-05-01/close", headers=h)
    assert r.status_code == 403 and "main firm" in r.json()["detail"]
    assert client.post("/api/v1/kpi/periods/2025-05-01/reopen", json={"reason": "x"}, headers=h).status_code == 403
    assert _denials(db, "period.close") == before_close + 1 and _denials(db, "period.reopen") == before_reopen + 1


def test_d16_entity_scoped_reopen_refused_on_closed_period(client, auth, ids, db):
    """The main firm closes; an entity-scoped holder cannot reopen the (whole-tenant) period."""
    main = auth("ex.hrdir@exact.local")
    assert client.post("/api/v1/kpi/periods/2025-08-01/close", headers=main).status_code == 200
    try:
        h = auth(_user(db, ids, "ex.hrdir.d16.a@exact.local", "A"))
        assert client.post("/api/v1/kpi/periods/2025-08-01/reopen", json={"reason": "x"}, headers=h).status_code == 403
    finally:
        assert client.post("/api/v1/kpi/periods/2025-08-01/reopen", json={"reason": "test cleanup"},
                           headers=main).status_code == 200


def test_d16_main_firm_close_freezes_every_firm(client, auth, ids, db):
    """D-16 (flag off): the tenant-wide holder closes the month for the whole tenant - tenant scope and every firm
    serve frozen values; the closer gets only a count; reopening works for the same holder."""
    from app.analytics import engine
    from app.security.access import AccessContext

    ctx = AccessContext(user_id=None, tenant_id=ids["exact"]["tenant_id"], email="t", locale="en",
                        grants={"period.close": None})
    assert engine.can_close_periods(db, ctx) is True
    h = auth("ex.hrdir@exact.local")
    r = client.post("/api/v1/kpi/periods/2025-09-01/close", headers=h)
    assert r.status_code == 200, r.text
    try:
        assert isinstance(r.json()["frozen_results"], int) and r.json()["frozen_results"] > 0
        p = {"period_start": "2025-09-01", "period_end": "2025-09-30"}
        admin = auth("ex.admin@exact.local")
        assert client.get("/api/v1/kpi/evaluate/KPI-016", params=p, headers=admin).json().get("frozen") is True
        for code, eid in ids["exact"]["entities"].items():
            res = client.get("/api/v1/kpi/evaluate/KPI-016", params={**p, "entity_ids": eid}, headers=admin).json()
            assert res.get("frozen") is True, code
    finally:
        assert client.post("/api/v1/kpi/periods/2025-09-01/reopen", json={"reason": "test cleanup"},
                           headers=h).status_code == 200


def test_d16_can_close_periods_unit(db, ids, monkeypatch):
    """Unit: flag off -> only tenant-wide; flag on -> D-2 (main_contractor in scope) unchanged."""
    from app.analytics import engine
    from app.core.config import get_settings
    from app.security.access import AccessContext

    t, e = ids["exact"]["tenant_id"], ids["exact"]["entities"]

    def ctx(scope):
        return AccessContext(user_id=None, tenant_id=t, email="t", locale="en", grants={"period.close": scope})

    assert engine.can_close_periods(db, ctx(None)) is True
    assert engine.can_close_periods(db, ctx({e["A"]})) is False
    assert engine.can_close_periods(db, ctx(set(e.values()))) is False  # even every firm is not "tenant-wide"
    monkeypatch.setattr(get_settings(), "feature_subcontractors", True)
    assert engine.can_close_periods(db, ctx({e["A"]})) is True  # A = main_contractor (D-2)
    assert engine.can_close_periods(db, ctx({e["B"]})) is False
