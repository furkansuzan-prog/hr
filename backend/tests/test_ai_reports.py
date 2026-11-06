"""Assistant, forecasting, reports, integrations, dashboard, health, sustainability."""
import time

DEMO = "admin@demo.local"
P = {"period_start": "2025-11-01", "period_end": "2026-10-31"}


def ask(client, auth, user, q):
    r = client.post("/api/v1/assistant/ask", json={"question": q}, headers=auth(user))
    return r


def test_assistant_intents_and_numbers_come_from_engine(client, auth):
    r = ask(client, auth, DEMO, "Last six months why did turnover rise?").json()
    assert r["intent"] == "turnover_trend" and r["language"] == "en"
    assert r["sources"][0]["kpi"] == "KPI-001" and r["sources"][0]["formula_version"] >= 1
    assert "kpi.monthly_series:KPI-001" in r["tools_used"] and r["llm_used"] is False
    # the period figure quoted in the answer equals the engine result in data
    assert f"{r['data']['period_total']:,.2f}".replace(",", " ") in r["answer"]


def test_assistant_turkish_and_russian(client, auth):
    r = ask(client, auth, DEMO, "Hangi departmanlarda planlanan ve gerçekleşen iş gücü arasında en yüksek fark var?").json()
    assert r["intent"] == "plan_deviation" and r["language"] == "tr"
    r = ask(client, auth, DEMO, "Какие риски у субподрядчиков?").json()
    assert r["intent"] == "contractor_risk" and r["language"] == "ru"


def test_assistant_forecast_marked_as_forecast(client, auth):
    r = ask(client, auth, DEMO, "Önümüzdeki üç ay için kaç ilave çalışana ihtiyaç duyulabilir?").json()
    assert r["kind"] == "forecast"
    assert all(p["kind"] == "forecast" for p in r["data"]["forecast"])
    assert all(h["kind"] == "actual" for h in r["data"]["history"])
    assert r["caveats"]


def test_assistant_correlation_caveat(client, auth):
    r = ask(client, auth, DEMO, "Eğitim saatleri ile kalite göstergeleri arasında bir ilişki var mı?").json()
    assert r["intent"] == "training_quality"
    assert any("nedensellik" in c for c in r["caveats"])
    assert "n_entities" in r["data"]


def test_assistant_refuses_individual_decisions(client, auth):
    for q in ("Hangi çalışanı işten çıkarmalıyız?", "Who should we fire next month?", "Кого уволить?",
              "Çalışan risk puanı hesapla"):
        r = ask(client, auth, DEMO, q).json()
        assert r["intent"] == "prohibited" and r["refused"] is True, q


def test_assistant_respects_scope(client, auth, ids):
    """Scenario 19: a subcontractor manager's answer only contains their own entities."""
    r = ask(client, auth, "ex.b@exact.local", "Why did turnover rise in the last six months?").json()
    names = {e["entity"] for e in r["data"]["top_entities"]}
    assert names <= {"Entity B"}
    assert ask(client, auth, "ex.hse@exact.local", "turnover?").status_code == 403  # no assistant.use


def test_forecast_insufficient_data(client, auth, ids):
    """Scenario 18: entity F has only a few months of history."""
    f = ids["exact"]["entities"]["F"]
    r = client.get("/api/v1/forecast/headcount", params={"entity_ids": f, "as_of": "2025-06-30"},
                   headers=auth("ex.admin@exact.local")).json()
    assert r["status"] == "insufficient_data" and r["forecast"] == []


def test_forecast_ok_on_demo(client, auth):
    r = client.get("/api/v1/forecast/headcount", params={"as_of": "2026-10-31", "horizon": 3},
                   headers=auth(DEMO)).json()
    assert r["status"] == "ok" and len(r["forecast"]) == 3 and r["training_points"] >= 12
    assert r["model"] == "linear_trend+seasonal12"


def test_reports_all_formats(client, auth):
    h = auth(DEMO)
    j = client.get("/api/v1/reports/monthly_hr_performance", params=P, headers=h).json()
    md = j["metadata"]
    for k in ("period_start", "filters", "data_sources", "generated_at", "formula_versions", "data_quality"):
        assert k in md
    x = client.get("/api/v1/reports/monthly_hr_performance", params={**P, "format": "xlsx"}, headers=h)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    p = client.get("/api/v1/reports/monthly_hr_performance", params={**P, "format": "pdf"}, headers=h)
    assert p.status_code == 200 and p.content[:4] == b"%PDF"
    c = client.get("/api/v1/reports/subcontractor_workforce", params={**P, "format": "csv"}, headers=h)
    assert c.status_code == 200 and "KPI-016" in c.text


def test_report_omits_cost_without_permission(client, auth):
    j = client.get("/api/v1/reports/monthly_hr_performance", params=P, headers=auth("mc.ops@demo.local")).json()
    assert "KPI-019" not in [k["code"] for k in j["kpis"]]
    assert {"code": "KPI-019", "reason": "requires cost.read"} in j["metadata"]["omitted_kpis"]


def test_source_unavailable(client, auth):
    """Scenario 20."""
    h = auth(DEMO)
    src = next(s for s in client.get("/api/v1/integrations/sources", headers=h).json() if s["code"] == "TURNSTILE")
    r = client.post(f"/api/v1/integrations/sources/{src['id']}/sync", headers=h)
    assert r.status_code == 503 and r.json()["detail"]["data_written"] == 0
    after = next(s for s in client.get("/api/v1/integrations/sources", headers=h).json() if s["code"] == "TURNSTILE")
    assert "unreachable" in after["last_error"]


def test_executive_dashboard_latency_and_shape(client, auth):
    h = auth(DEMO)
    t0 = time.perf_counter()
    r = client.get("/api/v1/dashboard/executive", params=P, headers=h)
    dt = time.perf_counter() - t0
    assert r.status_code == 200
    d = r.json()
    assert len(d["tiles"]) >= 11 and set(d["trends"]) == {"KPI-016", "KPI-001", "KPI-007", "KPI-003"}
    assert len(d["trends"]["KPI-016"]) == 12
    assert dt < 2.0, f"dashboard {dt:.2f}s (dev, small dataset)"


def test_same_filter_for_table_and_chart(client, auth, ids):
    """Section 6.5: series and by-entity use the same filter definition as the single evaluation."""
    h = auth(DEMO)
    ents = client.get("/api/v1/org/legal-entities", headers=h).json()
    sub = next(e for e in ents if e["code"] == "SUB01")["id"]
    single = client.get("/api/v1/kpi/evaluate/KPI-016", params={**P, "entity_ids": sub}, headers=h).json()
    table = client.get("/api/v1/kpi/by-entity/KPI-016", params={**P, "entity_ids": sub}, headers=h).json()
    series = client.get("/api/v1/kpi/series/KPI-016", params={**P, "entity_ids": sub}, headers=h).json()
    assert table[0]["value"] == single["value"] == series[-1]["value"]


def test_sustainability_coverage_disclaimer(client, auth):
    fws = client.get("/api/v1/sustainability/frameworks", headers=auth("sustainability@demo.local")).json()
    assert {f["code"] for f in fws} >= {"ISO30414", "GRI", "ESRS-S1"}
    assert all("not a compliance" in f["disclaimer"] and f["version_verified"] is False for f in fws)


def test_health(client):
    assert client.get("/health/live").json()["status"] == "ok"
    assert client.get("/health/ready").json()["migration"] == "0008"
