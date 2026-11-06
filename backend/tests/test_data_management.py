"""Data management API (/api/v1/manage): users & roles (privilege-escalation guard), identity review queue,
manual person / employment writes (scope, optimistic concurrency, transfer semantics, cache invalidation),
legal entities and contractor relationships.

All fixture data lives in NEW legal entities created through the API and uses 2026 dates, so the hand-computed
EXACT-tenant expectations (Q1 2025 / June 2025, entities A/B/C/F) used by other test modules stay valid.
"""
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    EmploymentRelationship,
    IdentityReviewItem,
    KpiResult,
    Occupation,
    Person,
    PersonIdentifier,
    Project,
    Tenant,
    UserRoleAssignment,
)
from app.security.auth import national_id_hash

ADMIN = "ex.admin@exact.local"
HRMAN = "ex.hrman@exact.local"  # HR_MANAGER scoped to entity A
CMGR_B = "ex.b@exact.local"  # CONTRACTOR_MANAGER scoped to B (no people.write)
M = "/api/v1/manage"


def _uid() -> str:
    return uuid.uuid4().hex[:6].upper()


def _login(client, email: str, password: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _entity(client, auth, code_prefix="DM") -> dict:
    code = f"{code_prefix}-{_uid()}"
    r = client.post(f"{M}/legal-entities", headers=auth(ADMIN),
                    json={"code": code, "name": f"Data mgmt {code}", "entity_type": "subcontractor", "country": "tr"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"] == code and body["country"] == "TR"
    return body


def _person(client, headers, entity_id, start="2026-01-01", **extra) -> dict:
    emp = {"legal_entity_id": entity_id, "start_date": start, "contract_type": "permanent"}
    emp.update(extra.pop("employment", {}))
    body = {"first_name": "Dm", "last_name": f"Person{_uid()}", "employment": emp, **extra}
    r = client.post(f"{M}/people", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _version(db, employment_id: int) -> int:
    db.expire_all()
    return db.get(EmploymentRelationship, employment_id).record_version


def _audit_count(db, action: str, entity_id=None, outcome="allowed") -> int:
    q = select(func.count()).select_from(AuditLog).where(AuditLog.action == action, AuditLog.outcome == outcome)
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == str(entity_id))
    return db.execute(q).scalar()


def _new_user(client, auth, role_code=None, scope_entity=None) -> tuple[int, str, dict]:
    email = f"dm.{_uid().lower()}@exact.local"
    r = client.post(f"{M}/users", headers=auth(ADMIN), json={"email": email, "full_name": "DM user", "locale": "en"})
    assert r.status_code == 201, r.text
    uid, pw = r.json()["id"], r.json()["initial_password"]
    if role_code:
        g = client.post(f"{M}/users/{uid}/roles", headers=auth(ADMIN),
                        json={"role_code": role_code, "scope_type": "legal_entity" if scope_entity else "tenant",
                              "legal_entity_id": scope_entity})
        assert g.status_code == 201, g.text
    return uid, email, _login(client, email, pw)


@pytest.fixture(scope="module")
def ents(client, auth):
    return {"t1": _entity(client, auth)["id"], "t2": _entity(client, auth)["id"]}


# ------------------------------------------------------------------ users & roles


def test_users_roles_grant_and_revoke(client, auth, ids, db):
    users = client.get(f"{M}/users", headers=auth(ADMIN))
    assert users.status_code == 200
    emails = {u["email"] for u in users.json()}
    assert ADMIN in emails and "admin@demo.local" not in emails  # tenant isolation
    admin_row = next(u for u in users.json() if u["email"] == ADMIN)
    assert admin_row["roles"][0]["role_code"] == "SYSTEM_ADMIN" and admin_row["roles"][0]["scope_type"] == "tenant"

    roles = client.get(f"{M}/roles", headers=auth(ADMIN)).json()
    hrm = next(r for r in roles if r["code"] == "HR_MANAGER")
    assert "people.write" in hrm["permissions"] and "admin.users" not in hrm["permissions"]

    email = f"dm.new.{_uid().lower()}@exact.local"
    r = client.post(f"{M}/users", headers=auth(ADMIN), json={"email": email, "full_name": "New User", "locale": "tr"})
    assert r.status_code == 201
    new_id, pw = r.json()["id"], r.json()["initial_password"]
    assert len(pw) >= 12
    dup = client.post(f"{M}/users", headers=auth(ADMIN), json={"email": email.upper(), "full_name": "x", "locale": "tr"})
    assert dup.status_code == 409
    assert client.post(f"{M}/users", headers=auth(ADMIN),
                       json={"email": "not-an-email", "full_name": "x", "locale": "tr"}).status_code == 422

    a_id = ids["exact"]["entities"]["A"]
    g = client.post(f"{M}/users/{new_id}/roles", headers=auth(ADMIN),
                    json={"role_code": "HR_SPECIALIST", "scope_type": "legal_entity", "legal_entity_id": a_id})
    assert g.status_code == 201, g.text
    asg = g.json()
    assert asg["role_code"] == "HR_SPECIALIST" and asg["legal_entity_id"] == a_id and asg["valid_to"] is None
    # identical active grant -> 409; inconsistent scope -> 422
    assert client.post(f"{M}/users/{new_id}/roles", headers=auth(ADMIN),
                       json={"role_code": "HR_SPECIALIST", "scope_type": "legal_entity",
                             "legal_entity_id": a_id}).status_code == 409
    assert client.post(f"{M}/users/{new_id}/roles", headers=auth(ADMIN),
                       json={"role_code": "HR_SPECIALIST", "scope_type": "tenant",
                             "legal_entity_id": a_id}).status_code == 422
    # the new user can log in with the one-time password and has the scoped grant
    me = client.get("/api/v1/auth/me", headers=_login(client, email, pw)).json()
    assert me["scopes"]["people.write"] != "tenant" and a_id in me["scopes"]["people.write"]

    rv = client.delete(f"{M}/role-assignments/{asg['assignment_id']}", headers=auth(ADMIN))
    assert rv.status_code == 200 and rv.json()["valid_to"] == date.today().isoformat()
    assert client.delete(f"{M}/role-assignments/{asg['assignment_id']}", headers=auth(ADMIN)).status_code == 409
    db.expire_all()
    assert db.get(UserRoleAssignment, asg["assignment_id"]) is not None  # never deleted
    me = client.get("/api/v1/auth/me", headers=_login(client, email, pw)).json()
    assert "people.write" not in me["permissions"]

    for action, eid in [("admin.user_create", new_id), ("admin.role_grant", asg["assignment_id"]),
                        ("admin.role_revoke", asg["assignment_id"])]:
        assert _audit_count(db, action, eid) == 1


def test_cross_tenant_and_missing_permission(client, auth, ids):
    exact_admin_id = ids["exact"]["users"][ADMIN]
    r = client.post(f"{M}/users/{exact_admin_id}/roles", headers=auth("admin@demo.local"),
                    json={"role_code": "READONLY_EXECUTIVE", "scope_type": "tenant", "legal_entity_id": None})
    assert r.status_code == 404
    assert client.get(f"{M}/users", headers=auth(HRMAN)).status_code == 403
    assert client.post(f"{M}/legal-entities", headers=auth(HRMAN),
                       json={"code": "NOPE", "name": "x", "entity_type": "employer", "country": "TR"}).status_code == 403


def test_privilege_escalation_guard(client, auth, ents, ids, db, subcontractors_on):
    t1, t2 = ents["t1"], ents["t2"]
    scoped_admin_id, _, sa = _new_user(client, auth, "COMPANY_ADMIN", t1)
    target = client.post(f"{M}/users", headers=sa, json={"email": f"dm.t.{_uid().lower()}@exact.local",
                                                         "full_name": "Target", "locale": "en"})
    assert target.status_code == 201
    tid = target.json()["id"]
    denied_before = _audit_count(db, "authz.scope_denied", outcome="denied")

    def grant(role, scope_type, ent):
        return client.post(f"{M}/users/{tid}/roles", headers=sa,
                           json={"role_code": role, "scope_type": scope_type, "legal_entity_id": ent})

    assert grant("COMPANY_ADMIN", "tenant", None).status_code == 403  # tenant scope from entity-scoped admin
    assert grant("COMPANY_ADMIN", "legal_entity", t2).status_code == 403  # entity outside own scope
    assert grant("HR_MANAGER", "legal_entity", t1).status_code == 403  # people.write etc. not held
    assert grant("SYSTEM_ADMIN", "legal_entity", t1).status_code == 403
    r = grant("READONLY_EXECUTIVE", "legal_entity", t1)
    assert r.status_code == 403 and "kpi.read" in r.json()["detail"]
    ok = grant("COMPANY_ADMIN", "legal_entity", t1)  # subset of own permissions, inside own scope
    assert ok.status_code == 201, ok.text
    assert _audit_count(db, "authz.scope_denied", outcome="denied") >= denied_before + 5

    # cannot revoke a tenant-wide grant of someone else
    sys_asg = db.execute(select(UserRoleAssignment.id).where(
        UserRoleAssignment.user_id == ids["exact"]["users"][ADMIN])).scalars().first()
    assert client.delete(f"{M}/role-assignments/{sys_asg}", headers=sa).status_code == 403
    # cannot revoke own last admin.users grant
    own = db.execute(select(UserRoleAssignment.id).where(UserRoleAssignment.user_id == scoped_admin_id)).scalar()
    assert client.delete(f"{M}/role-assignments/{own}", headers=sa).status_code == 409
    # entity-scoped org.write: no new entities, no relationships that reach outside the scope
    assert client.post(f"{M}/legal-entities", headers=sa, json={"code": f"X-{_uid()}", "name": "x",
                                                               "entity_type": "employer", "country": "TR"}
                       ).status_code == 403
    assert client.post(f"{M}/contractor-relationships", headers=sa,
                       json={"parent_entity_id": t1, "child_entity_id": t2, "tier": 1, "valid_from": "2026-01-01",
                             "data_sharing_granted": True}).status_code == 403


# ------------------------------------------------------------------ people & employments


def test_create_person_scope_and_national_id(client, auth, ents, db):
    t1, t2 = ents["t1"], ents["t2"]
    body = {"first_name": "Nid", "last_name": "Owner", "national_id": "55500011122", "birth_year": 1990,
            "gender": "f", "employment": {"legal_entity_id": t1, "start_date": "2026-02-01",
                                          "contract_type": "fixed_term", "employee_number": "E-1"}}
    # contractor manager (no people.write) and HR manager scoped to A cannot create people in T1
    assert client.post(f"{M}/people", headers=auth(CMGR_B), json=body).status_code == 403
    assert client.post(f"{M}/people", headers=auth(HRMAN), json=body).status_code == 403
    # a people.write user scoped to T1 cannot create in T2
    _, _, spec = _new_user(client, auth, "HR_SPECIALIST", t1)
    other = {**body, "national_id": None, "employment": {**body["employment"], "legal_entity_id": t2}}
    r = client.post(f"{M}/people", headers=spec, json=other)
    assert r.status_code == 403
    assert client.post(f"{M}/people", headers=spec, json={**other, "employment": {**other["employment"],
                                                                                    "legal_entity_id": t1}}
                       ).status_code == 201

    res = client.post(f"{M}/people", headers=auth(ADMIN), json=body)
    assert res.status_code == 201, res.text
    pid, eid = res.json()["person_id"], res.json()["employment_id"]
    p = db.get(Person, pid)
    assert p.national_id_hash == national_id_hash("55500011122") and p.gender == "F" and p.birth_year == 1990
    emp = db.get(EmploymentRelationship, eid)
    assert emp.legal_entity_id == t1 and str(emp.start_date) == "2026-02-01" and emp.contract_type == "fixed_term"
    dup = client.post(f"{M}/people", headers=auth(ADMIN), json=body)
    assert dup.status_code == 409 and dup.json()["detail"]["candidate_person_id"] == pid
    # validation
    bad = {**body, "national_id": None, "employment": {**body["employment"], "contract_type": "gig"}}
    assert client.post(f"{M}/people", headers=auth(ADMIN), json=bad).status_code == 422
    assert client.post(f"{M}/people", headers=auth(ADMIN), json={**body, "national_id": None, "birth_year": 1800}
                       ).status_code == 422
    # the raw identity number never reaches the audit trail
    details = db.execute(select(AuditLog.details).where(AuditLog.action == "people.create",
                                                        AuditLog.entity_id == str(pid))).scalar_one()
    assert "55500011122" not in json.dumps(details)


def test_terminate_validation_and_optimistic_concurrency(client, auth, ents, db):
    eid = _person(client, auth(ADMIN), ents["t1"], start="2026-01-05")["employment_id"]
    url = f"{M}/employments/{eid}/terminate"
    ok_body = {"end_date": "2026-05-31", "termination_category": "voluntary", "record_version": 1}
    assert client.post(url, headers=auth(ADMIN), json={**ok_body, "record_version": 2}).status_code == 409
    assert client.post(url, headers=auth(ADMIN), json={**ok_body, "termination_category": "fired"}).status_code == 422
    assert client.post(url, headers=auth(ADMIN), json={**ok_body, "end_date": "2026-01-01"}).status_code == 422
    assert client.post(url, headers=auth(HRMAN), json=ok_body).status_code == 403  # outside HR manager scope
    r = client.post(url, headers=auth(ADMIN), json=ok_body)
    assert r.status_code == 200, r.text
    assert r.json()["record_version"] == 2 and r.json()["end_date"] == "2026-05-31"
    # a second writer still holding version 1 loses
    assert client.post(url, headers=auth(ADMIN), json=ok_body).status_code == 409
    # back-dated correction with the current version
    r = client.post(url, headers=auth(ADMIN), json={"end_date": "2026-03-15", "termination_category": "contract_end",
                                                    "record_version": 2})
    assert r.status_code == 200 and r.json()["record_version"] == 3 and r.json()["end_date"] == "2026-03-15"
    assert _audit_count(db, "employment.terminate", eid) == 2
    assert client.post(f"{M}/employments/99999999/terminate", headers=auth(ADMIN), json=ok_body).status_code == 404


def _kpi001(client, auth, entity_ids) -> dict:
    r = client.get("/api/v1/kpi/evaluate/KPI-001", headers=auth(ADMIN),
                   params={"period_start": "2026-01-01", "period_end": "2026-06-30",
                           "entity_ids": ",".join(map(str, entity_ids))})
    assert r.status_code == 200, r.text
    return r.json()


def test_transfer_is_internal_and_termination_invalidates_cache(client, auth, ids, db):
    """Transfer between two in-scope entities is not a separation for KPI-001; a later termination is picked up
    immediately on re-evaluation (cache invalidated through mark_stale_from)."""
    t3, t4 = _entity(client, auth, "TR")["id"], _entity(client, auth, "TR")["id"]
    tenant_id = ids["exact"]["tenant_id"]
    proj = Project(tenant_id=tenant_id, code=f"DMP-{_uid()}", name="DM project", owner_entity_id=t3)
    occ = Occupation(tenant_id=tenant_id, code=f"DMO-{_uid()}", name="DM welder", occupation_group="construction")
    db.add_all([proj, occ])
    db.commit()
    h = auth(ADMIN)
    mover = _person(client, h, t3, employment={"project_id": proj.id, "occupation_id": occ.id})
    for _ in range(5):
        _person(client, h, t3)
    t4_emps = [_person(client, h, t4)["employment_id"] for _ in range(6)]

    before_both = _kpi001(client, auth, [t3, t4])
    assert before_both["status"] == "ok" and before_both["numerator"] == 0

    url = f"{M}/employments/{mover['employment_id']}/transfer"
    body = {"target_legal_entity_id": t4, "transfer_date": "2026-03-01", "record_version": 1}
    assert client.post(url, headers=h, json={**body, "record_version": 9}).status_code == 409
    assert client.post(url, headers=h, json={**body, "target_legal_entity_id": t3}).status_code == 422
    assert client.post(url, headers=h, json={**body, "transfer_date": "2026-01-01"}).status_code == 422
    assert client.post(url, headers=auth(HRMAN), json=body).status_code == 403
    r = client.post(url, headers=h, json=body)
    assert r.status_code == 200, r.text
    ended, new = r.json()["ended_employment_id"], r.json()["new_employment_id"]
    db.expire_all()
    e_old, e_new = db.get(EmploymentRelationship, ended), db.get(EmploymentRelationship, new)
    assert str(e_old.end_date) == "2026-02-28" and e_old.termination_category == "other" and e_old.record_version == 2
    assert e_new.legal_entity_id == t4 and str(e_new.start_date) == "2026-03-01" and e_new.person_id == e_old.person_id
    hist = client.get(f"/api/v1/people/{mover['person_id']}/history", headers=h).json()
    new_hist = next(x for x in hist["employments"] if x["employment_id"] == new)
    assert new_hist["assignments"][0]["project_id"] == proj.id and new_hist["assignments"][0]["occupation_id"] == occ.id
    old_hist = next(x for x in hist["employments"] if x["employment_id"] == ended)
    assert old_hist["assignments"][0]["valid_to"] == "2026-03-01"

    # regression (A07 finding): an already-transferred/ended employment cannot be transferred again or terminated
    again = client.post(url, headers=h, json={**body, "record_version": 2})
    assert again.status_code == 409, again.text
    term = client.post(f"{M}/employments/{ended}/terminate", headers=h,
                       json={"end_date": "2026-02-20", "termination_category": "voluntary", "record_version": 2})
    assert term.status_code == 409
    back = client.post(f"{M}/employments/{new}/transfer", headers=h,
                       json={"target_legal_entity_id": t3, "transfer_date": "2026-03-01", "record_version": 1})
    assert back.status_code == 422  # transfer date must be after the new spell's start
    db.expire_all()
    assert db.execute(select(EmploymentRelationship).where(
        EmploymentRelationship.person_id == e_old.person_id)).scalars().all().__len__() == 2  # no duplicate spells

    both = _kpi001(client, auth, [t3, t4])
    assert both["status"] == "ok" and both["numerator"] == 0  # internal transfer, not a separation
    only_t3 = _kpi001(client, auth, [t3])
    assert only_t3["status"] == "ok" and only_t3["numerator"] == 1  # leaves T3's scope

    run = client.post("/api/v1/kpi/runs", headers=h, json={"codes": ["KPI-001"], "period_start": "2026-01-01",
                                                           "period_end": "2026-06-30", "entity_ids": [t3, t4]})
    assert run.status_code == 201
    dv_before = db.execute(select(Tenant.data_version).where(Tenant.id == tenant_id)).scalar()

    t = client.post(f"{M}/employments/{t4_emps[0]}/terminate", headers=h,
                    json={"end_date": "2026-04-30", "termination_category": "voluntary", "record_version": 1})
    assert t.status_code == 200
    db.expire_all()
    assert db.execute(select(Tenant.data_version).where(Tenant.id == tenant_id)).scalar() > dv_before
    stale = db.execute(select(KpiResult.is_stale).where(KpiResult.run_id == run.json()["run_id"])).scalars().all()
    assert stale and all(stale)
    after = _kpi001(client, auth, [t3, t4])
    assert after["numerator"] == 1 and after["value"] != both["value"]
    assert _audit_count(db, "employment.transfer", mover["employment_id"]) == 1


# ------------------------------------------------------------------ identity review queue


def _csv(rows, header="Sicil;Ad;Soyad;TC Kimlik;Firma;İşe Giriş;Çıkış;Çıkış Nedeni"):
    return (header + "\n" + "\n".join(";".join(r) for r in rows) + "\n").encode()


def test_identity_review_resolve_flow(client, auth, ents, db):
    t1 = ents["t1"]
    code = client.get("/api/v1/org/legal-entities", headers=auth(ADMIN)).json()
    t1_code = next(e["code"] for e in code if e["id"] == t1)
    tc = "7" + str(uuid.uuid4().int)[:10]
    owner = _person(client, auth(ADMIN), t1, start="2026-01-10", national_id=tc)
    u = _uid()
    content = _csv([[f"RV1-{u}", "Imp", "Mismatch", tc, t1_code, "2026-02-01", "", ""],
                    [f"RV2-{u}", "Imp", "Other", tc, t1_code, "2026-02-02", "", ""],
                    [f"RV3-{u}", "Imp", "Third", tc, t1_code, "2026-02-03", "", ""]])
    up = client.post("/api/v1/imports", headers=auth(ADMIN), files={"file": ("rv.csv", content)},
                     data={"entity_type": "employment"})
    assert up.status_code == 201, up.text
    bid = up.json()["batch_id"]
    client.post(f"/api/v1/imports/{bid}/validate", json={}, headers=auth(ADMIN))
    c = client.post(f"/api/v1/imports/{bid}/commit", headers=auth(ADMIN)).json()
    assert c["status"] == "committed" and c["identity_reviews"] == 3

    lst = client.get(f"{M}/identity-reviews", params={"status": "open"}, headers=auth(ADMIN))
    assert lst.status_code == 200
    assert "national_id_hash" not in lst.text and tc not in lst.text and national_id_hash(tc) not in lst.text
    items = {i["payload"]["external_id"]: i for i in lst.json() if i["import_batch_id"] == bid}
    assert len(items) == 3
    assert all(i["candidate_person_id"] == owner["person_id"] and i["status"] == "open" for i in items.values())
    assert "last name differs" in items[f"RV1-{u}"]["reason"]
    # entity-scoped people.write user only sees items of batches it created
    scoped = client.get(f"{M}/identity-reviews", headers=auth(HRMAN))
    assert scoped.status_code == 200 and not [i for i in scoped.json() if i["import_batch_id"] == bid]
    assert client.post(f"{M}/identity-reviews/{items[f'RV1-{u}']['id']}/resolve", headers=auth(HRMAN),
                       json={"action": "merge"}).status_code == 404
    assert client.get(f"{M}/identity-reviews", headers=auth(CMGR_B)).status_code == 403

    # merge -> employment attached to the candidate + identifier mapping
    rid = items[f"RV1-{u}"]["id"]
    r = client.post(f"{M}/identity-reviews/{rid}/resolve", headers=auth(ADMIN), json={"action": "merge"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "merged" and r.json()["person_id"] == owner["person_id"]
    emp = db.get(EmploymentRelationship, r.json()["employment_id"])
    assert emp.person_id == owner["person_id"] and str(emp.start_date) == "2026-02-01" and emp.legal_entity_id == t1
    assert db.execute(select(PersonIdentifier).where(PersonIdentifier.external_id == f"RV1-{u}")).scalar_one(
    ).person_id == owner["person_id"]
    assert client.post(f"{M}/identity-reviews/{rid}/resolve", headers=auth(ADMIN),
                       json={"action": "reject"}).status_code == 409

    # create_new -> separate person; colliding hash is not duplicated
    r2 = client.post(f"{M}/identity-reviews/{items[f'RV2-{u}']['id']}/resolve", headers=auth(ADMIN),
                     json={"action": "create_new"})
    assert r2.status_code == 200 and r2.json()["status"] == "created"
    newp = db.get(Person, r2.json()["person_id"])
    assert newp.id != owner["person_id"] and newp.last_name == "Other" and newp.national_id_hash is None
    assert db.get(EmploymentRelationship, r2.json()["employment_id"]).person_id == newp.id

    # reject -> nothing created
    r3 = client.post(f"{M}/identity-reviews/{items[f'RV3-{u}']['id']}/resolve", headers=auth(ADMIN),
                     json={"action": "reject"})
    assert r3.status_code == 200 and r3.json()["status"] == "rejected"
    assert db.execute(select(PersonIdentifier).where(PersonIdentifier.external_id == f"RV3-{u}")).scalar() is None
    assert client.post(f"{M}/identity-reviews/{rid}/resolve", headers=auth(ADMIN),
                       json={"action": "bogus"}).status_code == 422

    db.expire_all()
    statuses = {i.status for i in db.execute(select(IdentityReviewItem).where(
        IdentityReviewItem.import_batch_id == bid)).scalars()}
    assert statuses == {"merged", "created", "rejected"}
    assert not [i for i in client.get(f"{M}/identity-reviews", headers=auth(ADMIN)).json() if i["import_batch_id"] == bid]
    assert _audit_count(db, "identity_review.resolve", rid) == 1


# ------------------------------------------------------------------ organisation


def test_legal_entities_and_contractor_relationships(client, auth, db, subcontractors_on):
    h = auth(ADMIN)
    parent, child = _entity(client, auth, "CP"), _entity(client, auth, "CC")
    assert client.post(f"{M}/legal-entities", headers=h, json={"code": parent["code"].lower(), "name": "dup",
                                                              "entity_type": "employer", "country": "TR"}
                       ).status_code == 409
    assert client.post(f"{M}/legal-entities", headers=h, json={"code": "OK1", "name": "x", "entity_type": "alien",
                                                              "country": "TR"}).status_code == 422
    base = {"parent_entity_id": parent["id"], "child_entity_id": child["id"], "tier": 1, "valid_from": "2026-01-01",
            "data_sharing_granted": True, "scope_of_work": "Steel works"}
    assert client.post(f"{M}/contractor-relationships", headers=h,
                       json={**base, "child_entity_id": parent["id"]}).status_code == 422
    r = client.post(f"{M}/contractor-relationships", headers=h, json=base)
    assert r.status_code == 201, r.text
    rel = r.json()
    assert rel["valid_to"] is None and rel["data_sharing_granted"] is True and rel["tier"] == 1
    ov = client.post(f"{M}/contractor-relationships", headers=h, json={**base, "valid_from": "2026-06-01"})
    assert ov.status_code == 409
    end_url = f"{M}/contractor-relationships/{rel['id']}/end"
    assert client.post(end_url, headers=h, json={"valid_to": "2026-01-01"}).status_code == 422
    e = client.post(end_url, headers=h, json={"valid_to": "2026-07-01"})
    assert e.status_code == 200 and e.json()["valid_to"] == "2026-07-01"
    assert client.post(end_url, headers=h, json={"valid_to": "2026-08-01"}).status_code == 409
    # a new relationship after the ended one does not overlap
    nxt = client.post(f"{M}/contractor-relationships", headers=h, json={**base, "valid_from": "2026-07-01"})
    assert nxt.status_code == 201
    assert client.post(f"{M}/contractor-relationships/99999999/end", headers=h,
                       json={"valid_to": "2026-08-01"}).status_code == 404
    assert _audit_count(db, "org.legal_entity_create", parent["id"]) == 1
    assert _audit_count(db, "org.contractor_relationship_create", rel["id"]) == 1
    assert _audit_count(db, "org.contractor_relationship_end", rel["id"]) == 1


def test_deactivate_user_blocks_login_and_tokens(client, auth, db):
    h = auth(ADMIN)
    created = client.post(f"{M}/users", headers=h, json={"email": f"deact.{_uid()}@exact.local", "full_name": "Temp",
                                                         "locale": "en"}).json()
    email = next(u["email"] for u in client.get(f"{M}/users", headers=h).json() if u["id"] == created["id"])
    tok = client.post("/api/v1/auth/login", json={"email": email, "password": created["initial_password"]}).json()
    me = {"Authorization": f"Bearer {tok['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=me).status_code == 200
    assert client.post(f"{M}/users/{created['id']}/deactivate", headers=auth(HRMAN)).status_code == 403
    r = client.post(f"{M}/users/{created['id']}/deactivate", headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/api/v1/auth/me", headers=me).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": email, "password": created["initial_password"]}).status_code == 401
    assert client.post(f"{M}/users/{created['id']}/deactivate", headers=h).status_code == 409
