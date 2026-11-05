# A06 — Backend: data management API (`/api/v1/manage`)

## Work done
Built the contracted data-management API. The router validates requests, checks permissions, maps errors and writes the audit trail. The business rules are in a new service module.

| File | Status |
|---|---|
| `backend/app/api/routers/admin_data.py` | rewritten. The stub was already registered in `main.py`, which is unchanged. |
| `backend/app/services/data_management.py` | new |
| `backend/tests/test_data_management.py` | new, 8 tests |

No models, migrations, factories or conftest were touched.

## Endpoints (all under `/api/v1/manage`, errors `{"detail": ...}`)
| Method / path | Perm | Status | Notes |
|---|---|---|---|
| GET `/users` | admin.users | 200 | Same tenant only. Each role entry also carries `active` (bool) as well as the contract fields. |
| POST `/users` | admin.users | 201 | Returns `{id, initial_password}` (`random_password`, scrypt-hashed). 409 if the email exists (case-insensitive). 422 for a bad email or locale (`tr\|en\|ru`). |
| GET `/roles` | admin.users | 200 | `{id, code, name, is_system, permissions}`. Covers tenant roles and global templates. |
| POST `/users/{id}/roles` | admin.users | 201 | Escalation guard: (1) the target scope must be inside the granter's own `admin.users` scope; tenant scope needs a tenant-wide grant. (2) The granter must hold every permission of the role for that same scope. 403 is audited `authz.scope_denied/denied`. 409 if an identical active grant exists. 422 if the scope and entity don't match. 404 for another tenant's user. |
| DELETE `/role-assignments/{id}` | admin.users | 200 | Sets `valid_to = today` (the row is kept). 409 if already revoked, or if this is the caller's own last `admin.users` grant. 403 outside the caller's admin scope. |
| GET `/identity-reviews?status=open\|merged\|created\|rejected\|all` | people.write | 200 | Users scoped to an entity only see items from batches they created. Any payload key containing `national_id` is stripped. |
| POST `/identity-reviews/{id}/resolve` | people.write | 200 | `merge`: the employment goes to the candidate person and a `PersonIdentifier` is added. `create_new`: a new person plus an employment. `reject`. Returns `{id, status, person_id?, employment_id?}`. Status values are `merged`, `created` and `rejected`. 409 if already resolved. 403 if the payload's entity is outside the caller's people.write scope. |
| POST `/people` | people.write (entity) | 201 | `{person_id, employment_id}`. Only `national_id_hash` is stored. 409 `{"message", "candidate_person_id"}` if another person has the same hash. The raw ID is never written to the audit log. |
| POST `/employments/{id}/terminate` | people.write (entity) | 200 | Returns the employment JSON. Row lock plus `record_version` check (409). 422 if end < start or the category is not in `TERMINATION_CATEGORIES`. Back-dating allowed. Open assignments are closed. `mark_stale_from(min(new, old end))`. |
| POST `/employments/{id}/transfer` | people.write on **both** entities | 200 | Ends the current spell at `transfer_date - 1` with category `other` and `termination_reason_code = internal_transfer`. Creates the new spell on `transfer_date` and copies `contract_type` plus the primary assignment's project/occupation. KPI-001 sees a 1-day gap, so it counts as a transfer. 409 if the version is stale or the employment has already ended. 422 for the same entity or a date not after the start. |
| POST `/legal-entities` | org.write (**tenant-wide**) | 201 | Returns `entity_type` and also `type`, matching the `/org/legal-entities` shape. 409 for a duplicate code (case-insensitive). |
| POST `/contractor-relationships` | org.write on parent **and** child | 201 | 422 if parent == child. 409 if an open or overlapping relationship exists for the same parent/child/project. |
| POST `/contractor-relationships/{id}/end` | org.write on both | 200 | 422 if `valid_to <= valid_from`. 409 if already ended. |

All writes are audited (`admin.user_create`, `admin.role_grant`, `admin.role_revoke`, `identity_review.resolve`, `people.create`, `employment.terminate`, `employment.transfer`, `org.legal_entity_create`, `org.contractor_relationship_create`, `org.contractor_relationship_end`). Writes that affect analytics call `mark_stale_from`: people, terminate, transfer, review resolve, and relationship create/end. The relationship calls are a conservative choice under ADR-015.

## Security decisions beyond the contract
- **Creating a legal entity needs tenant-wide `org.write`.** A new entity is outside every entity-scoped grant.
- **Contractor relationships need `org.write` on both ends.** Otherwise an entity-scoped admin could create `own -> foreign` with `data_sharing_granted=true` and widen its own data visibility.
- **Scopes include subcontractors.** Grant and revoke scope checks use `AccessContext`, which already adds subcontractors that share data. So an admin scoped to A can also administer grants for A's data-sharing subs, which matches how every other permission behaves.

## Tests run (honest results)
- `python -m pytest -p no:warnings tests/test_data_management.py`: **8 passed**.
- Full suite `python -m pytest -p no:warnings tests` (DB `hrswi_test_be`): **100 passed** in 135 s.
- `ruff check app tests`: **All checks passed**.

Coverage:
- happy paths for every endpoint
- escalation attempts → 403 (tenant scope from an entity admin, a foreign entity, a role with unheld permissions; plus audit rows)
- revoking a tenant grant from an entity admin → 403; revoking your own last admin grant → 409
- creating a person: contractor manager, HR manager on A, and a T1-scoped specialist in T2 → 403
- stale `record_version` → 409, for both terminate and transfer
- KPI-001 numerator: 0 over {T3, T4} after a transfer and 1 over {T3}
- termination bumps `data_version`, marks persisted results stale, and changes the re-evaluated value
- identity review flow driven by a CSV import (merge, create_new, reject, re-resolve → 409, scoped visibility)
- no `national_id_hash` or raw ID in review payloads
- audit rows for each write

All fixture data sits in new entities created through the API, with 2026 dates. The transfer test also inserts one Project and one Occupation directly into the EXACT tenant.

## Known gaps / notes for integration
- **`create_new` when the hash collides:** the new person is stored **without** `national_id_hash`, so that future imports are not sent back to review. The response includes a `note`.
- **`create_new` when the external ID is already mapped:** if the source external ID already belongs to another person, it returns 409 (use merge or reject).
- **Staging rows are not updated on resolve.** Batch counters stay as the import set them.
- **`GET /users` lists every user in the tenant**, even for entity-scoped admins, as the contract says ("same tenant only"). It may be worth narrowing.
- **Terminate never reopens assignments.** If an end date is moved later, assignments that were already closed stay closed.
- **Transfer does not copy `employee_number`** (employers number staff differently).
- **Create endpoints return 201, action endpoints return 200.**
