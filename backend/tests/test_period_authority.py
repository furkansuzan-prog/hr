"""Decision 2026-11-05: closing/reopening periods is reserved for the main contractor."""
from sqlalchemy import select

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


def test_main_contractor_director_closes_for_everyone(client, auth, ids, db):
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
