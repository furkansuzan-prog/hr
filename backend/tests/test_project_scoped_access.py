"""ADR-017: subcontractor visibility through a project-limited contractor relationship (SUB-02, SPRINT1_REVIEW wave 1
item 2). Own hand-built tenant 'PRJ' so the shared EXACT / DEMO numbers stay untouched.

Entities: M (main contractor), S, T, T2, T3, W (subcontractors). Projects PA, PB. All relationships share data.
  M -> S   project PA      => S visible to M only for PA
  S -> T   project PA      => T visible to M only for PA (tier 2 on the same project)
  S -> T2  project PB      => T2 NOT visible to M (tier 2 on another project)
  S -> T3  project NULL    => T3 visible to M only for PA (inherits the parent's project)
  M -> W   project NULL    => W visible to M as a whole (backwards compatible)

People (employments 2024-01-01 -> open; assignment = the project in brackets, 2024-01-01 -> open):
  M: 5 [PA] | S: 6 [PA], 7 [PB], 2 [no project] | T: 5 [PA] | T2: 5 [PB] | T3: 5 [PA], 3 [PB] | W: 5 [no assignment]
  -> M's HR director sees M 5 + S 6 + T 5 + T3 5 + W 5 = 26 active people (tenant total = 43).

Timesheets Jan 2025 (one row per person):
  S [PA] people:          project PA,   planned 45, worked 50, overtime 5
  S [PB] people:          project PB,   planned 45, worked 60, overtime 20
  first S [PA] person:    project NULL, planned 45, worked 60, overtime 15 (extra row, NOT visible to M)
  -> KPI-007 for S as seen by M = 30 / 300 = 10.0 %;  S as seen by itself = (30 + 140 + 15) / (300 + 420 + 60)
Safety events Jan 2025 for S: 1 LTI on PA, 2 on PB, 1 with no project -> LTIFR seen by M = 1 / 300 h x 1e6.
"""
from datetime import date

import pytest
from sqlalchemy import select

from app.analytics import calculators, dashboards, engine, summary
from app.analytics.calculators import Scope
from app.models import (
    Assignment,
    ContractorRelationship,
    EmploymentRelationship,
    LegalEntity,
    Person,
    Project,
    RiskItem,
    SafetyEvent,
    Tenant,
    Timesheet,
    User,
    UserRoleAssignment,
)
from app.security.access import AccessContext, build_access_context
from app.security.auth import hash_password
from app.seed.reference import seed_permissions_and_roles, seed_reference
from tests.factories import declare_all_feeds

# D-9 / ADR-025: ADR-017 project limits only exist with the subcontractor feature on
pytestmark = pytest.mark.usefixtures("subcontractors_on")

D = date
JAN = {"period_start": "2025-01-01", "period_end": "2025-01-31"}


