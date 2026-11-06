"""Data-feed coverage declarations (B-20, ADR-020; docs/hr/data/WP-6 sections 3.4-3.5).

Coverage says, per feed type x employer x (optional) project x month, whether the source data is complete. The KPI
engine's gate (app.analytics.coverage) reads it so that a feed that never arrived is not shown as a real zero.

Ways a declaration is written:
1. Import (primary): the upload may carry a `coverage` object; on a successful commit every declared employer x month
   gets a row (`basis = import_batch`). Without a declaration every employer x month that received rows gets
   `partial` (ratio unknown) - row presence is not proof of completeness - and an existing declaration is kept.
   A header-only file of an event feed (leave, safety_event) is accepted only with `completeness = complete` -> `confirmed_empty`.
2. Manual (`POST /api/v1/coverage`): import.write on the WHOLE employer (ADR-017 - project-limited grants cannot
   write); `none` and `confirmed_empty` require a note; the safety feed additionally needs hse.read for that employer
   (interim rule until A11 defines an HSE declaration permission).
Every change writes an audit_logs row (action coverage.declare) in the same transaction and calls mark_stale_from
(INTEGRATION_CONTRACTS section 5). A later declaration replaces the earlier one; the audit row keeps the history.
"""
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.domain import month_end, month_starts
from app.analytics.engine import mark_stale_from
from app.models import (
    COVERAGE_COMPLETENESS,
    COVERAGE_DECLARE_ACTION,
    FEED_TYPES,
    SAFETY_COVERAGE_ATTRIBUTES,
    AuditLog,
    DataFeedCoverage,
    ImportStagingRow,
    LegalEntity,
    Project,
)
from app.security.access import AccessContext

EVENT_FEEDS = {"leave", "training", "safety"}  # an empty month is a valid, confirmable state (header-only file)
# import entity_type -> feed type (ADR-021: the HSE case import feeds 'safety')
IMPORT_FEEDS = {"employment": "employment", "leave": "leave", "safety_event": "safety", "timesheet": "timesheet"}
SAFETY_ATTRIBUTES = set(SAFETY_COVERAGE_ATTRIBUTES)
PLANNED_ON_LEAVE = {"included", "excluded", "unknown"}
MAX_MONTHS = 60
UNDECLARED_IMPORT_LOOKBACK_MONTHS = 60  # employment rows without a declaration: partial only for recent months


class CoverageError(Exception):
    status_code = 422


