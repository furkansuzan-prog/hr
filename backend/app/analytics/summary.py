"""Timesheet monthly summary maintenance (B-21).

Correctness never depends on this module being called: DB triggers mark changed months dirty and calculators read
the summary only for clean months. refresh() just restores the fast path.
"""
from datetime import date

from sqlalchemy import event, text
from sqlalchemy.orm import Session

REFRESH_SQL = """
INSERT INTO timesheet_monthly (tenant_id, month, employment_id, project_id, n, planned, worked, overtime,
                               productive, n_productive, planned_productive, night)
SELECT tenant_id, date_trunc('month', work_date)::date, employment_id, project_id, count(*),
       coalesce(sum(planned_hours), 0), coalesce(sum(worked_hours), 0), coalesce(sum(overtime_hours), 0),
       coalesce(sum(productive_hours), 0), count(productive_hours),
       coalesce(sum(planned_hours) FILTER (WHERE productive_hours IS NOT NULL), 0), coalesce(sum(night_hours), 0)
FROM timesheets WHERE status = 'approved' AND tenant_id = :tenant AND work_date >= :from_month
GROUP BY 1, 2, 3, 4
"""


def dirty_from(db: Session, tenant_id: int) -> date | None:
    """Earliest month whose summary is stale for the tenant (None = summary fully valid). Memoised per request."""
    store = db.info.setdefault("ts_dirty", {})
    if tenant_id not in store:
        store[tenant_id] = db.execute(text("SELECT from_month FROM timesheet_summary_dirty WHERE tenant_id = :t"),
                                      {"t": tenant_id}).scalar()
    return store[tenant_id]


def refresh(db: Session, tenant_id: int | None = None) -> dict[int, date]:
    """Rebuild dirty months (all tenants, or one). Row-locks the dirty marker so concurrent refreshes serialise.
    Commits its own work. Returns {tenant_id: from_month} that were rebuilt."""
    q = "SELECT tenant_id, from_month FROM timesheet_summary_dirty"
    q += " WHERE tenant_id = :t FOR UPDATE" if tenant_id is not None else " FOR UPDATE"
    rows = db.execute(text(q), {"t": tenant_id}).all()
    done = {}
    for tid, from_month in rows:
        db.execute(text("DELETE FROM timesheet_monthly WHERE tenant_id = :t AND month >= :m"), {"t": tid, "m": from_month})
        db.execute(text(REFRESH_SQL), {"tenant": tid, "from_month": from_month})
        db.execute(text("DELETE FROM timesheet_summary_dirty WHERE tenant_id = :t AND from_month = :m"),
                   {"t": tid, "m": from_month})
        done[tid] = from_month
    db.commit()
    db.info.pop("ts_dirty", None)
    return done


_REQUEST_MEMOS = ("kpi_memo", "hours_memo", "headcount_memo", "ts_dirty")


@event.listens_for(Session, "after_commit")
def _drop_request_memos(session: Session) -> None:
    """Request-scoped analytics memos must never outlive a write: drop them on every commit of the session."""
    for k in _REQUEST_MEMOS:
        session.info.pop(k, None)
