"""Data-feed coverage gate (B-20, audit finding #19, ADR-020; docs/hr/data/WP-6 sections 3.4-3.6).

A KPI whose formula lists `required_feeds` is checked against `data_feed_coverage` over the scope x window:

    C_feed = sum(covered weight) / sum(required weight)        over (legal entity x month) cells
    weight = mean headcount of the entity in the month (start + end) / 2, from employment data in the scope;
             for the employment feed itself every entity-month weighs 1
    covered = weight x factor;  factor: complete 1, partial coverage_ratio (unknown -> 0.5), none / undeclared 0
    C = min over the feeds whose mode is not "off"

Mode per feed (settings.coverage_modes; decision D-13 / PO-6: default "default=warn,safety=enforce"):
    off      -> the feed is ignored
    warn     -> C < min_feed_coverage: value kept, no colour (status_reason low_coverage), note, quality <= C x 100
    enforce  -> C < min_feed_coverage: insufficient_data, value null, quality = C x 100
    both     -> min_feed_coverage <= C < 1: value kept, quality <= C x 100, note "n entity-month(s) not covered";
                enforce additionally withholds the colour until the covered-subset recomputation (WP-6 K3) exists.

Formula parameter `required_feed_attributes` (ADR-021, e.g. KPI-043 {"safety": {"includes_near_miss": true}}): a
declaration whose attributes explicitly contradict the requirement counts as not covered (factor 0) - "near misses
are not in this feed" is not a real zero near-miss rate. A declaration without the attribute is taken at its word.

The gate never touches fatality alerts (computed separately, WP-6 data review condition 3). Scope rules: whole
entities need a declaration without project (project_id NULL); a project filter or an ADR-017 project-limited
entity accepts a declaration for that project and falls back to the whole-entity declaration.
"""
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics.calculators import Scope, _emp_where
from app.analytics.domain import INSUFFICIENT, OK, CalcResult, month_end, month_starts
from app.core.config import get_settings
from app.models import FEED_TYPES

LOW_COVERAGE = "low_coverage"
MODES = ("off", "warn", "enforce")
DEFAULT_MIN_FEED_COVERAGE = 0.8
PARTIAL_UNKNOWN_RATIO = 0.5
_FACTOR = {"complete": 1.0, "none": 0.0}


def parse_modes(raw: str) -> dict[str, str]:
    """'default=warn,safety=enforce' -> {feed: mode} for every feed type. Unknown feeds / modes are configuration
    errors and fail loudly (a silently ignored typo would hide a PO-6 decision)."""
    out: dict[str, str] = {}
    default = "warn"
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        feed, _, mode = part.partition("=")
        feed, mode = feed.strip(), mode.strip()
        if mode not in MODES or (feed != "default" and feed not in FEED_TYPES):
            raise ValueError(f"Invalid HRSWI_COVERAGE_MODES entry '{part}'")
        if feed == "default":
            default = mode
        else:
            out[feed] = mode
    return {f: out.get(f, default) for f in FEED_TYPES}


def modes() -> dict[str, str]:
    return parse_modes(get_settings().coverage_modes)


def mode_signature() -> str:
    """Part of the KPI cache key: a PO-6 configuration change must never serve results cached under the old mode."""
    m = modes()
    return ",".join(f"{f}={m[f]}" for f in FEED_TYPES if m[f] != "warn") or "warn"


# ------------------------------------------------------------------ inputs (request-scoped memo on the Session)


def _memo(db: Session) -> dict:
    return db.info.setdefault("coverage_memo", {})


def _entities(db: Session, tenant_id: int) -> dict[int, tuple[bool, str]]:
    m = _memo(db)
    key = ("ents", tenant_id)
    if key not in m:
        m[key] = {r[0]: (bool(r[1]), r[2]) for r in db.execute(text(
            "SELECT id, is_active, entity_type FROM legal_entities WHERE tenant_id = :t"), {"t": tenant_id})}
    return m[key]