class CoverageDenied(CoverageError):
    status_code = 403


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _date(v, name: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise CoverageError(f"coverage.{name} must be an ISO date") from None


def can_declare(ctx: AccessContext, feed_type: str, entity_id: int) -> bool:
    if not (ctx.has("import.write") and ctx.covers_entity("import.write", entity_id)):
        return False
    return feed_type != "safety" or (ctx.has("hse.read") and ctx.covers_entity("hse.read", entity_id))


def normalize(db: Session, ctx: AccessContext, feed_type: str, raw: dict, *, manual: bool) -> dict:
    """Validate a declaration and return its canonical form (stored on the import batch / applied directly).
    Accepts legal_entity_codes | legal_entity_ids | legal_entity_id and project_codes | project_ids | project_id."""
    if not isinstance(raw, dict):
        raise CoverageError("coverage must be an object")
    if feed_type not in FEED_TYPES:
        raise CoverageError(f"Unknown feed_type '{feed_type}' (use one of: {', '.join(FEED_TYPES)})")
    ps, pe = _date(raw.get("period_start"), "period_start"), _date(raw.get("period_end"), "period_end")
    if ps.day != 1 or pe != month_end(pe) or pe < ps:
        raise CoverageError("Coverage is declared in whole months: period_start = first day, period_end = last day")
    months = month_starts(ps, pe)
    if len(months) > MAX_MONTHS:
        raise CoverageError(f"At most {MAX_MONTHS} months per declaration")
    if pe > month_end(date.today()):
        raise CoverageError("Coverage cannot be declared for future months")
    completeness = raw.get("completeness")
    if completeness not in COVERAGE_COMPLETENESS:
        raise CoverageError(f"completeness must be one of: {', '.join(COVERAGE_COMPLETENESS)}")
    ratio = raw.get("coverage_ratio")
    if ratio is not None:
        if completeness != "partial" or isinstance(ratio, bool) or not isinstance(ratio, int | float) \
                or not 0 < ratio < 1:
            raise CoverageError("coverage_ratio is allowed only for 'partial' and must be between 0 and 1 (exclusive)")
        ratio = round(float(ratio), 3)
    confirmed_empty = bool(raw.get("confirmed_empty", False))
    if confirmed_empty and completeness != "complete":
        raise CoverageError("confirmed_empty requires completeness 'complete'")
    note = (str(raw.get("note") or "").strip() or None)
    if manual and (completeness == "none" or confirmed_empty) and not (note and len(note) >= 5):
        raise CoverageError("A note (>= 5 characters) is required for 'none' and confirmed_empty declarations")
    attributes = raw.get("attributes") or None
    if attributes is not None:
        if feed_type != "safety" or not isinstance(attributes, dict) or not set(attributes) <= SAFETY_ATTRIBUTES \
                or not all(isinstance(v, bool) for v in attributes.values()):
            raise CoverageError("attributes are only for the safety feed: includes_subcontractor_cases, "
                                "includes_near_miss (true/false)")
    planned = raw.get("planned_on_leave_days")
    if planned is not None and (feed_type != "timesheet" or planned not in PLANNED_ON_LEAVE):
        raise CoverageError("planned_on_leave_days is only for the timesheet feed: included | excluded | unknown")

    ents = {le.code: le.id for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id))
            .scalars()}
    if raw.get("legal_entity_codes") is not None:
        codes = raw["legal_entity_codes"]
        if not isinstance(codes, list) or not codes:
            raise CoverageError("legal_entity_codes must be a non-empty list")
        unknown = [c for c in codes if c not in ents]
        if unknown:
            raise CoverageError(f"Unknown legal entity code(s): {', '.join(map(str, unknown))}")
        entity_ids = sorted({ents[c] for c in codes})
    else:
        ids = raw.get("legal_entity_ids") or ([raw["legal_entity_id"]] if raw.get("legal_entity_id") else None)
        if not ids or not all(isinstance(i, int) for i in ids) or not set(ids) <= set(ents.values()):
            raise CoverageError("legal_entity_id(s) of this tenant are required")
        entity_ids = sorted(set(ids))
    denied = [e for e in entity_ids if not can_declare(ctx, feed_type, e)]
    if denied:
        raise CoverageDenied("No permission to declare coverage for legal entity id(s) "
                             + ", ".join(map(str, denied)))
    projects = {p.code: p.id for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    if raw.get("project_codes"):
        unknown = [c for c in raw["project_codes"] if c not in projects]
        if unknown:
            raise CoverageError(f"Unknown project code(s): {', '.join(map(str, unknown))}")
        project_ids: list[int | None] = sorted({projects[c] for c in raw["project_codes"]})
    elif raw.get("project_ids") or raw.get("project_id"):
        pids = raw.get("project_ids") or [raw["project_id"]]
        if not set(pids) <= set(projects.values()):
            raise CoverageError("Unknown project id")
        project_ids = sorted(set(pids))
    else:
        project_ids = [None]
    return {"feed_type": feed_type, "period_start": ps.isoformat(), "period_end": pe.isoformat(),
            "legal_entity_ids": entity_ids, "project_ids": project_ids, "completeness": completeness,
            "coverage_ratio": ratio, "confirmed_empty": confirmed_empty, "attributes": attributes,
            "planned_on_leave_days": planned, "note": note}


def declare(db: Session, *, tenant_id: int, user_id: int | None, decl: dict, basis: str,
            import_batch_id: int | None = None, data_source_id: int | None = None,
            non_empty_cells: set[tuple[int, date]] | None = None, invalidate: bool = True) -> int:
    """Upsert one row per declared employer x project x month (canonical `decl` from normalize). For imports,
    `non_empty_cells` = (entity, month) cells that received rows: a 'complete' cell outside it is confirmed_empty.
    Writes ONE audit row with the previous state counts, then mark_stale_from(first month). Caller commits."""
    months = month_starts(date.fromisoformat(decl["period_start"]), date.fromisoformat(decl["period_end"]))
    existing = {(r.legal_entity_id, r.project_id, r.period_month): r for r in db.execute(
        select(DataFeedCoverage).where(
            DataFeedCoverage.tenant_id == tenant_id, DataFeedCoverage.feed_type == decl["feed_type"],
            DataFeedCoverage.legal_entity_id.in_(decl["legal_entity_ids"]),
            DataFeedCoverage.period_month.in_(months))).scalars()}
    previous: dict[str, int] = {}
    n = 0
    for e in decl["legal_entity_ids"]:
        for p in decl["project_ids"]:
            for m in months:
                empty = decl["confirmed_empty"] or (non_empty_cells is not None and decl["completeness"] == "complete"
                                                    and (e, m) not in non_empty_cells)
                values = {"completeness": decl["completeness"], "coverage_ratio": decl["coverage_ratio"],
                          "confirmed_empty": empty, "attributes": decl["attributes"],
                          "planned_on_leave_days": decl["planned_on_leave_days"], "basis": basis,
                          "import_batch_id": import_batch_id, "data_source_id": data_source_id,
                          "declared_by": user_id, "note": decl["note"]}
                row = existing.get((e, p, m))
                if row is None:
                    db.add(DataFeedCoverage(tenant_id=tenant_id, feed_type=decl["feed_type"], legal_entity_id=e,
                                            project_id=p, period_month=m, **values))
                else:
                    previous[row.completeness] = previous.get(row.completeness, 0) + 1
                    for k, v in values.items():
                        setattr(row, k, v)
                    row.declared_at = datetime.now(UTC)
                n += 1
    db.add(AuditLog(tenant_id=tenant_id, user_id=user_id, action=COVERAGE_DECLARE_ACTION, entity_type="data_feed_coverage",
                    entity_id=decl["feed_type"], details={**decl, "basis": basis,
                                                          "import_batch_id": import_batch_id, "rows": n,
                                                          "replaced": previous}))
    db.flush()
    if invalidate:
        mark_stale_from(db, tenant_id, months[0])
    return n


def declare_complete(db: Session, tenant_id: int, entity_ids, feeds, first_month: date, last_month: date, *,
                     note: str, user_id: int | None = None, invalidate: bool = True) -> int:
    """Bulk 'complete' declaration (demo seed, test factories, onboarding of a tenant whose history is known to be
    complete). Existing declarations are replaced like any other declaration (audited)."""
    n = 0
    for feed in feeds:
        n += declare(db, tenant_id=tenant_id, user_id=user_id, basis="manual_declaration", invalidate=False, decl={
            "feed_type": feed, "period_start": date(first_month.year, first_month.month, 1).isoformat(),
            "period_end": month_end(last_month).isoformat(), "legal_entity_ids": sorted(entity_ids),
            "project_ids": [None], "completeness": "complete", "coverage_ratio": None, "confirmed_empty": False,
            "attributes": None, "planned_on_leave_days": None, "note": note})
    if invalidate:
        mark_stale_from(db, tenant_id, date(first_month.year, first_month.month, 1))
    return n


# ---------------------------------------------------------------- imports


def _row_cells(m: dict, ents: dict[str, int], today: date) -> set[tuple[int, date]]:
    e = ents.get(m.get("legal_entity_code") or "")
    if m.get("event_date"):  # HSE case (ADR-021): a single day
        sd = ed = date.fromisoformat(m["event_date"])
        return {(e, date(sd.year, sd.month, 1))} if e is not None else set()
    if m.get("work_date"):  # timesheet (ADR-022): the day, or the span of a period row
        if e is None:
            return set()
        sd = date.fromisoformat(m["work_date"])
        ed = min(sd + timedelta(days=(m.get("period_days") or 1) - 1), today)
        return {(e, mo) for mo in month_starts(sd, ed)}
    if e is None or not m.get("start_date"):
        return set()
    sd = date.fromisoformat(m["start_date"])
    ed = date.fromisoformat(m["end_date"]) if m.get("end_date") else today
    sd = max(sd, _add_months(today, -UNDECLARED_IMPORT_LOOKBACK_MONTHS))
    ed = min(ed, today)
    return {(e, mo) for mo in month_starts(sd, ed)} if ed >= sd else set()


def row_outside(decl: dict | None, entity_id: int | None, sd: date | None, ed: date | None,
                project_id: int | None = None) -> bool:
    """Validation warning row_outside_declared_coverage: the row's employer is not declared, its span does not
    overlap the declared period or (project-level declaration, rows with a project) its project is not declared.
    Such rows are still loaded but create no coverage."""
    if not decl or entity_id is None or sd is None:
        return False
    if entity_id not in decl["legal_entity_ids"]:
        return True
    if project_id is not None and None not in decl["project_ids"] and project_id not in decl["project_ids"]:
        return True
    return sd > date.fromisoformat(decl["period_end"]) or (ed or sd) < date.fromisoformat(decl["period_start"])


def record_import(db: Session, ctx: AccessContext, batch) -> int:
    """Called after a successful commit (same transaction as the batch status change)."""
    feed = IMPORT_FEEDS.get(batch.entity_type)
    if feed is None:
        return 0
    ents = {le.code: le.id for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id))
            .scalars()}
    today = date.today()
    cells: set[tuple[int, date]] = set()
    for (m,) in db.execute(select(ImportStagingRow.mapped).where(ImportStagingRow.batch_id == batch.id,
                                                                 ImportStagingRow.committed.is_(True))):
        if m:
            cells |= _row_cells(m, ents, today)
    if batch.coverage:
        decl = batch.coverage
        return declare(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, decl=decl, basis="import_batch",
                       import_batch_id=batch.id, non_empty_cells=cells)
    if not cells:
        return 0
    # undeclared: 'partial' with unknown ratio only where nothing is declared yet (never downgrades a declaration)
    have = {(r[0], r[1]) for r in db.execute(select(DataFeedCoverage.legal_entity_id, DataFeedCoverage.period_month)
                                             .where(DataFeedCoverage.tenant_id == ctx.tenant_id,
                                                    DataFeedCoverage.feed_type == feed,
                                                    DataFeedCoverage.project_id.is_(None),
                                                    DataFeedCoverage.legal_entity_id.in_({e for e, _ in cells})))}
    new = sorted(cells - have)
    for e, mo in new:
        db.add(DataFeedCoverage(tenant_id=ctx.tenant_id, feed_type=feed, legal_entity_id=e, project_id=None,
                                period_month=mo, completeness="partial", coverage_ratio=None, basis="import_batch",
                                import_batch_id=batch.id, declared_by=ctx.user_id,
                                note="import without coverage declaration (row presence is not completeness)"))
    if new:
        db.add(AuditLog(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=COVERAGE_DECLARE_ACTION,
                        entity_type="data_feed_coverage", entity_id=feed,
                        details={"feed_type": feed, "basis": "import_batch", "import_batch_id": batch.id,
                                 "completeness": "partial", "rows": len(new), "undeclared_import": True}))
        db.flush()
        mark_stale_from(db, ctx.tenant_id, new[0][1] if len(new) == 1 else min(mo for _, mo in new))
    return len(new)


