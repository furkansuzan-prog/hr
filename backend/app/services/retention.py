"""Retention / minimisation job for import working data (SECURITY_PRIVACY section 3, gap G5 / backlog B-03).

What it does (operational tables only - analytics/master tables are NEVER touched):
  * import_staging_rows of batches whose status is `committed` or `failed` and whose last change
    (import_batches.updated_at) is older than `staging_days`:
      - raw    -> {}                                   (the source row as uploaded)
      - mapped -> only NON_PERSONAL_MAPPED_KEYS kept   (names, external ids, employee numbers, national id hash,
                                                        birth year and gender are removed)
      - errors are kept (rule codes / messages about the row, needed for data-quality history)
    Failed batches that are purged are moved to status `expired`: their staging data can no longer be
    resumed, so a later retry is rejected cleanly ("Batch must be validated before commit") instead of
    committing half-empty rows.
  * identity_review_items with status `merged` or `rejected` whose last change is older than `review_days`:
      - payload -> {}
Dry-run (default) only counts. `--apply` changes data and writes one `retention.purge` audit row per affected
tenant plus a run summary row (tenant_id NULL), in the same transaction as the purge.

The job is idempotent: already-purged rows are not counted again.

CLI:  python -m app.services.retention [--apply] [--staging-days N] [--review-days N]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy.types import String

from app.models import AuditLog

NON_PERSONAL_MAPPED_KEYS: tuple[str, ...] = (
    "legal_entity_code",
    "start_date",
    "end_date",
    "contract_type",
    "termination_category",
    "project_code",
    "occupation_code",
    "source_system",
    "errors",
)
FINISHED_BATCH_STATUSES = ("committed", "failed")
RESOLVED_REVIEW_STATUSES = ("merged", "rejected")

# a staging row still needs purging when raw is non-empty or mapped still carries a personal key
_STAGING_NEEDS_PURGE = """
    (s.raw::text <> '{}'
     OR (s.mapped IS NOT NULL AND json_typeof(s.mapped) = 'object'
         AND EXISTS (SELECT 1 FROM json_object_keys(s.mapped) k WHERE k <> ALL(:keep))))
"""
_ELIGIBLE_BATCHES = """
    SELECT b.id, b.tenant_id, b.status FROM import_batches b
    WHERE b.status IN ('committed', 'failed') AND b.updated_at < :cutoff
"""


def _keep_param():
    return bindparam("keep", value=list(NON_PERSONAL_MAPPED_KEYS), type_=ARRAY(String))


def purge(db: Session, *, now: datetime | None = None, staging_days: int = 90, review_days: int = 90,
          dry_run: bool = True) -> dict:
    """Count (dry_run) or purge personal data in finished import working tables. Returns a JSON-able summary."""
    if staging_days < 1 or review_days < 1:
        raise ValueError("retention periods must be at least one day")
    now = now or datetime.now(UTC)
    staging_cutoff = now - timedelta(days=staging_days)
    review_cutoff = now - timedelta(days=review_days)

    staging = db.execute(
        text(f"""
            SELECT b.tenant_id, count(*) AS n, count(DISTINCT b.id) AS batches
            FROM ({_ELIGIBLE_BATCHES}) b JOIN import_staging_rows s ON s.batch_id = b.id
            WHERE {_STAGING_NEEDS_PURGE}
            GROUP BY b.tenant_id
        """).bindparams(_keep_param()),
        {"cutoff": staging_cutoff},
    ).all()
    failed_batches = db.execute(
        text(f"""
            SELECT b.tenant_id, count(*) FROM ({_ELIGIBLE_BATCHES}) b
            WHERE b.status = 'failed' GROUP BY b.tenant_id
        """),
        {"cutoff": staging_cutoff},
    ).all()
    reviews = db.execute(
        text("""
            SELECT tenant_id, count(*) FROM identity_review_items
            WHERE status IN ('merged', 'rejected') AND updated_at < :cutoff AND payload::text <> '{}'
            GROUP BY tenant_id
        """),
        {"cutoff": review_cutoff},
    ).all()

    per_tenant: dict[int, dict[str, int]] = {}

    def slot(tid: int) -> dict[str, int]:
        return per_tenant.setdefault(tid, {"staging_rows": 0, "batches": 0, "failed_batches_expired": 0,
                                           "review_items": 0})

    for tid, n, batches in staging:
        slot(tid)["staging_rows"] += n
        slot(tid)["batches"] += batches
    for tid, n in failed_batches:
        slot(tid)["failed_batches_expired"] += n
    for tid, n in reviews:
        slot(tid)["review_items"] += n

    totals = {k: sum(v[k] for v in per_tenant.values())
              for k in ("staging_rows", "batches", "failed_batches_expired", "review_items")}
    result = {
        "dry_run": dry_run,
        "now": now.isoformat(),
        "staging_days": staging_days,
        "review_days": review_days,
        "staging_cutoff": staging_cutoff.isoformat(),
        "review_cutoff": review_cutoff.isoformat(),
        **totals,
        "per_tenant": {str(k): v for k, v in sorted(per_tenant.items())},
    }
    if dry_run:
        return result

    db.execute(
        text(f"""
            UPDATE import_staging_rows s SET
              raw = '{{}}'::json,
              mapped = CASE
                WHEN s.mapped IS NULL OR json_typeof(s.mapped) <> 'object' THEN s.mapped
                ELSE COALESCE((SELECT json_object_agg(e.key, e.value) FROM json_each(s.mapped) e
                               WHERE e.key = ANY(:keep)), '{{}}'::json)
              END
            WHERE s.batch_id IN (SELECT id FROM ({_ELIGIBLE_BATCHES}) b) AND {_STAGING_NEEDS_PURGE}
        """).bindparams(_keep_param()),
        {"cutoff": staging_cutoff},
    )
    # status change only; keep updated_at semantics (a later run still sees the batch as old enough)
    db.execute(
        text("UPDATE import_batches SET status = 'expired', "
             "error_message = 'staging data purged by retention policy' "
             "WHERE status = 'failed' AND updated_at < :cutoff"),
        {"cutoff": staging_cutoff},
    )
    db.execute(
        text("""
            UPDATE identity_review_items SET payload = '{}'::json
            WHERE status IN ('merged', 'rejected') AND updated_at < :cutoff AND payload::text <> '{}'
        """),
        {"cutoff": review_cutoff},
    )
    params = {"staging_days": staging_days, "review_days": review_days, "now": now.isoformat()}
    for tid, counts in sorted(per_tenant.items()):
        db.add(AuditLog(tenant_id=tid, user_id=None, action="retention.purge", entity_type="retention",
                        outcome="allowed", details={**counts, **params}))
    db.add(AuditLog(tenant_id=None, user_id=None, action="retention.purge", entity_type="retention",
                    entity_id="run", outcome="allowed", details={**totals, **params,
                                                                  "tenants": sorted(per_tenant)}))
    db.commit()
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.services.retention", description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="change data (default: dry run, counts only)")
    ap.add_argument("--staging-days", type=int, default=90)
    ap.add_argument("--review-days", type=int, default=90)
    a = ap.parse_args(argv)
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        res = purge(db, staging_days=a.staging_days, review_days=a.review_days, dry_run=not a.apply)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
