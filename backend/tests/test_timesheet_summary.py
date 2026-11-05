"""B-21: the timesheet monthly summary is a pure optimisation - it must give exactly the raw-table numbers, and DB
triggers must keep stale months off the fast path no matter who writes timesheets."""
from datetime import date

import pytest
from sqlalchemy import select, text

from app.analytics import calculators, summary
from app.analytics.calculators import Scope, _hours_query
from app.analytics.domain import month_end, month_starts
from app.core.cache import cache
from app.models import Tenant

KEYS = ("rows", "planned", "worked", "overtime", "productive", "productive_rows", "planned_with_productive",
        "persons", "night")


@pytest.fixture()
def demo_tid(db):
    return db.execute(select(Tenant.id).where(Tenant.code == "DEMO")).scalar_one()


def _months(ps, pe):
    return [(m, month_end(m)) for m in month_starts(ps, pe)]


def _raw(db, scope, windows):
    r = _hours_query(db, scope, windows, summary=False)
    return {w: r.get(w) for w in windows}


def _sum(db, scope, windows):
    r = _hours_query(db, scope, windows, summary=True)
    return {w: r.get(w) for w in windows}


def test_summary_equals_raw_for_months_and_year(db, demo_tid):
    summary.refresh(db)  # generator already refreshed; make the test independent of ordering
    windows = [*_months(date(2025, 11, 1), date(2026, 10, 31)), (date(2025, 11, 1), date(2026, 10, 31))]
    ents = db.execute(text("SELECT id FROM legal_entities WHERE tenant_id = :t AND entity_type = 'subcontractor' "
                           "ORDER BY id LIMIT 2"), {"t": demo_tid}).scalars().all()
    proj = db.execute(text("SELECT id FROM projects WHERE tenant_id = :t ORDER BY id LIMIT 1"), {"t": demo_tid}).scalar()
    for scope in (Scope(demo_tid), Scope(demo_tid, frozenset(ents)), Scope(demo_tid, None, proj),
                  Scope(demo_tid, None, None, "construction")):
        assert _sum(db, scope, windows) == _raw(db, scope, windows), scope


def test_fast_path_used_only_for_clean_whole_months(db, demo_tid, monkeypatch):
    summary.refresh(db)
    calls = []
    real = calculators._hours_query
    monkeypatch.setattr(calculators, "_hours_query", lambda d, s, w, summary: calls.append((summary, w)) or real(
        d, s, w, summary=summary))
    calculators._hours_windows(db, Scope(demo_tid), [(date(2026, 3, 1), date(2026, 3, 31)),
                                                     (date(2026, 3, 1), date(2026, 3, 15))])
    assert (True, [(date(2026, 3, 1), date(2026, 3, 31))]) in calls
    assert (False, [(date(2026, 3, 1), date(2026, 3, 15))]) in calls  # partial month -> raw


def test_triggers_mark_dirty_and_results_stay_exact(db, demo_tid):
    summary.refresh(db)
    emp = db.execute(text("SELECT id FROM employment_relationships WHERE tenant_id = :t AND end_date IS NULL "
                          "ORDER BY id LIMIT 1"), {"t": demo_tid}).scalar()
    march = (date(2026, 3, 1), date(2026, 3, 31))
    before = calculators._hours_windows(db, Scope(demo_tid), [march])[march]
    # a late timesheet row written directly (as any future feed would) -> trigger marks March dirty
    db.execute(text("INSERT INTO timesheets (tenant_id, employment_id, work_date, planned_hours, worked_hours, "
                    "overtime_hours, night_hours, status, source_system, record_version, created_at, updated_at) "
                    "VALUES (:t, :e, '2026-03-28', 9, 13, 4, 0, 'approved', 'test', 1, now(), now())"),
                   {"t": demo_tid, "e": emp})
    db.commit()
    db.info.pop("ts_dirty", None)
    assert summary.dirty_from(db, demo_tid) == date(2026, 3, 1)
    after = calculators._hours_windows(db, Scope(demo_tid), [march])[march]  # served from raw: includes the row
    assert after["overtime"] == pytest.approx(before["overtime"] + 4)
    assert after == _raw_dict(db, demo_tid, march)
    assert summary.refresh(db, demo_tid) == {demo_tid: date(2026, 3, 1)}
    assert summary.dirty_from(db, demo_tid) is None
    assert calculators._hours_windows(db, Scope(demo_tid), [march])[march] == after  # fast path, same numbers
    # update and delete are tracked too
    db.execute(text("UPDATE timesheets SET overtime_hours = 5 WHERE source_system = 'test' AND tenant_id = :t"),
               {"t": demo_tid})
    db.commit()
    db.info.pop("ts_dirty", None)
    assert summary.dirty_from(db, demo_tid) == date(2026, 3, 1)
    db.execute(text("DELETE FROM timesheets WHERE source_system = 'test' AND tenant_id = :t"), {"t": demo_tid})
    db.commit()
    summary.refresh(db, demo_tid)
    assert calculators._hours_windows(db, Scope(demo_tid), [march])[march] == before