# ---------------------------------------------------------------- read model (imports page matrix)


def matrix(db: Session, ctx: AccessContext, ps: date, pe: date, feed_types: list[str] | None = None) -> dict:
    """Feed x month x employer matrix for the caller's whole-entity import scope. States: complete, partial, none,
    confirmed_empty (complete + no rows) and undeclared (no row). Metadata only - no person data."""
    feeds = feed_types or list(FEED_TYPES)
    bad = [f for f in feeds if f not in FEED_TYPES]
    if bad:
        raise CoverageError(f"Unknown feed_type(s): {', '.join(bad)}")
    months = month_starts(ps, pe)
    if len(months) > MAX_MONTHS:
        raise CoverageError(f"At most {MAX_MONTHS} months")
    allowed = ctx.entity_filter("import.write")
    q = select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id, LegalEntity.entity_type != "client")
    if allowed is not None:
        q = q.where(LegalEntity.id.in_({e for e in allowed if ctx.covers_entity("import.write", e)} or {-1}))
    ents = list(db.execute(q.order_by(LegalEntity.code)).scalars())
    rows = db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == ctx.tenant_id, DataFeedCoverage.feed_type.in_(feeds),
        DataFeedCoverage.period_month.in_(months),
        DataFeedCoverage.legal_entity_id.in_([e.id for e in ents] or [-1])).order_by(DataFeedCoverage.id)).scalars()
    cells = []
    for r in rows:
        state = "confirmed_empty" if r.completeness == "complete" and r.confirmed_empty else r.completeness
        cells.append({"feed_type": r.feed_type, "legal_entity_id": r.legal_entity_id, "project_id": r.project_id,
                      "period_month": r.period_month.isoformat(), "state": state,
                      "coverage_ratio": float(r.coverage_ratio) if r.coverage_ratio is not None else None,
                      "basis": r.basis, "import_batch_id": r.import_batch_id, "attributes": r.attributes,
                      "declared_at": r.declared_at.isoformat() if r.declared_at else None, "note": r.note})
    return {"period_start": ps.isoformat(), "period_end": pe.isoformat(), "months": [m.isoformat() for m in months],
            "feed_types": feeds, "legal_entities": [{"id": e.id, "code": e.code, "name": e.name,
                                                    "type": e.entity_type} for e in ents],
            "cells": cells, "states": ["complete", "partial", "none", "confirmed_empty", "undeclared"]}


