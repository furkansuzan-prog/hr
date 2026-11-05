"""Concurrent latency smoke test against a running API (NOT a substitute for a full load test).

python scripts/perf_smoke.py --base http://localhost:8000 --concurrency 30 --requests 600
Reports p50/p95/max per endpoint. Results depend on hardware and dataset size - record them with both.
"""
import argparse
import concurrent.futures as cf
import random
import statistics
import time

import httpx

USERS = ["admin@demo.local", "hr.director@demo.local", "mc.ops@demo.local", "sub01.manager@demo.local",
         "exec@demo.local", "hr.manager@demo.local"]
ENDPOINTS = [
    ("dashboard", "/api/v1/dashboard/executive", 0.25),
    ("kpi_evaluate", "/api/v1/kpi/evaluate/KPI-001", 0.25),
    ("kpi_series", "/api/v1/kpi/series/KPI-007", 0.15),
    ("kpi_by_entity", "/api/v1/kpi/by-entity/KPI-016", 0.15),
    ("people_list", "/api/v1/people?page=1&page_size=50", 0.1),
    ("legal_entities", "/api/v1/org/legal-entities", 0.1),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--requests", type=int, default=600)
    ap.add_argument("--password", default="Demo!2345")
    a = ap.parse_args()
    tokens = {}
    with httpx.Client(base_url=a.base, timeout=60) as c:
        for u in USERS:
            r = c.post("/api/v1/auth/login", json={"email": u, "password": a.password})
            if r.status_code == 200:
                tokens[u] = r.json()["access_token"]
    weights = [w for _, _, w in ENDPOINTS]
    plan = [(random.choices(ENDPOINTS, weights)[0], random.choice(list(tokens.values()))) for _ in range(a.requests)]
    results: dict[str, list[float]] = {n: [] for n, _, _ in ENDPOINTS}
    errors: dict[str, int] = {}
    client = httpx.Client(base_url=a.base, timeout=60, limits=httpx.Limits(max_connections=a.concurrency))

    def call(item):
        (name, path, _), tok = item
        t0 = time.perf_counter()
        r = client.get(path, headers={"Authorization": f"Bearer {tok}"})
        dt = time.perf_counter() - t0
        return name, dt, r.status_code

    t_start = time.perf_counter()
    with cf.ThreadPoolExecutor(a.concurrency) as ex:
        for name, dt, status in ex.map(call, plan):
            if status >= 400 and status != 403:
                errors[name] = errors.get(name, 0) + 1
            results[name].append(dt)
    wall = time.perf_counter() - t_start
    print(f"concurrency={a.concurrency} requests={a.requests} users={len(tokens)} wall={wall:.1f}s "
          f"throughput={a.requests / wall:.1f} req/s")
    print(f"{'endpoint':<16}{'n':>5}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}")
    for name, xs in results.items():
        if not xs:
            continue
        xs.sort()
        p95 = xs[min(len(xs) - 1, int(0.95 * len(xs)))]
        print(f"{name:<16}{len(xs):>5}{statistics.median(xs) * 1000:>10.0f}{p95 * 1000:>10.0f}{xs[-1] * 1000:>10.0f}")
    print("errors:", errors or "none")


if __name__ == "__main__":
    main()