def _raw_dict(db, tid, w):
    r = _hours_query(db, Scope(tid), [w], summary=False)[w]
    return dict(zip(KEYS, (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5]), float(r[6]),
                           int(r[7]), float(r[8])), strict=True))


def test_kpis_identical_on_both_paths(client, auth, db, demo_tid, monkeypatch):
    """End to end: every hours-based KPI returns the same result with the summary disabled."""
    summary.refresh(db)
    monkeypatch.setattr(cache, "enabled", False)
    codes = ["KPI-003", "KPI-004", "KPI-007", "KPI-014A", "KPI-014B", "KPI-028", "KPI-032", "KPI-034"]
    params = {"period_start": "2025-11-01", "period_end": "2026-10-31"}
    h = auth("admin@demo.local")
    fast = {c: client.get(f"/api/v1/kpi/evaluate/{c}", params=params, headers=h).json() for c in codes}
    series_fast = client.get("/api/v1/kpi/series/KPI-007", params=params, headers=h).json()
    monkeypatch.setattr(calculators, "dirty_from", lambda d, t: date(1900, 1, 1))  # force raw path everywhere
    slow = {c: client.get(f"/api/v1/kpi/evaluate/{c}", params=params, headers=h).json() for c in codes}
    series_slow = client.get("/api/v1/kpi/series/KPI-007", params=params, headers=h).json()
    for c in codes:
        assert (fast[c]["status"], fast[c]["value"], fast[c]["sample_size"]) == \
               (slow[c]["status"], slow[c]["value"], slow[c]["sample_size"]), c
    assert [s["value"] for s in series_fast] == [s["value"] for s in series_slow]


def test_prewarm_fills_cache_for_each_user_scope(db, demo_tid, monkeypatch):
    from app.services import prewarm

    cache.clear_local()
    res = prewarm.warm_tenant(demo_tid)
    assert res["scopes_warmed"] >= 3  # tenant-wide, main-contractor-scoped and subcontractor-scoped users
    from app.analytics import dashboards
    from app.models import User
    from app.security.access import build_access_context

    user = db.execute(select(User).where(User.email == "mc.ops@demo.local")).scalar_one()
    ctx = build_access_context(db, user)
    ps, pe = dashboards.default_period()
    calls = []
    monkeypatch.setattr(calculators, "_hours_query", lambda *a, **k: calls.append(1))
    dashboards.executive(db, ctx, ps, pe)  # must be served from the warmed cache: no timesheet query at all
    assert calls == []


def test_dashboard_response_cache_respects_scope_and_period_close(client, auth, ids):
    """Whole-dashboard cache: never shared across scopes; closing/reopening a period invalidates it."""
    p = {"period_start": "2025-01-01", "period_end": "2025-03-31"}
    admin, b = auth("ex.admin@exact.local"), auth("ex.b@exact.local")
    d_admin = client.get("/api/v1/dashboard/executive", params=p, headers=admin).json()
    d_b = client.get("/api/v1/dashboard/executive", params=p, headers=b).json()
    hc = lambda d: next(t for t in d["tiles"] if t["code"] == "KPI-016")["value"]  # noqa: E731
    assert hc(d_admin) != hc(d_b)  # different scopes -> different cached payloads
    assert {e["entity"] for e in d_b["headcount_by_entity"]} == {"Entity B"}
    assert client.get("/api/v1/dashboard/executive", params=p, headers=b).json() == d_b  # cached, identical
    hr = auth("ex.hrdir@exact.local")
    assert client.post("/api/v1/kpi/periods/2025-02-01/close", headers=hr).status_code == 200
    try:
        r = client.get("/api/v1/dashboard/executive",
                       params={"period_start": "2025-02-01", "period_end": "2025-02-28"}, headers=admin).json()
        assert next(t for t in r["tiles"] if t["code"] == "KPI-016").get("frozen") is True
    finally:
        client.post("/api/v1/kpi/periods/2025-02-01/reopen", json={"reason": "test cleanup"}, headers=hr)