@pytest.fixture(scope="module")
def prj(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="PRJ", name="Project-scoped access test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        ents = {}
        for code, typ in [("M", "main_contractor"), ("S", "subcontractor"), ("T", "subcontractor"),
                          ("T2", "subcontractor"), ("T3", "subcontractor"), ("W", "subcontractor")]:
            ents[code] = LegalEntity(tenant_id=t.id, code=code, name=f"Entity {code}", entity_type=typ)
            db.add(ents[code])
        db.flush()
        projects = {}
        for code in ("PA", "PB"):
            projects[code] = Project(tenant_id=t.id, code=code, name=f"Project {code}", owner_entity_id=ents["M"].id)
            db.add(projects[code])
        db.flush()
        pa, pb = projects["PA"].id, projects["PB"].id
        for parent, child, proj in [("M", "S", pa), ("S", "T", pa), ("S", "T2", pb), ("S", "T3", None),
                                    ("M", "W", None)]:
            db.add(ContractorRelationship(tenant_id=t.id, parent_entity_id=ents[parent].id,
                                          child_entity_id=ents[child].id, project_id=proj, tier=1,
                                          data_sharing_granted=True, valid_from=D(2020, 1, 1)))
        n = 0
        emps: dict[tuple[str, str], list[EmploymentRelationship]] = {}

        def people(ent: str, count: int, project: int | str | None):
            nonlocal n
            out = []
            for _ in range(count):
                n += 1
                p = Person(tenant_id=t.id, person_code=f"PRJ{n:04d}", first_name="Prj", last_name=f"Person{n}")
                db.add(p)
                db.flush()
                e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ents[ent].id,
                                           start_date=D(2024, 1, 1))
                db.add(e)
                db.flush()
                if project != "none":
                    db.add(Assignment(tenant_id=t.id, employment_id=e.id, project_id=project,
                                      valid_from=D(2024, 1, 1)))
                out.append(e)
            return out

        emps["M", "PA"] = people("M", 5, pa)
        emps["S", "PA"] = people("S", 6, pa)
        emps["S", "PB"] = people("S", 7, pb)
        emps["S", "NULL"] = people("S", 2, None)
        emps["T", "PA"] = people("T", 5, pa)
        emps["T2", "PB"] = people("T2", 5, pb)
        emps["T3", "PA"] = people("T3", 5, pa)
        emps["T3", "PB"] = people("T3", 3, pb)
        emps["W", "-"] = people("W", 5, "none")
        # one S person on PA who also worked on PB: M must see only the PA assignment in the history
        both = emps["S", "PA"][1]
        db.add(Assignment(tenant_id=t.id, employment_id=both.id, project_id=pb, valid_from=D(2023, 1, 1),
                          valid_to=D(2024, 1, 1)))
        for e in emps["S", "PA"]:
            db.add(Timesheet(tenant_id=t.id, employment_id=e.id, project_id=pa, work_date=D(2025, 1, 6),
                             planned_hours=45, worked_hours=50, overtime_hours=5))
        for e in emps["S", "PB"]:
            db.add(Timesheet(tenant_id=t.id, employment_id=e.id, project_id=pb, work_date=D(2025, 1, 6),
                             planned_hours=45, worked_hours=60, overtime_hours=20))
        db.add(Timesheet(tenant_id=t.id, employment_id=emps["S", "PA"][0].id, project_id=None,
                         work_date=D(2025, 1, 7), planned_hours=45, worked_hours=60, overtime_hours=15))
        for proj, k in [(pa, 1), (pb, 2), (None, 1)]:
            for _ in range(k):
                db.add(SafetyEvent(tenant_id=t.id, legal_entity_id=ents["S"].id, project_id=proj,
                                   event_date=D(2025, 1, 10), event_type="lost_time_injury"))
        for proj, title in [(pa, "S on PA"), (pb, "S on PB"), (None, "S no project")]:
            db.add(RiskItem(tenant_id=t.id, legal_entity_id=ents["S"].id, project_id=proj, category="hse",
                            title=title, severity="low"))
        pw = hash_password("Demo!2345")
        users = {}
        for email, role, ent in [("m.hr@prj.local", "HR_DIRECTOR", "M"),
                                 ("s.mgr@prj.local", "CONTRACTOR_MANAGER", "S"),
                                 ("prj.admin@prj.local", "SYSTEM_ADMIN", None)]:
            u = User(tenant_id=t.id, email=email, full_name=email, password_hash=pw, locale="en")
            db.add(u)
            db.flush()
            users[email] = u.id
            db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role],
                                      scope_type="legal_entity" if ent else "tenant",
                                      legal_entity_id=ents[ent].id if ent else None))
        declare_all_feeds(db, t.id)  # ADR-020: every feed complete
        db.commit()
        return {"tenant_id": t.id, "e": {k: v.id for k, v in ents.items()}, "pa": pa, "pb": pb, "users": users,
                "both_person": both.person_id, "pb_person": emps["S", "PB"][0].person_id,
                "w_person": emps["W", "-"][0].person_id}


def _ctx(db, prj, email) -> AccessContext:
    return build_access_context(db, db.get(User, prj["users"][email]))


