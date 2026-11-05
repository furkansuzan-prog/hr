"""Concurrent latency smoke test against a running API (NOT a substitute for a full load test).

Closed-loop virtual users (VUs): each VU repeatedly picks an endpoint from a weighted mix, calls it with one of the
demo users' tokens and then waits a random "think time" (exponential distribution, mean --think-time seconds).

  # saturation test: 30 VUs, no think time, 600 requests in total
  python scripts/perf_smoke.py --base http://localhost:8000 --concurrency 30 --requests 600
  # realistic: 150 VUs, ~5 s think time, run for 180 s (stop condition: --duration)
  python scripts/perf_smoke.py --base http://localhost:8000 --concurrency 150 --think-time 5 --duration 180

Reports p50/p95/p99/max per endpoint and overall, throughput and errors; --json writes the same numbers to a file
(used as a CI artifact). Results depend on hardware and dataset size - record them with both.
"""
import argparse
import json
import math
import random
import statistics
import threading
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


def pct(xs: list[float], p: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, max(0, math.ceil(p * len(xs)) - 1))]


def summarise(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {"n": len(xs), "p50_ms": round(statistics.median(xs) * 1000) if xs else None,
            "p95_ms": round(pct(xs, 0.95) * 1000) if xs else None,
            "p99_ms": round(pct(xs, 0.99) * 1000) if xs else None,
            "max_ms": round(xs[-1] * 1000) if xs else None}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=30, help="number of closed-loop virtual users")
    ap.add_argument("--requests", type=int, default=600, help="stop after this many requests (0 = no limit)")
    ap.add_argument("--duration", type=float, default=0, help="stop after this many seconds (0 = no limit)")
    ap.add_argument("--think-time", type=float, default=0.0,
                    help="mean think time per VU between requests, seconds (exponential); 0 = none")
    ap.add_argument("--ramp-up", type=float, default=None,
                    help="spread VU start over this many seconds (default: think time, i.e. desynchronised starts)")
    ap.add_argument("--password", default="Demo!2345")
    ap.add_argument("--seed", type=int, default=None, help="random seed for a reproducible request mix")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--json", dest="json_out", default=None, help="also write results as JSON to this file")
    ap.add_argument("--label", default="", help="free-text label stored in the JSON output")
    a = ap.parse_args()
    if a.requests <= 0 and a.duration <= 0:
        ap.error("set --requests and/or --duration")
    rng = random.Random(a.seed)

    tokens: list[str] = []
    with httpx.Client(base_url=a.base, timeout=a.timeout) as c:
        for u in USERS:
            r = c.post("/api/v1/auth/login", json={"email": u, "password": a.password})
            if r.status_code == 200:
                tokens.append(r.json()["access_token"])
    if not tokens:
        raise SystemExit("no demo user could log in - is the database seeded?")

    weights = [w for _, _, w in ENDPOINTS]
    results: dict[str, list[float]] = {n: [] for n, _, _ in ENDPOINTS}
    errors: dict[str, int] = {}
    statuses: dict[str, int] = {}
    lock = threading.Lock()
    issued = 0
    stop = threading.Event()
    client = httpx.Client(base_url=a.base, timeout=a.timeout,
                          limits=httpx.Limits(max_connections=a.concurrency, max_keepalive_connections=a.concurrency))
    ramp = a.think_time if a.ramp_up is None else a.ramp_up
    t_start = time.perf_counter()
    deadline = t_start + a.duration if a.duration > 0 else None

    def take_ticket() -> bool:
        nonlocal issued
        with lock:
            if stop.is_set() or (a.requests > 0 and issued >= a.requests):
                stop.set()
                return False
            if deadline is not None and time.perf_counter() >= deadline:
                stop.set()
                return False
            issued += 1
            return True

    def vu(idx: int) -> None:
        local = random.Random(rng.random() + idx)
        if ramp > 0:
            stop.wait(local.uniform(0, ramp))
        tok = tokens[idx % len(tokens)]
        while take_ticket():
            name, path, _ = local.choices(ENDPOINTS, weights)[0]
            t0 = time.perf_counter()
            try:
                status = client.get(path, headers={"Authorization": f"Bearer {tok}"}).status_code
            except httpx.HTTPError as e:
                status = type(e).__name__
            dt = time.perf_counter() - t0
            with lock:
                results[name].append(dt)
                statuses[str(status)] = statuses.get(str(status), 0) + 1
                if not isinstance(status, int) or (status >= 400 and status != 403):
                    errors[name] = errors.get(name, 0) + 1
            if a.think_time > 0 and stop.wait(local.expovariate(1.0 / a.think_time)):
                return

    threads = [threading.Thread(target=vu, args=(i,), daemon=True) for i in range(a.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t_start
    client.close()

    total = sum(len(v) for v in results.values())
    all_lat = [x for v in results.values() for x in v]
    out = {
        "label": a.label, "base": a.base, "concurrency": a.concurrency, "think_time_s": a.think_time,
        "requests": total, "users": len(tokens), "wall_s": round(wall, 1),
        "throughput_rps": round(total / wall, 1) if wall else None,
        "overall": summarise(all_lat), "endpoints": {n: summarise(xs) for n, xs in results.items() if xs},
        "errors": errors, "status_counts": statuses,
    }
    print(f"concurrency={a.concurrency} think_time={a.think_time}s requests={total} users={len(tokens)} "
          f"wall={wall:.1f}s throughput={out['throughput_rps']} req/s")
    print(f"{'endpoint':<16}{'n':>6}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}")
    for name, s in [*out["endpoints"].items(), ("ALL", out["overall"])]:
        print(f"{name:<16}{s['n']:>6}{s['p50_ms']:>9}{s['p95_ms']:>9}{s['p99_ms']:>9}{s['max_ms']:>9}")
    print("status:", statuses)
    print("errors:", errors or "none")
    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