def _weights(db: Session, scope: Scope, months: list[date]) -> dict[date, dict[int, float]]:
    """Mean headcount per (entity, month) = (headcount at month start + at month end) / 2. One grouped query per
    distinct filter set; entity-only scopes reuse the tenant-wide figures (an entity's own headcount does not depend
    on which other entities are in the scope). ADR-017 project-limited scopes are computed for themselves."""
    base = scope if scope.project_limits else Scope(scope.tenant_id, None, scope.project_id, scope.occupation_group)
    store = _memo(db).setdefault(("w", base.key()), {})
    todo = [m for m in months if m not in store]
    if todo:
        dates = sorted({d for m in todo for d in (m, month_end(m))})
        where, p = _emp_where(base, date_from="d.d", date_to="d.d")
        rows = db.execute(text(
            "SELECT e.legal_entity_id, d.d, count(DISTINCT e.person_id) FROM unnest(CAST(:dates AS date[])) AS d(d) "
            "JOIN employment_relationships e ON e.start_date <= d.d AND (e.end_date IS NULL OR e.end_date >= d.d) "
            f"WHERE true {where} GROUP BY 1, 2"), {**p, "dates": dates}).all()
        hc: dict[tuple[int, date], int] = {(r[0], r[1]): int(r[2]) for r in rows}
        ents = {r[0] for r in rows}
        for m in todo:
            store[m] = {e: (hc.get((e, m), 0) + hc.get((e, month_end(m)), 0)) / 2 for e in ents}
    return {m: store[m] for m in months}


def _declarations(db: Session, tenant_id: int, months: list[date]) -> dict[tuple, tuple]:
    """(feed, entity, project|None, month) -> (completeness, ratio, attributes) for the tenant (metadata only)."""
    store = _memo(db).setdefault(("decl", tenant_id), {})
    loaded = _memo(db).setdefault(("decl_months", tenant_id), set())
    todo = [m for m in months if m not in loaded]
    if todo:
        for r in db.execute(text(
                "SELECT feed_type, legal_entity_id, project_id, period_month, completeness, coverage_ratio, "
                "attributes FROM data_feed_coverage WHERE tenant_id = :t AND period_month = ANY(CAST(:m AS date[]))"),
                {"t": tenant_id, "m": todo}):
            store[(r[0], r[1], r[2], r[3])] = (r[4], float(r[5]) if r[5] is not None else None, r[6] or {})
        loaded.update(todo)
    return store


def _factor(decl: tuple | None, required: dict | None = None) -> float:
    if decl is None:
        return 0.0  # undeclared behaves like 'none'
    completeness, ratio, attributes = decl
    if required and any(k in attributes and attributes[k] != v for k, v in required.items()):
        return 0.0  # ADR-021: the declaration says the required content is not in the feed
    if completeness == "partial":
        return ratio if ratio is not None else PARTIAL_UNKNOWN_RATIO
    return _FACTOR.get(completeness, 0.0)