def test_access_context_project_limits(db, prj):
    e, pa = prj["e"], prj["pa"]
    ctx = _ctx(db, prj, "m.hr@prj.local")
    assert ctx.entity_filter("people.read") == {e["M"], e["S"], e["T"], e["T3"], e["W"]}  # T2: other project
    assert ctx.limits("people.read") == {e["S"]: {pa}, e["T"]: {pa}, e["T3"]: {pa}}
    assert ctx.can_see_entity("people.read", e["S"]) and not ctx.covers_entity("people.read", e["S"])
    assert ctx.covers_entity("people.read", e["W"]) and ctx.covers_entity("people.read", e["M"])
    # the subcontractor itself sees its own entity whole; its tier-2s are limited to their relationship's project
    s = _ctx(db, prj, "s.mgr@prj.local")
    assert s.entity_filter("people.read") == {e["S"], e["T"], e["T2"], e["T3"]}
    assert s.limits("people.read") == {e["T"]: {pa}, e["T2"]: {prj["pb"]}}
    me = s.limits("people.read")
    assert e["S"] not in me and e["T3"] not in me  # NULL-project relationship from a whole entity -> whole


def test_whole_entity_path_wins_and_limited_paths_union(db, prj):
    """A second, whole-entity relationship to S (project NULL) widens it back to the whole entity; two limited
    paths union their projects. Done inside a rolled-back transaction."""
    e, pa, pb = prj["e"], prj["pa"], prj["pb"]
    from app.security.access import _shared_subcontractors

    try:
        db.add(ContractorRelationship(tenant_id=prj["tenant_id"], parent_entity_id=e["M"], child_entity_id=e["T2"],
                                      project_id=pa, tier=1, data_sharing_granted=True, valid_from=D(2020, 1, 1)))
        db.flush()
        st = _shared_subcontractors(db, prj["tenant_id"], {e["M"]}, D(2025, 1, 1))
        assert st[e["T2"]] == {pa}  # via M->T2 on PA (S->T2 on PB is not followed from S limited to PA)
        db.add(ContractorRelationship(tenant_id=prj["tenant_id"], parent_entity_id=e["M"], child_entity_id=e["S"],
                                      project_id=pb, tier=1, data_sharing_granted=True, valid_from=D(2020, 1, 1)))
        db.flush()
        st = _shared_subcontractors(db, prj["tenant_id"], {e["M"]}, D(2025, 1, 1))
        assert st[e["S"]] == {pa, pb} and st[e["T2"]] == {pa, pb} and st[e["T3"]] == {pa, pb}
        db.add(ContractorRelationship(tenant_id=prj["tenant_id"], parent_entity_id=e["M"], child_entity_id=e["S"],
                                      project_id=None, tier=1, data_sharing_granted=True, valid_from=D(2020, 1, 1)))
        db.flush()
        st = _shared_subcontractors(db, prj["tenant_id"], {e["M"]}, D(2025, 1, 1))
        assert st[e["S"]] is None and st[e["T3"]] is None and st[e["T"]] == {pa}
    finally:
        db.rollback()


def test_project_scoped_sharing_hides_other_projects(client, auth, prj):
    """SUB-02 (B): sharing on project A exposes only the subcontractor's project-A workforce."""
    e = prj["e"]
    h = auth("m.hr@prj.local")
    people = client.get("/api/v1/people", params={"status": "all", "page_size": 200}, headers=h).json()
    by_ent: dict[int, int] = {}
    for p in people["items"]:
        by_ent[p["legal_entity_id"]] = by_ent.get(p["legal_entity_id"], 0) + 1
    assert by_ent == {e["M"]: 5, e["S"]: 6, e["T"]: 5, e["T3"]: 5, e["W"]: 5}
    s_only = client.get("/api/v1/people", params={"entity_id": e["S"], "status": "all"}, headers=h).json()
    assert s_only["total"] == 6
    assert client.get("/api/v1/people", params={"entity_id": e["T2"]}, headers=h).status_code == 403
    # headcount: S as seen by M = its PA people only; requesting PB on top of the PA limit gives nothing
    r = client.get("/api/v1/kpi/evaluate/KPI-016", params={"entity_ids": e["S"], **JAN}, headers=h).json()
    assert r["value"] == 6.0 and r["scope"]["project_limits"] == {str(e["S"]): [prj["pa"]]}
    r = client.get("/api/v1/kpi/evaluate/KPI-016", params={"entity_ids": e["S"], "project_id": prj["pb"], **JAN},
                   headers=h).json()
    assert r["value"] == 0.0
    total = client.get("/api/v1/kpi/evaluate/KPI-016", params=JAN, headers=h).json()
    assert total["value"] == 26.0
    # the subcontractor's own manager still sees all 15 of its people
    own = client.get("/api/v1/kpi/evaluate/KPI-016", params={"entity_ids": e["S"], **JAN},
                     headers=auth("s.mgr@prj.local")).json()
    assert own["value"] == 15.0 and "project_limits" not in own["scope"]


