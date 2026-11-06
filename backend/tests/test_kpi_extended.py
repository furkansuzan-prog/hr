"""B-10 KPIs and B-18 period close against the hand-computed KPIX tenant (arithmetic in tests/factories_kpi.py)."""
import pytest
from sqlalchemy import func, select

from app.analytics.catalog import KPI_CATALOG
from app.analytics.engine import validate_parameters
from app.models import KpiResult

ADMIN = "kx.admin@kpix.local"
HRDIR = "kx.hrdir@kpix.local"
MCDIR = "kx.mcdir@kpix.local"
MAR = {"period_start": "2025-03-01", "period_end": "2025-03-31"}
JAN24 = {"period_start": "2024-01-01", "period_end": "2024-01-31"}
NEW = ["KPI-020", "KPI-021", "KPI-022", "KPI-023", "KPI-028", "KPI-032", "KPI-034", "KPI-036", "KPI-048", "KPI-050"]
# counts that must never appear in the lineage of a suppressed / small-population result
LEAKY = {"separations", "persons", "unknown_gender", "hires", "early_leavers", "rows", "timesheet_rows",
         "downtime_events", "downtime_events_excluded_no_entity", "start_population", "retained", "by_cost_type"}


def ev(client, auth, code, user=ADMIN, **params):
    r = client.get(f"/api/v1/kpi/evaluate/{code}", params=params, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def ent(ids, code):
    return str(ids["kpi"]["entities"][code])


def test_new_kpis_active_validated_and_params_valid(client, auth):
    defs = {d["code"]: d for d in client.get("/api/v1/kpi/definitions", headers=auth(ADMIN)).json()}
    for code in NEW:
        assert defs[code]["status"] == "active" and defs[code]["validated"] is True, code
    assert defs["KPI-036"]["sensitive"] is True
    for k in KPI_CATALOG:
        validate_parameters(k["formula"]["parameters"])  # every catalog parameter passes the engine's schema


def test_kpi020_voluntary_turnover(client, auth, ids, db):
    from datetime import date

    from app.analytics.calculators import Scope, turnover_rate

    # formula v1 behaviour (include_categories only) on the same data
    db.info.clear()
    r = turnover_rate(db, Scope(ids["kpi"]["tenant_id"]), date(2025, 3, 1), date(2025, 3, 31),
                      {"include_categories": ["voluntary"]})
    assert r.status == "ok" and r.numerator == 2 and r.denominator == pytest.approx(22.0)
    assert r.value == pytest.approx(9.0909, abs=1e-4)
    assert r.quality_score == 75.0 and "NOT counted" in r.note
    assert r.lineage["included_categories"] == ["voluntary"]
    # v2 (ADR-023, WP-5 3.5): 1 of 4 separations has no category, u = 25 % > 20 % -> no value
    r = ev(client, auth, "KPI-020", **MAR)
    assert r["status"] == "insufficient_data" and r["value"] is None and r["quality_score"] == 75.0
    # KPI-001 semantics unchanged on the same data
    assert ev(client, auth, "KPI-001", **MAR)["value"] == pytest.approx(18.1818, abs=1e-4)
    # MC: L1, L2, T1 voluntary, L3 employer, L4 unknown -> u = 1/5 = 20 % (band edge): value, no colour, range
    r = ev(client, auth, "KPI-020", entity_ids=ent(ids, "MC"), **MAR)
    assert r["numerator"] == 3 and r["denominator"] == pytest.approx(13.5)
    assert r["value"] == pytest.approx(22.2222, abs=1e-4)
    assert r["annualized_value"] == pytest.approx(261.6485, abs=1e-3)
    assert (r["threshold_status"], r["status_reason"]) == (None, "unknown_category_share")
    assert r["breakdown"]["voluntary_range"] == [22.2222, 29.6296]


def test_include_categories_parameter_validated():
    validate_parameters({"include_categories": ["voluntary", "unknown"]})
    for bad in ([], ["quit"], "voluntary"):
        with pytest.raises(Exception, match="include_categories"):
            validate_parameters({"include_categories": bad})


def test_kpi021_022_early_turnover(client, auth, ids):
    r21, r22 = ev(client, auth, "KPI-021", **JAN24), ev(client, auth, "KPI-022", **JAN24)
    assert (r21["numerator"], r21["denominator"]) == (1, 12) and r21["value"] == pytest.approx(8.3333, abs=1e-4)
    assert (r22["numerator"], r22["denominator"]) == (3, 12) and r22["value"] == pytest.approx(25.0)
    assert r21["lineage"]["window_days"] == 30 and r22["lineage"]["window_days"] == 180
    mc = ent(ids, "MC")
    r21 = ev(client, auth, "KPI-021", entity_ids=mc, **JAN24)
    r22 = ev(client, auth, "KPI-022", entity_ids=mc, **JAN24)
    assert r21["value"] == pytest.approx(33.3333, abs=1e-4) and r22["value"] == pytest.approx(66.6667, abs=1e-4)


def test_kpi023_average_tenure_current_spell(client, auth, ids):
    r = ev(client, auth, "KPI-023", **MAR)
    assert r["status"] == "ok" and r["sample_size"] == 20 and r["denominator"] == 20
    assert r["value"] == pytest.approx(2.3504, abs=1e-4)
    r = ev(client, auth, "KPI-023", entity_ids=ent(ids, "MC"), **MAR)
    assert r["value"] == pytest.approx(3.3934, abs=1e-4) and r["sample_size"] == 11


def test_kpi028_sickness_and_kpi003_unchanged(client, auth, ids):
    r = ev(client, auth, "KPI-028", **MAR)
    assert r["value"] == pytest.approx(4.0)
    assert r["breakdown"]["included_types"] == ["sick"]
    assert r["breakdown"]["excluded_hours_by_type"] == {"annual": 8.0, "unexcused": 4.0}
    assert ev(client, auth, "KPI-003", **MAR)["value"] == pytest.approx(7.0)  # sick + unexcused + annual


def test_kpi032_night_work_share(client, auth, ids):
    r = ev(client, auth, "KPI-032", entity_ids=ent(ids, "MC"), **MAR)
    assert r["status"] == "ok" and r["value"] == pytest.approx(25.0)
    assert (r["numerator"], r["denominator"]) == (110.0, 440.0)


def test_kpi034_downtime_lost_hours_share(client, auth, ids):
    r = ev(client, auth, "KPI-034", **MAR)
    assert r["value"] == pytest.approx(9.0)
    assert r["breakdown"]["lost_hours_by_cause"] == {"equipment": 20.0, "weather": 10.0, "material": 6.0}
    r = ev(client, auth, "KPI-034", entity_ids=ent(ids, "MC"), **MAR)
    assert r["value"] == pytest.approx(7.5)
    assert r["lineage"]["downtime_events_excluded_no_entity"] == 1 and "no legal entity" in r["note"]
    r = ev(client, auth, "KPI-034", project_id=ids["kpi"]["project"], **MAR)
    assert r["value"] == pytest.approx(9.0)
    r = ev(client, auth, "KPI-034", occupation_group="office", **MAR)
    assert r["status"] == "insufficient_data" and r["value"] is None and "occupation" in r["note"]


def test_kpi036_overtime_cost_share(client, auth, ids):
    r = ev(client, auth, "KPI-036", **MAR)
    assert r["status"] == "ok" and r["value"] == pytest.approx(10.0)
    assert r["breakdown"]["total_labour_cost"] == pytest.approx(20000.0)
    assert r["lineage"]["source_currencies"] == ["CHF", "TRY"]
    r = ev(client, auth, "KPI-036", period_start="2025-02-01", period_end="2025-02-28")
    assert r["status"] == "insufficient_data" and r["value"] is None and "JPY->TRY" in r["note"]
    # sensitive: a user without cost.read is refused
    r = client.get("/api/v1/kpi/evaluate/KPI-036", params=MAR, headers=auth("kx.ops@kpix.local"))
    assert r.status_code == 403


def test_kpi048_female_share(client, auth, ids):
    r = ev(client, auth, "KPI-048", **MAR)
    assert r["status"] == "ok"
    assert (r["numerator"], r["denominator"]) == (8, 18) and r["value"] == pytest.approx(44.4444, abs=1e-4)
    assert r["lineage"]["unknown_gender"] == 2 and r["lineage"]["persons"] == 20
    assert r["quality_score"] == 90.0 and r["breakdown"] == {"female": 8, "male": 10}
    r = ev(client, auth, "KPI-048", entity_ids=ent(ids, "MC"), **MAR)
    assert r["value"] == pytest.approx(45.4545, abs=1e-4)
    # S1: 7 persons but only 3 women -> suppressed, and no count survives anywhere
    r = ev(client, auth, "KPI-048", entity_ids=ent(ids, "S1"), **MAR)
    assert r["status"] == "suppressed"
    assert r["value"] is r["numerator"] is r["denominator"] is r["sample_size"] is None
    assert r["breakdown"] == {} and not (LEAKY & set(r["lineage"]))


def test_kpi050_subcontractor_share(client, auth, ids):
    r = ev(client, auth, "KPI-050", **MAR)
    assert (r["numerator"], r["denominator"]) == (10, 20) and r["value"] == pytest.approx(50.0)
    r = ev(client, auth, "KPI-050", entity_ids=ent(ids, "MC"), **MAR)
    assert r["status"] == "ok" and r["value"] == 0.0


@pytest.mark.parametrize("code", NEW)
def test_new_kpis_leak_nothing_for_small_group(client, auth, ids, code):
    """S2 has 3 people: every new KPI is suppressed or insufficient and exposes no counts (enforce_privacy)."""
    # S2's 3 hires started 2024-06-01 -> a cohort below the minimum size
    period = {"period_start": "2024-06-01", "period_end": "2024-06-30"} if code in ("KPI-021", "KPI-022") else MAR
    r = ev(client, auth, code, entity_ids=ent(ids, "S2"), **period)
    assert r["status"] in ("suppressed", "insufficient_data"), r
    assert r["value"] is None and r["breakdown"] == {}
    if r["status"] == "suppressed":
        assert r["numerator"] is None and r["denominator"] is None and r["sample_size"] is None
        assert not (LEAKY & set(r["lineage"])), r["lineage"]
    else:  # no source data in S2 for this KPI: nothing beyond zero counts may be exposed
        assert r["numerator"] in (None, 0) and r["sample_size"] in (None, 0)
        assert all(r["lineage"].get(k) in (None, 0, []) for k in LEAKY - {"by_cost_type"}), r["lineage"]


def _results(db, tenant_id, ps):
    db.expire_all()
    return db.execute(select(func.count()).select_from(KpiResult).where(
        KpiResult.tenant_id == tenant_id, KpiResult.period_start == ps)).scalar()


def test_period_close_freezes_all_active_kpis_first(client, auth, ids, db):
    """B-18: close persists results for every active KPI at tenant scope and per legal entity, then closes."""
    from datetime import date

    active = [k for k in KPI_CATALOG if k["status"] == "active"]
    n_entities = len(ids["kpi"]["entities"])
    hr = auth(HRDIR)
    before = _results(db, ids["kpi"]["tenant_id"], date(2025, 3, 1))
    r = client.post("/api/v1/kpi/periods/2025-03-01/close", headers=hr)
    assert r.status_code == 200, r.text
    try:
        body = r.json()
        assert body["status"] == "closed" and body["period_month"] == "2025-03-01"
        assert body["frozen_results"] == len(active) * (1 + n_entities)
        assert _results(db, ids["kpi"]["tenant_id"], date(2025, 3, 1)) - before == body["frozen_results"]
        # closed month serves frozen values, for plain and sensitive KPIs, tenant and entity scope
        for code, params, value in [("KPI-020", {"entity_ids": ent(ids, "MC")}, 22.2222), ("KPI-036", {}, 10.0),
                                    ("KPI-048", {"entity_ids": ent(ids, "MC")}, 45.4545)]:
            res = ev(client, auth, code, **params, **MAR)
            assert res["frozen"] is True and res["value"] == pytest.approx(value, abs=1e-4), code
        # the colour-suppression reason survives freezing (ADR-018 rule 3)
        assert ev(client, auth, "KPI-020", entity_ids=ent(ids, "MC"), **MAR)["status_reason"] == "unknown_category_share"
        # suppressed results stay suppressed when served frozen
        res = ev(client, auth, "KPI-048", entity_ids=ent(ids, "S1"), **MAR)
        assert res["frozen"] is True and res["status"] == "suppressed" and not (LEAKY & set(res["lineage"]))
        assert client.post("/api/v1/kpi/periods/2025-03-01/close", headers=hr).status_code == 409
    finally:
        assert client.post("/api/v1/kpi/periods/2025-03-01/reopen", json={"reason": "test cleanup"},
                           headers=hr).status_code == 200
    # after reopen the month is live again
    assert "frozen" not in ev(client, auth, "KPI-020", **MAR)


def test_main_contractor_close_freezes_every_scope_including_cost(client, auth, ids, db):
    """Decision 2026-11-05 (supersedes the original B-18 rule): the main contractor owns period closing, so an
    MC-scoped closer freezes ALL active KPIs (cost included) for the tenant scope and every non-client entity.
    Results are stored as system runs; the closer only receives the count."""
    from sqlalchemy import text

    active = [k for k in KPI_CATALOG if k["status"] == "active"]
    ents = db.execute(text("SELECT count(*) FROM legal_entities WHERE tenant_id = :t AND entity_type <> 'client'"),
                      {"t": ids["kpi"]["tenant_id"]}).scalar()
    h = auth(MCDIR)
    r = client.post("/api/v1/kpi/periods/2025-02-01/close", headers=h)
    assert r.status_code == 200, r.text
    try:
        assert r.json()["frozen_results"] == len(active) * (1 + ents)
        # no values leak to the closer; coverage_gaps is feed metadata only (B-20, ADR-020)
        assert set(r.json()) == {"period_month", "status", "frozen_results", "coverage_gaps"}
    finally:
        assert client.post("/api/v1/kpi/periods/2025-02-01/reopen", json={"reason": "test cleanup"},
                           headers=h).status_code == 200


# ------------------------------------------------------------------ B-13: batched monthly series


def _demo_scopes(db, tenant_id):
    from sqlalchemy import text

    from app.analytics.calculators import Scope

    ent = db.execute(text("SELECT id FROM legal_entities WHERE tenant_id = :t ORDER BY id LIMIT 2"),
                     {"t": tenant_id}).scalars().all()
    prj = db.execute(text("SELECT project_id FROM timesheets t JOIN employment_relationships e ON e.id = t.employment_id "
                          "WHERE e.tenant_id = :t AND project_id IS NOT NULL LIMIT 1"), {"t": tenant_id}).scalar()
    occ = db.execute(text("SELECT occupation_group FROM occupations WHERE tenant_id = :t AND occupation_group IS NOT "
                          "NULL ORDER BY 1 LIMIT 1"), {"t": tenant_id}).scalar()
    return [Scope(tenant_id), Scope(tenant_id, frozenset(ent)), Scope(tenant_id, frozenset(ent[:1])),
            Scope(tenant_id, None, prj), Scope(tenant_id, None, None, occ), Scope(tenant_id, frozenset(), None, None)]


def test_series_calculators_identical_to_per_period(db, ids):
    """Every batched calculator returns, for every window, exactly what the per-period calculator returns."""
    from datetime import date

    from app.analytics.calculators import CALCULATORS, SERIES_CALCULATORS
    from app.analytics.domain import month_end, month_starts

    ps, pe = date(2025, 10, 15), date(2026, 10, 20)  # partial first and last month on purpose
    windows = [(max(m, ps), min(month_end(m), pe)) for m in month_starts(ps, pe)]
    checked = 0
    for scope in _demo_scopes(db, ids["demo"]["tenant_id"]):
        for key, batched in SERIES_CALCULATORS.items():
            got = batched(db, scope, windows, {})
            assert set(got) == set(windows)
            for w in windows:
                assert got[w].to_dict() == CALCULATORS[key](db, scope, w[0], w[1], {}).to_dict(), (key, scope, w)
                checked += 1
    assert checked == 6 * len(SERIES_CALCULATORS) * len(windows)


@pytest.mark.parametrize("code", ["KPI-016", "KPI-007", "KPI-032"])
def test_monthly_series_api_identical_to_per_month_evaluate(client, auth, db, ids, code):
    from datetime import date

    from app.analytics import engine

    params = {"period_start": "2025-11-01", "period_end": "2026-10-31"}

    def strip(rows):
        return [{k: v for k, v in r.items() if k != "calculated_at"} for r in rows]

    h = auth("admin@demo.local")
    engine.bump_data_version(db, ids["demo"]["tenant_id"])  # cold cache: the batched path really runs
    db.commit()
    series = client.get(f"/api/v1/kpi/series/{code}", params=params, headers=h).json()
    engine.bump_data_version(db, ids["demo"]["tenant_id"])
    db.commit()
    single = []
    for m in engine.month_starts(date(2025, 11, 1), date(2026, 10, 31)):
        single.append(client.get(f"/api/v1/kpi/evaluate/{code}", headers=h, params={
            "period_start": m.isoformat(), "period_end": engine.month_end(m).isoformat()}).json())
    assert len(series) == 12 and strip(series) == strip(single)
