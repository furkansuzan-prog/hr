# A13 — DevOps, Performance & Platform report

## Work done
1. **B-05 observability: `GET /metrics`** (Prometheus text format 0.0.4, no new dependencies)
   - `backend/app/core/metrics.py` (new): thread-safe Counter/Gauge/Histogram (one lock per metric family), registry and renderer, label escaping.
   - Metrics: `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}` histogram (5 ms to 30 s), `http_requests_in_progress`, `kpi_cache_hits_total`, `kpi_cache_misses_total`, `kpi_cache_errors_total`, `process_start_time_seconds`, `hrswi_worker_info{pid}`.
   - `route` is the route **template** read from `scope["route"].path_format` (e.g. `/api/v1/kpi/evaluate/{code}`), so no ids reach the labels. Unmatched paths are folded into `<unmatched>`, and unknown HTTP methods become `OTHER`. This keeps label cardinality bounded.
   - `app/main.py`: the existing request middleware now records metrics (500s from unhandled exceptions included). `/metrics` is left out of the OpenAPI schema. When `HRSWI_METRICS_TOKEN` is set, the endpoint requires `Authorization: Bearer <token>` (checked with `hmac.compare_digest`) and returns 401 otherwise.
   - `app/core/cache.py`: `get` counts hits and misses (misses only while the cache is enabled). Backend errors in `get`/`set` increment `kpi_cache_errors_total`; before this change errors in `set` were silently dropped.
   - Multi-worker caveat (documented in `docs/OPERATIONS.md` §2): counters are kept per process. On the 4-worker scale instance, 3 scrapes in a row returned 3 different `pid`s with different counter values. The doc recommends 1 worker per pod with each pod scraped as its own target. The alternatives are per-worker ports or `prometheus_client` multiprocess mode, which needs a new dependency.
2. **B-04 scale test (15K active employees)**: results are in `docs/PERFORMANCE_REPORT.md` § 15K. The earlier results are kept.
   - Seed: 234 s, peak RSS 1.40 GB. Result: 30,599 people, 31,229 employments (15,294 active), 1.83M weekly timesheets, 2.23M cost rows, 4.46M rows in total, 770 MB DB.
   - `scripts/perf_smoke.py`: new options `--think-time` (exponential, mean in seconds), `--duration`, `--ramp-up`, `--seed`, `--json` and `--label`. It now runs closed-loop VU threads and reports p99 and status-code counts.
   - **Pass:** 150 VUs with ~5 s think time and a warm cache. Dashboard p95 was **512 ms**, overall p95 335 ms, at 29.4 req/s with 1 ReadError in 5,294 requests.
   - **Fail:** 30 VUs with no think time and a warm cache. Dashboard p95 was 3.5–4.5 s (two runs), at 29–37 req/s saturation.
   - **Fail:** 30 VUs from a cold cache. Dashboard p95 was 86 s, with 7×500 errors.
   - **Fail:** 150 VUs from a cold cache. Dashboard p95 was 117 s, with 172×500 errors (`QueuePool limit … timed out`).
   - **Fail:** single-user cold dashboard took 19.8 s (it was about 1.5 s on the 2.5K dataset).
   - Cold-path analysis used a SQLAlchemy event profiler (`pg_stat_statements` is not preloaded, and restarting the shared server was avoided) plus EXPLAIN (ANALYZE, BUFFERS). About 90% of the cold SQL time is the timesheet window aggregate (Q1), the payroll `count(DISTINCT person)` query (Q2) and headcount-at-date. Each experiment was run on `hrswi_scale` and then removed:
     - Monthly timesheet summary table: Q1 went from 334 to 35 ms (9.5×), and a 12-month series from 2.9 to 0.52 s.
     - Dropping `count(DISTINCT)` from Q2: 4.26 s to 0.31 s (14×).
     - Covering index: page reads went from 15,170 to 984 hits, but time only from 334 to 275 ms.
     - `work_mem` and two-level aggregation: little or no gain.
   - Recommended DDL and app changes are listed in the report: dogpile lock, cache prewarm after `data_version` bumps, aligning the threadpool with the DB pool (or PgBouncer), MGET for by-entity, `tenant_id` predicates on fact tables, and partitioning once daily feeds arrive.