def test_project_limited_rows_by_row_project(client, auth, prj):
    """Timesheets, safety events and risks are attributed by their own project: PB and NULL-project rows hidden."""
    e = prj["e"]
    h = auth("m.hr@prj.local")
    ot = client.get("/api/v1/kpi/evaluate/KPI-007", params={"entity_ids": e["S"], **JAN}, headers=h).json()
    assert ot["value"] == 10.0  # 30 / 300: neither the PB rows nor the NULL-project row
    own = client.get("/api/v1/kpi/evaluate/KPI-007", params={"entity_ids": e["S"], **JAN},
                     headers=auth("s.mgr@prj.local")).json()
    assert own["value"] == round(100 * (30 + 140 + 15) / (300 + 420 + 60), 4)
    lt = client.get("/api/v1/kpi/evaluate/KPI-014A", params={"entity_ids": e["S"], **JAN}, headers=h).json()
    assert lt["breakdown"]["events_by_type"] == {"lost_time_injury": 1}
    assert lt["value"] == round(1 / 300 * 1_000_000, 4)
    titles = {r["title"] for r in client.get("/api/v1/risks", headers=h).json()}
    assert titles == {"S on PA"}


def test_tier2_visible_only_for_parent_project(client, auth, prj):
    e = prj["e"]
    h = auth("m.hr@prj.local")
    rows = client.get("/api/v1/kpi/by-entity/KPI-016", params=JAN, headers=h).json()
    got = {r["legal_entity"]["code"]: r["value"] for r in rows}
    assert got == {"M": 5.0, "S": 6.0, "T": 5.0, "T3": 5.0, "W": 5.0}  # T2 (project B) absent, T3 only its PA people
    assert client.get("/api/v1/kpi/evaluate/KPI-016", params={"entity_ids": e["T2"], **JAN},
                      headers=h).status_code == 403


def test_null_project_relationship_keeps_whole_entity(client, auth, prj):
    e = prj["e"]
    h = auth("m.hr@prj.local")
    r = client.get("/api/v1/kpi/evaluate/KPI-016", params={"entity_ids": e["W"], **JAN}, headers=h).json()
    assert r["value"] == 5.0 and "project_limits" not in r["scope"]  # W people have no assignment at all
    hist = client.get(f"/api/v1/people/{prj['w_person']}/history", headers=h)
    assert hist.status_code == 200


def test_person_history_respects_project_limits(client, auth, prj):
    h = auth("m.hr@prj.local")
    hist = client.get(f"/api/v1/people/{prj['both_person']}/history", headers=h).json()
    assert [a["project_id"] for a in hist["employments"][0]["assignments"]] == [prj["pa"]]
    assert client.get(f"/api/v1/people/{prj['pb_person']}/history", headers=h).status_code == 404
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["project_limits"]["people.read"][str(prj["e"]["S"])] == [prj["pa"]]


def test_dashboard_respects_project_limits(client, auth, prj):
    h = auth("m.hr@prj.local")
    d = client.get("/api/v1/dashboard/executive", params=JAN, headers=h)
    assert d.status_code == 200, d.text
    d = d.json()
    tiles = {t["code"]: t for t in d["tiles"]}
    assert tiles["KPI-016"]["value"] == 26.0
    assert {r["entity"]: r["value"] for r in d["headcount_by_entity"]} == {
        "Entity M": 5.0, "Entity S": 6.0, "Entity T": 5.0, "Entity T3": 5.0, "Entity W": 5.0}