def feed_coverage(db: Session, scope: Scope, ps: date, pe: date, feeds: list[str],
                  attributes: dict | None = None) -> dict[str, dict]:
    """{feed: {"ratio": C | None, "uncovered": n entity-months, "cells": n}}. ratio None = nothing is required
    (no entity-month with people in scope) - the calculator's own missing-data rule decides."""
    months = month_starts(ps, pe)
    weights = _weights(db, scope, months)
    decl = _declarations(db, scope.tenant_id, months)
    ents = _entities(db, scope.tenant_id)
    limits = dict(scope.project_limits)
    in_scope = (lambda e: True) if scope.entity_ids is None else (lambda e: e in scope.entity_ids)
    entity_level = not (scope.project_id or scope.occupation_group)
    out = {}
    for feed in feeds:
        need = (attributes or {}).get(feed)
        req = cov = 0.0
        cells = uncovered = 0
        for m in months:
            w_m = weights[m]
            if feed == "employment":
                # equal weight per employer-month: employers with people in the month, plus (whole-entity views)
                # employers with an explicit employment declaration for it - a declared 'none' / 'partial' always
                # counts. An employer that never delivered any employment data and has no declaration is not in the
                # data at all; the /imports coverage matrix shows it as 'undeclared'.
                cand = {e for e in ents if in_scope(e) and ents[e][1] != "client" and (
                    w_m.get(e, 0) > 0 or (entity_level and e not in limits and ("employment", e, None, m) in decl))}
                cell_w = {e: 1.0 for e in cand}
            else:
                cell_w = {e: w for e, w in w_m.items() if w > 0 and in_scope(e)}
            for e, w in cell_w.items():
                units = [scope.project_id] if scope.project_id else (list(limits[e]) if e in limits else [None])
                f = min(_factor(decl.get((feed, e, u, m)) if u is not None and (feed, e, u, m) in decl
                                else decl.get((feed, e, None, m)), need) for u in units)
                req += w
                cov += w * f
                cells += 1
                uncovered += f < 1
        out[feed] = {"ratio": round(cov / req, 4) if req > 0 else None, "uncovered": uncovered, "cells": cells}
    return out


# ------------------------------------------------------------------ the gate


def _append(note: str | None, extra: str) -> str:
    return f"{note}; {extra}" if note else extra


def apply_gate(db: Session, scope: Scope, ps: date, pe: date, params: dict, res: CalcResult) -> CalcResult:
    feeds = list(params.get("required_feeds") or ())
    if not feeds:
        return res
    mode_of = modes()
    active = [f for f in feeds if mode_of[f] != "off"]
    if not active:
        return res
    min_c = float(params.get("min_feed_coverage", DEFAULT_MIN_FEED_COVERAGE))
    cov = feed_coverage(db, scope, ps, pe, active, params.get("required_feed_attributes"))
    known = {f: c for f, c in cov.items() if c["ratio"] is not None}
    res.lineage = {**(res.lineage or {}), "coverage": {
        "feeds": {f: c["ratio"] for f, c in cov.items()}, "min_feed_coverage": min_c,
        "mode": {f: mode_of[f] for f in active},
        "uncovered_entity_months": max((c["uncovered"] for c in cov.values()), default=0)}}
    if not known:
        return res
    c_min = min(c["ratio"] for c in known.values())
    if c_min >= 1:
        return res
    failing = sorted(f for f, c in known.items() if c["ratio"] < min_c)
    short = sorted(f for f, c in known.items() if c["ratio"] < 1)
    uncovered = max(known[f]["uncovered"] for f in short)
    detail = ", ".join(f"{f} {known[f]['ratio']:.3f}" for f in (failing or short))
    quality = round(c_min * 100, 1)
    if any(mode_of[f] == "enforce" for f in failing):
        res.status, res.value, res.numerator, res.denominator, res.sample_size = INSUFFICIENT, None, None, None, None
        res.breakdown, res.status_reason, res.quality_score = {}, None, quality
        res.note = (f"Data feed coverage below the minimum ({detail} < {min_c:g}): not computed - a missing feed is "
                    f"not a zero ({uncovered} entity-month(s) not covered)")
        return res
    if res.status != OK:
        return res
    res.quality_score = min(res.quality_score if res.quality_score is not None else 100.0, quality)
    if failing:
        res.status_reason = LOW_COVERAGE
        res.note = _append(res.note, f"Data feed coverage below the minimum ({detail} < {min_c:g}): value shown "
                                     f"without colour - missing data may look like zero ({uncovered} entity-month(s) "
                                     "not covered or not declared)")
    else:
        if any(mode_of[f] == "enforce" for f in short):
            res.status_reason = LOW_COVERAGE  # covered-subset recomputation (WP-6 K3) not implemented yet
        res.note = _append(res.note, f"Data feed coverage {detail}: {uncovered} entity-month(s) not covered")
    return res