def period_gaps(db: Session, ctx: AccessContext, period_month: date, feeds: set[str]) -> list[dict]:
    """WP-6 3.5 step 5 / HR Director S-3: feeds required by active KPIs that are not 'complete' for the month being
    closed. Listed in the close response and audit record - closing is NOT blocked (a block would invite false
    confirmed_empty declarations). Only employers inside the closer's period.close scope are listed."""
    tenant_id = ctx.tenant_id
    q = select(LegalEntity.id, LegalEntity.code).where(
        LegalEntity.tenant_id == tenant_id, LegalEntity.entity_type != "client", LegalEntity.is_active.is_(True))
    allowed = ctx.entity_filter("period.close")
    if allowed is not None:
        q = q.where(LegalEntity.id.in_(allowed or {-1}))
    ents = list(db.execute(q.order_by(LegalEntity.code)))
    have = {(r[0], r[1]): r[2] for r in db.execute(select(
        DataFeedCoverage.feed_type, DataFeedCoverage.legal_entity_id, DataFeedCoverage.completeness).where(
        DataFeedCoverage.tenant_id == tenant_id, DataFeedCoverage.period_month == period_month,
        DataFeedCoverage.project_id.is_(None)))}
    return [{"feed_type": f, "legal_entity_id": e, "legal_entity_code": code,
             "state": have.get((f, e), "undeclared")}
            for f in sorted(feeds) for e, code in ents if have.get((f, e)) != "complete"]