def test_every_active_kpi_evaluates_under_project_limits(client, auth, prj):
    """SQL smoke test: every active calculator accepts a project-limited scope (no broken fragments)."""
    h = auth("m.hr@prj.local")
    defs = client.get("/api/v1/kpi/definitions", headers=h).json()
    active = [d["code"] for d in defs if d["status"] == "active"]
    assert len(active) > 20
    for code in active:
        r = client.get(f"/api/v1/kpi/evaluate/{code}", params=JAN, headers=h)
        assert r.status_code == 200, (code, r.text)
        r = client.get(f"/api/v1/kpi/evaluate/{code}", params={"entity_ids": prj["e"]["S"], **JAN}, headers=h)
        assert r.status_code == 200, (code, r.text)


def test_cache_cannot_leak_across_project_limits(db, prj):
    """Same entity set, different project limits -> different cache keys and numbers, in both orders."""
    e, pa, tid = prj["e"], prj["pa"], prj["tenant_id"]

    def ctx(limited: bool) -> AccessContext:
        return AccessContext(user_id=None, tenant_id=tid, email="t", locale="en",
                             grants={"kpi.read": {e["S"]}},
                             project_limits={"kpi.read": {e["S"]: frozenset({pa})}} if limited else {})

    ps, pe = D(2025, 1, 1), D(2025, 1, 31)
    full, lim = ctx(False), ctx(True)
    assert full.scope_signature("kpi.read") != lim.scope_signature("kpi.read")
    assert engine.evaluate(db, lim, "KPI-016", ps, pe)["value"] == 6.0
    db.info.clear()
    assert engine.evaluate(db, full, "KPI-016", ps, pe)["value"] == 15.0
    db.info.clear()
    assert engine.evaluate(db, lim, "KPI-016", ps, pe)["value"] == 6.0
    db.info.clear()
    d_full = dashboards.executive(db, full, ps, pe)
    db.info.clear()
    d_lim = dashboards.executive(db, lim, ps, pe)
    assert {t["code"]: t["value"] for t in d_full["tiles"]}["KPI-016"] == 15.0
    assert {t["code"]: t["value"] for t in d_lim["tiles"]}["KPI-016"] == 6.0


def test_summary_and_raw_timesheet_paths_agree_under_limits(db, prj):
    e, pa, tid = prj["e"], prj["pa"], prj["tenant_id"]
    summary.refresh(db, tid)
    scope = Scope(tid, frozenset({e["S"], e["W"]}), project_limits=((e["S"], (pa,)),))
    w = [(D(2025, 1, 1), D(2025, 1, 31))]
    raw = calculators._hours_query(db, scope, w, summary=False)
    fast = calculators._hours_query(db, scope, w, summary=True)
    assert raw == fast
    assert float(raw[w[0]][2]) == 300.0  # worked hours: S PA rows only (W has no timesheets)


def test_project_limited_entity_is_not_writable(db, prj):
    """Writes need whole-entity access: a project-limited grant never allows entity-wide writes or imports."""
    from app.services.imports import _import_scope

    e, pa = prj["e"], prj["pa"]
    ctx = AccessContext(user_id=None, tenant_id=prj["tenant_id"], email="t", locale="en",
                        grants={"import.write": {e["M"], e["S"]}, "people.write": {e["M"], e["S"]}},
                        project_limits={"import.write": {e["S"]: frozenset({pa})},
                                        "people.write": {e["S"]: frozenset({pa})}})
    assert _import_scope(ctx) == {e["M"]}
    from app.services.data_management import Forbidden, _require_entity_scope

    with pytest.raises(Forbidden):
        _require_entity_scope(ctx, "people.write", e["S"])
    _require_entity_scope(ctx, "people.write", e["M"])
    assert db.execute(select(LegalEntity.id).where(LegalEntity.id == e["S"])).scalar() == e["S"]