3. **Backup/restore drill**:
   - `scripts/backup.sh`: `pg_dump -Fc` to a timestamped file, a SHA-256 checksum and a row-count manifest. The counts are taken in the **same snapshot** as the dump (`pg_export_snapshot` + `--snapshot`). Retention cleanup is included.
   - `scripts/restore_verify.sh`: checks the checksum, restores into a fresh temporary DB, compares exact row counts of every table against the manifest (and the live source with `--live`) plus the Alembic revision, then always drops the temp DB.
   - Run on `hrswi_scale`: dump 9.9 s, 44.1 MB for an 808 MB DB. Restore and ANALYZE 18.6 s, total verify 19.5 s. **PASS:** 57/57 tables and 4,462,490 rows matched both the manifest and the live source.
   - Negative tests: a corrupted dump gave CHECKSUM MISMATCH and exit 1. A tampered manifest gave FAIL and exit 1. The temp DB was dropped in both cases.
   - `docs/OPERATIONS.md` (new) covers: drill results, RPO ≤ 5 min (WAL/PITR plus a daily logical dump) and RTO ≤ 1 h, alert rules based on `/metrics`, an expand/contract migration policy, deployment and rollback (R1/R2/R3), and log handling.
4. **CI / compose**:
   - `tests/test_metrics.py` runs in the normal `pytest -q`.
   - New `perf-smoke` job, only on `schedule` (weekly) or `workflow_dispatch` (inputs: employees, concurrency, requests). It seeds a small DB and runs perf_smoke cold, warm and with think time, scrapes `/metrics`, runs the backup/restore drill and uploads `backend/perf-out/` as an artifact. It makes no latency assertions.
   - Adding the `schedule` trigger also runs the existing backend/frontend/docker jobs weekly.
   - docker-compose `api`: `HRSWI_REDIS_URL` (default `redis://redis:6379/0`) and optional `HRSWI_METRICS_TOKEN` (empty means open).

## Files
- New: `backend/app/core/metrics.py`, `backend/tests/test_metrics.py`, `backend/scripts/backup.sh`, `backend/scripts/restore_verify.sh`, `docs/OPERATIONS.md`, this report.
- Modified: `backend/app/main.py`, `backend/app/core/cache.py`, `backend/scripts/perf_smoke.py`, `.github/workflows/ci.yml`, `docker-compose.yml`, `docs/PERFORMANCE_REPORT.md`.
- No migrations or models were touched, and no new dependencies were added.

## Tests run
- `tests/test_metrics.py`: 8 passed (`HRSWI_TEST_DATABASE_URL=…/hrswi_test_ops`). The first attempt hit 3 failures caused by A11's in-progress `create_access_token` signature change. The rerun was green.
- Full suite: **130 passed**. `ruff check app tests`: all checks passed.
- The `perf-smoke` CI job was **not** run on GitHub Actions. Its commands were run locally, and the workflow YAML parses.

## Environment notes / honesty
- The machine was shared with other agents running pytest, `next build` and `tsc`, with load average between 2 and 22 during measurements. The load generator ran on the same host. Numbers are noisy; the warm 30-VU run was repeated and is reported as a range.
- The container restarted mid-task. The pre-restart 150-VU run was invalid (2,147 ConnectErrors) and is not used. Afterwards I confirmed the seed was complete (all final tables present, counts match the seed log), applied `0004` and re-ran every measurement.
- The API under test was the working tree as of 12:52, so other agents' later changes (for example the batched series calculator) may not have been loaded.
- The 8200 server was stopped. The dev DB `hrswi` and ports 8000/3000 were not touched. `hrswi_scale` is left seeded at `0004` with no experimental objects. Redis DB 1 still holds cached KPI entries.

## Recommendations for other agents
- **A09 KPI / A05 data:** add the `timesheet_monthly` summary (or a materialized view) and split `count(DISTINCT)` out of the payroll query. Add `t.tenant_id`/`pc.tenant_id` predicates.
- **A06 backend:** add single-flight on cache misses and a prewarm job after imports. Cap the anyio threadpool at pool_size+max_overflow. Return 503 + `Retry-After` on pool timeout instead of 500. Batch the by-entity cache reads with MGET.
- **Seed owner:** stream COPY per table instead of building full Python lists (1.4 GB RSS at 15K).
