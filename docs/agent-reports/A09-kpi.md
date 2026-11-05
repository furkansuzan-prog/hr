# A09 — KPI & Analytics report (B-10, B-18, B-13)

## Work done
### B-10 — 10 draft KPIs activated (status `active`, `validated=True`, calculator registered)
| KPI | Calculator / parameters | Notes |
|---|---|---|
| KPI-020 Voluntary Turnover | `turnover_rate` + new `include_categories=["voluntary"]`, `annualize` | Uses the same transfer rule as KPI-001. Quality score = share of separations that have a reason. Separations with no reason are **not** assumed voluntary (the note says so). |
| KPI-021 / KPI-022 | `early_turnover_90` with `days` 30 / 180 | |
| KPI-023 Average Tenure | new `average_tenure` | Uses the current spell only (a re-hire starts again from zero). If a person has concurrent spells, the longest-running one counts. Years = days / 365.25. Suppressed when n < min. |
| KPI-028 Sickness Absence | `absenteeism_rate`, `absence_types=["sick"]` | |
| KPI-032 Night Work Share | new `night_work_share` | night / worked hours × 100 |
| KPI-034 Downtime Lost-Hours Share | new `downtime_lost_hours_share` | lost_hours / planned timesheet hours in the same scope. An occupation filter returns `insufficient_data` with a note. Events with no legal entity are left out under an entity filter; the lineage counts them and the note says so. |
| KPI-036 Overtime Cost Share (sensitive) | new `overtime_cost_share` | Reuses the `labor_cost_total` FX logic. A missing FX rate gives `insufficient_data`. |
| KPI-048 Female Employee Share | new `female_share` | Persons with unknown gender are left out of the denominator and counted in the lineage. Suppressed if n < min **or** if the female or male count is < min. Gender values are matched in TR/EN/RU spellings regardless of case. |
| KPI-050 Subcontractor Workforce Share | new `subcontractor_share` | Distinct persons with a spell at an entity of type `subcontractor` / all persons in scope, both at period end. |

- `engine.PARAM_RULES` has a new `include_categories` rule (non-empty subset of the termination categories). `included_categories` was added to the lineage keys that stay visible when a result is suppressed.
- All results still pass through `engine.enforce_privacy`. Tests confirm that suppressed results expose no counts anywhere: value, numerator, denominator, sample_size, breakdown or lineage.
- KPI-001..019 v1 semantics are unchanged. `turnover_rate` behaves exactly as before when `include_categories` is absent, and all EXACT tests pass.
- The catalog is now sorted by code: 53 definitions, 28 active.

### B-18 — period close freezes results first
- `engine.freeze_period(db, ctx, month)` runs `run_calculation` (trigger `period_close`) for every active, validated KPI:
  - once at the closer's own scope (tenant scope for a tenant-wide closer);
  - once for each legal entity the closer can see. If an entity is identical to the closer's own single-entity scope, it is skipped, so nothing runs twice.
  - Sensitive KPIs are included only when the closer has tenant-wide `cost.read`.
- `POST /kpi/periods/{m}/close`:
  - returns 409 if the month is already closed;
  - freezes the results, then closes the month under a row lock;
  - returns 409 if another request closed the month at the same time (re-check after freezing, plus IntegrityError on the unique key);
  - returns `{"period_month", "status", "frozen_results"}` and writes `frozen_results` to the audit log.
- The reopen endpoint is unchanged.

### B-13 (stretch) — batched monthly series
- `calculators._hours_windows` computes the timesheet aggregates for many periods in one SQL query, using `unnest` of the period windows. Each window keeps its own dates for the project/occupation assignment overlap. `_hours` now delegates to it, so the single-period and batched paths share one implementation.
- `SERIES_CALCULATORS` covers `active_headcount`, `overtime_rate` and `night_work_share`.
- `engine.monthly_series` computes the batch lazily and passes it into `evaluate(precomputed=...)`. The batch is only used where a live calculation would happen anyway, so the frozen-result, cache and privacy paths are unchanged.
- Measured gain is small. On the test DEMO tenant (about 25K timesheets), 24 months of KPI-007 took 0.167 s batched against 0.169 s per month. The main benefit is fewer round trips (one query instead of N). `turnover_rate` and `absenteeism_rate` are not batched; that was not attempted.

## Files
- `backend/app/analytics/calculators.py`, `catalog.py`, `engine.py` (`domain.py` unchanged)
- `backend/app/api/routers/analytics.py` — `close_period` only
- `backend/tests/factories_kpi.py` (new): the KPIX tenant, with every expected value worked out by hand in its docstring
- `backend/tests/test_kpi_extended.py` (new): 27 tests
- `backend/tests/conftest.py`: additive only (builds KPIX and adds it to the session dict under `"kpi"`)
- `docs/KPI_CATALOG.md` regenerated with `scripts/gen_docs.py`, which also rewrote `DATA_DICTIONARY.md` and reflects the other agents' model changes

## Tests run (HRSWI_TEST_DATABASE_URL=…/hrswi_test_kpi)
- `pytest tests/test_kpi_extended.py`: **27 passed**
- Full suite `pytest tests/`, run after the environment restart with migration 0004 present: **127 passed, 0 failed**
- `ruff check app tests`: all checks passed

## Known gaps
1. **The dev database `hrswi` is not updated.** `seed_kpi_catalog` only inserts definitions that are missing, so an existing database keeps KPI-020…050 as `draft`. It needs a data migration or an update in the seed step; that belongs to the A05/A06 areas, and I was not allowed to create migrations. Fresh databases (tests, new installs) get the active definitions.
2. **Entity-scoped closers.** A closer who has `period.close` only at entity scope freezes only their own scope. Tenant-wide readers of that month then fall back to live values. Consider requiring tenant-wide `period.close` (a product/security decision).
3. **Close is not atomic.** Each `run_calculation` commits on its own. If one calculation fails, the month is not closed, but the earlier runs stay persisted. This is harmless because results of open months are never served frozen.
4. Thresholds for the new KPIs are deliberately empty (no traffic light) until HR validates them.
5. KPI-021/022 inherit `early_turnover_90`'s "cohort not yet fully observed" check, which is based on `date.today()`.
