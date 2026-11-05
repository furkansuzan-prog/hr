# A07 — Frontend & UX report (2026-11-05)

## Delivered

### 1. Module pages (the former "Planlandı" nav items now link to real pages; all data comes from existing endpoints)
| Route | Permission | Content |
|---|---|---|
| `/hse` | hse.read | KPI-014A LTIFR + KPI-014B TRIR tiles, one monthly chart per KPI, by-company table. An info box explains they are separate measures with different multipliers (1M h vs 200k h), so they cannot be added or compared directly. It also says the occupation filter is not supported for safety rates (the backend returns insufficient_data with "Safety events are not recorded per occupation"). |
| `/training` | training.read | KPI-008 + KPI-009 tiles, monthly series, by-company table, short method note. |
| `/cost` | cost.read | KPI-019 + KPI-010 tiles, series and by-company table. Every non-ok result is listed in a notes panel with the backend note and a reason badge (Missing FX rate / Mixed units / Missing source data). A series where no month could be calculated shows an explicit message instead of an empty chart. |
| `/planning` | forecast.read | KPI-006 tile + planned/actual averages and deviation %. Monthly planned-vs-actual chart from `breakdown.monthly`. Headcount forecast with a 3/6/12 horizon selector (radiogroup): ACTUAL is a solid line, FORECAST is a dashed line in a shaded area, with 80% and 95% bands. Also shows a table of forecast rows (each badged FORECAST), the model and the backtest MAPE. |
| `/admin` | admin.users | See 2. |

The nav now uses real permissions (`perm`) instead of `planned`. Users without the permission do not see the item. On a direct URL, `PermGate` shows a "no access" card naming the required permission.

### 2. Admin and data management (against `/api/v1/manage/*`, verified live after the backend landed)
- `/admin`:
  - Users table with search and role chips (role · scope or company name · valid_to).
  - Assignments with `active:false` are shown struck through and cannot be revoked again.
  - Create-user dialog: the initial password is shown once, read-only, with a copy button and a warning.
  - Grant-role dialog: role select showing its permissions, scope radio (single company / whole tenant, with a warning for tenant), company select from `/api/v1/org/legal-entities`.
  - Revoke goes through a confirmation dialog.
  - Role catalogue, with a `system` badge from `is_system`.
- `/imports`: an identity review queue section (only with people.write). It lists open reviews with the payload, candidate and batch, and offers Merge / Create new / Reject with a confirmation dialog. Merge is disabled when there is no candidate. The queue was empty in the dev DB, so only the empty state has been seen live.
- `/people`: per-row Transfer / Terminate buttons (people.write; only rows without an end_date).
  - The dialog loads `/api/v1/people/{id}/history` to get `record_version`.
  - Terminate takes an end date and a termination category (8 values, translated).
  - Transfer takes a target company and a date.
  - On a 409, the backend `detail.message` is shown and the history is refetched.
- `src/lib/api.ts`: `ApiError` now carries the raw `detail`, and its message uses `detail.message` when detail is an object (409 payloads). FastAPI validation arrays are joined.
- Unknown-route 404 ("Not Found") or 405 from `/manage` shows a clear "API not available yet" ErrorBox (`lib/manage.ts#manageError`).
- New `src/components/ui/dialog.tsx` (no new dependencies), built on native `<dialog>.showModal()`:
  - the background becomes inert
  - Esc goes through `onClose`
  - an explicit Tab/Shift+Tab focus trap
  - focus returns to the opener
  - `aria-labelledby` / `aria-describedby`
  - backdrop click closes it
  - also exports `ConfirmDialog` and `Field`.
- Header: a "Log out of all sessions" button (`POST /api/v1/auth/logout-all`, with confirmation). This was built after the coordinator's note. It is not in the E2E (it would revoke the tokens of the parallel test contexts) and has not been clicked manually.
- `Chart`: a page-level `legend` now keeps the themed text colour. Before, legend text was nearly invisible in dark mode on the forecast charts, including the existing dashboard forecast.
- i18n: about 150 new TR/EN/RU keys for all new text.

### 3. E2E (`frontend/e2e/smoke.mjs`): 28 checks, last run 28/28 PASS
New checks:
- hse/training/cost/planning render tiles and a chart
- HSE separate-measures and occupation notes
- cost mixed-units note
- planning horizon 12 gives 12 rows labelled TAHMİN
- people terminate dialog: labelled, focus inside, Esc closes; nothing is submitted
- admin nav visible for admin
- users table
- **create user → one-time password → listed**
- **grant HR_SPECIALIST on a company → chip appears → revoke with confirmation → active chip gone**
- identity queue section on /imports
- exec@demo.local: admin nav absent and /admin shows no-access

The manage-API checks fall back to a "graceful error" check when `/api/v1/manage/users` is not OK. The console-error filter ignores `/manage` 404s only in that case.

## Quality gates (all run on the final code)
- `npx tsc --noEmit`: clean.
- `npx eslint src`: 0 errors, 0 warnings in my files. Two warnings remain in files I did not change for this work: `kpis/page.tsx` (TanStack incompatible-library) and `lib/api.ts` (`location.assign`).
- `npx next build`: OK, 20 routes, the new ones are static.
- `node e2e/smoke.mjs /tmp/claude-0/e2e-shots`: 28/28 on the final run. I looked at the screenshots, light and dark, TR/EN/RU, including the dialogs.

## Known issues / for other agents
1. **Backend bug (A06): an employment can be transferred twice.** During a one-off manual UI test as hr.manager I transferred employment 10 (Kerem Pilot-10) to entity 3 on 2026-12-01. A second ad-hoc script run unintentionally transferred it again: the old row was still "active" because its end date 2026-11-30 is in the future. The API accepted both and created new employments **2606 and 2607**, so the dev DB now has one duplicate employment for person 10. `POST /manage/employments/{id}/transfer` (and probably `/terminate`) should reject an employment that already has an `end_date` / was already transferred. I cannot clean this up without writing to the DB outside the UI. The frontend now only offers the actions on rows without an end_date.
2. Every smoke run creates one `e2e.<timestamp>@demo.local` user with no active roles; there are about 8 such users in the dev DB now. There is no delete/deactivate endpoint in the contract.
3. Two intermittent smoke failures were each seen once in about 6 runs and did not reproduce on rerun:
   - React hydration error #418 during the run. A per-page sweep found no errors, so this is likely timing under load. The smoke now records the URL for page errors, to help locate it if it recurs.
   - The pre-existing "KPI detail lineage" check, which used `isVisible` without waiting. I made it wait.
4. Backend KPI notes (e.g. "Output measured in several units…") are English only. The UI adds translated reason badges but shows the note text verbatim.
5. Leftover: the login page and older pages still contain hardcoded English labels outside `t()` (pre-existing).

## Files
- New: `src/app/(app)/{hse,training,cost,planning,admin}/page.tsx`, `src/components/{kpi-views,identity-reviews,employment-actions}.tsx`, `src/components/ui/dialog.tsx`, `src/lib/manage.ts`
- Changed: `src/components/{app-shell,chart}.tsx`, `src/app/(app)/{people,imports}/page.tsx`, `src/lib/{api,i18n}.tsx?`, `e2e/smoke.mjs`, `docs/SCREEN_MAP.md`
- Servers left running: API on :8000 (working tree, 2 workers) and `next start` on :3000 (final build).
