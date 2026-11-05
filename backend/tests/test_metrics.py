"""B-05 observability: /metrics exposition, route-template labels, cache counters, token protection, no PII."""
import re
import threading

import pytest

from app.core import metrics
from app.core.metrics import Counter, Histogram

ADMIN = "admin@demo.local"


def _sample(text: str, name: str, **labels: str) -> float | None:
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith(name):
            continue
        m = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})? (\S+)$", line)
        if not m or m.group(1) != name:
            continue
        got = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(3) or ""))
        if all(got.get(k) == v for k, v in labels.items()):
            return float(m.group(4))
    return None


@pytest.fixture()
def no_token(monkeypatch):
    monkeypatch.delenv("HRSWI_METRICS_TOKEN", raising=False)


def test_metrics_exposition_format(client, no_token):
    client.get("/health/live")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = r.text
    for fam, typ in [("http_requests_total", "counter"), ("http_request_duration_seconds", "histogram"),
                     ("kpi_cache_hits_total", "counter"), ("kpi_cache_misses_total", "counter"),
                     ("process_start_time_seconds", "gauge")]:
        assert f"# TYPE {fam} {typ}" in body
        assert f"# HELP {fam} " in body
    assert _sample(body, "process_start_time_seconds") > 1.6e9
    # every non-comment line must be a valid sample line
    for line in body.strip().splitlines():
        if not line.startswith("#"):
            assert re.match(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[a-zA-Z_]\w*="[^"]*"(,[a-zA-Z_]\w*="[^"]*")*\})? \S+$', line), line


def test_route_template_labels_not_raw_paths(client, auth, no_token):
    h = auth(ADMIN)
    assert client.get("/api/v1/kpi/evaluate/KPI-001", headers=h).status_code == 200
    client.get("/api/v1/people/999999999/history", headers=h)
    client.get("/definitely/not/a/route/12345")
    body = client.get("/metrics").text
    assert _sample(body, "http_requests_total", method="GET", route="/api/v1/kpi/evaluate/{code}", status="200") >= 1
    assert _sample(body, "http_requests_total", method="GET", route="/api/v1/people/{person_id}/history") is not None
    assert _sample(body, "http_requests_total", route="<unmatched>", status="404") >= 1
    assert "KPI-001" not in body and "999999999" not in body and "12345" not in body
    assert _sample(body, "http_request_duration_seconds_count", method="GET",
                   route="/api/v1/kpi/evaluate/{code}") >= 1
    assert _sample(body, "http_request_duration_seconds_bucket", method="GET",
                   route="/api/v1/kpi/evaluate/{code}", le="+Inf") >= 1


def test_metrics_contain_no_personal_or_tenant_data(client, auth, db, no_token):
    from sqlalchemy import text

    h = auth(ADMIN)
    client.get("/api/v1/people?page=1&page_size=5", headers=h)
    client.get("/api/v1/auth/me", headers=h)
    body = client.get("/metrics").text
    assert "@" not in body  # no e-mail addresses
    tenants = db.execute(text("SELECT code, name FROM tenants")).all()
    names = db.execute(text("SELECT first_name, last_name FROM people LIMIT 50")).all()
    for code, name in tenants:
        assert f'"{code}"' not in body and name not in body
    for first, last in names:
        assert f"{first} {last}" not in body
    # only the whitelisted label keys appear
    keys = set(re.findall(r'[{,]([a-zA-Z_]\w*)="', body))
    assert keys <= {"method", "route", "status", "le", "pid"}


def test_kpi_cache_hit_and_miss_counters(client, auth, no_token):
    from app.core.cache import cache

    h = auth(ADMIN)
    cache.clear_local()
    if cache._redis is not None:  # pragma: no cover - CI uses the in-process cache
        pytest.skip("shared Redis cache configured; counters asserted only for the in-process backend")
    url = "/api/v1/kpi/evaluate/KPI-002?period_start=2026-01-01&period_end=2026-03-31"
    misses0 = metrics.KPI_CACHE_MISSES.value()
    assert client.get(url, headers=h).status_code == 200
    misses1, hits1 = metrics.KPI_CACHE_MISSES.value(), metrics.KPI_CACHE_HITS.value()
    assert misses1 > misses0
    assert client.get(url, headers=h).status_code == 200
    assert metrics.KPI_CACHE_HITS.value() > hits1
    body = client.get("/metrics").text
    assert _sample(body, "kpi_cache_hits_total") == metrics.KPI_CACHE_HITS.value()
    assert _sample(body, "kpi_cache_misses_total") == metrics.KPI_CACHE_MISSES.value()


def test_metrics_token_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("HRSWI_METRICS_TOKEN", "s3cret-scrape-token")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/metrics", headers={"Authorization": "Bearer s3cret-scrape-token"})
    assert r.status_code == 200 and "http_requests_total" in r.text
    # a user JWT is not a scrape credential
    assert client.get("/metrics", headers={"Authorization": "Basic czNjcmV0"}).status_code == 401


def test_metrics_not_in_openapi(client):
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


def test_counter_and_histogram_thread_safety():
    c = Counter("t_total", "t", ("route",))
    h = Histogram("t_seconds", "t", ("route",), buckets=(0.1, 1.0))

    def work():
        for i in range(5000):
            c.inc(route="/a")
            h.observe(0.05 if i % 2 else 0.5, route="/a")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.value(route="/a") == 40_000
    assert h.count(route="/a") == 40_000
    lines = h.collect()
    assert 't_seconds_bucket{route="/a",le="0.1"} 20000' in lines
    assert 't_seconds_bucket{route="/a",le="1"} 40000' in lines
    assert 't_seconds_bucket{route="/a",le="+Inf"} 40000' in lines
    assert any(line.startswith('t_seconds_sum{route="/a"} ') for line in lines)


def test_label_escaping_and_method_normalisation():
    c = Counter("e_total", "e", ("route",))
    c.inc(route='a"b\\c\nd')
    assert 'e_total{route="a\\"b\\\\c\\nd"} 1' in c.collect()
    assert metrics.normalise_method("get") == "GET"
    assert metrics.normalise_method("BREW") == "OTHER"
