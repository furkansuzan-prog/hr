"""Post-commit cache warming (B-21).

After a commit that changed analytics data (engine.mark_stale_from adds the tenant to session.info["warm_tenants"]),
a single background worker: (1) rebuilds dirty timesheet summary months, (2) recomputes the executive dashboard for
each DISTINCT access scope of the tenant's active users, so the first users after an import hit a warm cache.
Runs in-process (one worker thread, requests for the same tenant coalesce). Production: move to the job queue (B-12).
"""
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.analytics import dashboards, summary
from app.core.db import SessionLocal
from app.models import User
from app.security.access import build_access_context

log = logging.getLogger("hrswi.prewarm")
MAX_SCOPES = int(os.getenv("HRSWI_PREWARM_MAX_SCOPES", "25"))
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prewarm")
_pending: set[int] = set()
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("HRSWI_PREWARM", "true").lower() == "true"


def warm_tenant(tenant_id: int) -> dict:
    """Synchronous warm-up; returns what was done (used by tests and the CLI)."""
    with SessionLocal() as db:
        refreshed = summary.refresh(db, tenant_id)
        ps, pe = dashboards.default_period()
        seen, warmed = set(), 0
        for user in db.execute(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True))).scalars():
            ctx = build_access_context(db, user)
            if not ctx.has("kpi.read"):
                continue
            k = (frozenset(ctx.entity_filter("kpi.read") or {"*"}),
                 frozenset(ctx.entity_filter("cost.read") or {"*"}) if ctx.has("cost.read") else None)
            if k in seen:
                continue
            seen.add(k)
            if len(seen) > MAX_SCOPES:
                break
            try:
                dashboards.executive(db, ctx, ps, pe)
                warmed += 1
            except Exception:  # warming is best effort; never let it break anything
                log.exception("prewarm scope failed", extra={"tenant_id": tenant_id})
            db.info.pop("kpi_memo", None)
        return {"tenant_id": tenant_id, "summary_refreshed": bool(refreshed), "scopes_warmed": warmed}


def _run(tenant_id: int) -> None:
    with _lock:
        _pending.discard(tenant_id)
    try:
        log.info("prewarm done", extra={"tenant_id": tenant_id, **warm_tenant(tenant_id)})
    except Exception:
        log.exception("prewarm failed", extra={"tenant_id": tenant_id})


def schedule(tenant_id: int) -> None:
    if not enabled():
        return
    with _lock:
        if tenant_id in _pending:  # coalesce bursts of commits into one warm-up
            return
        _pending.add(tenant_id)
    _executor.submit(_run, tenant_id)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    for tid in session.info.pop("warm_tenants", set()):
        schedule(tid)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("warm_tenants", None)
