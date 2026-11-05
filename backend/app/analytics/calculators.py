"""Registered KPI calculators (ADR-008).

Each calculator: (db, scope, period_start, period_end, params) -> CalcResult. They read ONLY the scope they
are given; authorization has already been folded into `scope.entity_ids` / `scope.project_limits` by the engine.
Every entity filter goes through `_ent_sql` / `_emp_where` so project-limited entities (ADR-017) are honoured.
Formula changes are made by adding a new formula version with different `params` or a new calculator key -
never by editing a calculator in a way that changes historical semantics.
"""
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics.domain import (
    INSUFFICIENT,
    OK,
    SUPPRESSED,
    CalcResult,
    month_end,
    month_starts,
    ratio,
    suppress_small_counts,
)
from app.analytics.summary import dirty_from
from app.core.config import get_settings

TRANSFER_GAP_DAYS = 7  # a new employment within this many days in-scope = internal transfer, not a separation


@dataclass(frozen=True)
class Scope:
    tenant_id: int
    entity_ids: frozenset[int] | None = None  # None = all entities of tenant (already authorised)
    project_id: int | None = None
    occupation_group: str | None = None
    # ADR-017: ((entity_id, (project_id, ...)), ...) sorted - entities of entity_ids visible ONLY for these projects
    project_limits: tuple[tuple[int, tuple[int, ...]], ...] = ()

    @staticmethod
    def limits_of(limits: dict[int, frozenset[int]] | None, entity_ids) -> tuple[tuple[int, tuple[int, ...]], ...]:
        """Canonical project_limits restricted to entity_ids (None entity_ids = tenant-wide, no limits)."""
        if entity_ids is None or not limits:
            return ()
        return tuple((e, tuple(sorted(limits[e]))) for e in sorted(limits) if e in entity_ids)

    def entities_only(self) -> "Scope":
        """Same authorised entity scope (project limits included) without the user's project/occupation filters."""
        return Scope(self.tenant_id, self.entity_ids, project_limits=self.project_limits)

    def key(self) -> str:
        parts = ["le:" + (",".join(map(str, sorted(self.entity_ids))) if self.entity_ids is not None else "*")]
        if self.project_limits:
            parts.append("pl:" + ";".join(f"{e}=" + ",".join(map(str, ps)) for e, ps in self.project_limits))
        if self.project_id:
            parts.append(f"prj:{self.project_id}")
        if self.occupation_group:
            parts.append(f"occ:{self.occupation_group}")
        return "|".join(parts)

    def as_dict(self) -> dict:
        d = {
            "entity_ids": sorted(self.entity_ids) if self.entity_ids is not None else None,
            "project_id": self.project_id,
            "occupation_group": self.occupation_group,
        }
        if self.project_limits:
            d["project_limits"] = {str(e): list(ps) for e, ps in self.project_limits}
        return d


def _min_group() -> int:
    return get_settings().min_group_size


# ------------------------------------------------------------------ SQL fragment builders


def _ent_sql(scope: Scope, ent_col: str, p: dict, *, row_project: str | None = None, emp_alias: str | None = None,
             date_from: str = ":d", date_to: str = ":d") -> str:
    """Entity restriction of `scope` as SQL (ADR-017). Whole entities: `ent_col = ANY(:ents)`. A project-limited
    entity passes only when the row is attributable to an allowed project:
      row_project -> that column must hold an allowed project (NULL never passes);
      emp_alias   -> the employment must have an assignment on an allowed project overlapping [date_from, date_to];
      neither     -> the entity contributes nothing (the data has no project dimension).
    Parameter names are derived from the entity id, so fragments built for several aliases of one query agree."""
    if scope.entity_ids is None:
        return ""
    limited = dict(scope.project_limits)
    p["ents"] = [e for e in scope.entity_ids if e not in limited] or [-1]
    conds = [f"{ent_col} = ANY(:ents)"]
    for eid, projs in scope.project_limits:
        key = f"plim_{int(eid)}"
        if row_project:
            cond = f"{row_project} = ANY(:{key})"
        elif emp_alias:
            cond = (f"EXISTS (SELECT 1 FROM assignments ax WHERE ax.employment_id = {emp_alias}.id "
                    f"AND ax.project_id = ANY(:{key}) AND ax.valid_from <= {date_to} "
                    f"AND (ax.valid_to IS NULL OR ax.valid_to > {date_from}))")
        else:
            continue
        p[key] = list(projs)
        conds.append(f"({ent_col} = {int(eid)} AND {cond})")
    return f" AND ({' OR '.join(conds)})" if len(conds) > 1 else f" AND {conds[0]}"


def _spell(alias: str) -> dict:
    """Assignment window = the spell's own employment period (transfer detection on another spell)."""
    return {"date_from": f"{alias}.start_date", "date_to": f"coalesce({alias}.end_date, DATE '9999-12-31')"}


def _emp_where(scope: Scope, alias: str = "e", date_from: str = ":d", date_to: str = ":d",
               row_project: str | None = None) -> tuple[str, dict]:
    """Filter employments to the scope. Project/occupation filters go through assignments overlapping
    [date_from, date_to]. Project-limited entities (ADR-017): by `row_project` when the data row has its own project
    (timesheets, costs, assignments), otherwise by an assignment on an allowed project overlapping the same window."""
    sql = f" AND {alias}.tenant_id = :tenant"
    params: dict = {"tenant": scope.tenant_id}
    sql += _ent_sql(scope, f"{alias}.legal_entity_id", params, row_project=row_project, emp_alias=alias,
                    date_from=date_from, date_to=date_to)
    if scope.project_id or scope.occupation_group:
        sql += (
            f" AND EXISTS (SELECT 1 FROM assignments a LEFT JOIN occupations o ON o.id = a.occupation_id"
            f" WHERE a.employment_id = {alias}.id AND a.valid_from <= {date_to}"
            f" AND (a.valid_to IS NULL OR a.valid_to > {date_from})"
        )
        if scope.project_id:
            sql += " AND a.project_id = :project"
            params["project"] = scope.project_id
        if scope.occupation_group:
            sql += " AND o.occupation_group = :occ"
            params["occ"] = scope.occupation_group
        sql += ")"
    return sql, params


def headcount_series(db: Session, scope: Scope, dates: list[date]) -> dict[date, int]:
    # request-scoped memo per (scope, date): turnover, training, plan deviation ... share the same headcounts
    memo = db.info.setdefault("headcount_memo", {})
    sk = scope.key()
    todo = sorted({d for d in dates if (sk, d) not in memo})
    if todo:
        where, p = _emp_where(scope, date_from="d.d", date_to="d.d")
        rows = db.execute(
            text(
                "SELECT d.d, count(DISTINCT e.person_id) FROM unnest(CAST(:dates AS date[])) AS d(d) "
                "JOIN employment_relationships e ON e.start_date <= d.d AND (e.end_date IS NULL OR e.end_date >= d.d) "
                f"WHERE true {where} GROUP BY d.d"
            ),
            {**p, "dates": todo},
        ).all()
        found = {r[0]: int(r[1]) for r in rows}
        for d in todo:
            memo[(sk, d)] = found.get(d, 0)
    return {d: memo[(sk, d)] for d in dates}


def average_headcount(db: Session, scope: Scope, ps: date, pe: date) -> tuple[float, dict]:
    dates = sorted({ps, pe, *[min(month_end(m), pe) for m in month_starts(ps, pe)]})
    series = headcount_series(db, scope, dates)
    avg = sum(series.values()) / len(series) if series else 0.0
    return avg, {"method": "mean of headcount at period start and each month end", "points": len(series)}


def _separations(db: Session, scope: Scope, ps: date, pe: date) -> dict[str, int]:
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    where2, _ = _emp_where(scope.entities_only(), alias="e2", **_spell("e2"))
    rows = db.execute(
        text(
            "SELECT coalesce(e.termination_category, 'unknown'), count(*) FROM employment_relationships e "
            f"WHERE e.end_date BETWEEN :ps AND :pe {where} "
            "AND NOT EXISTS (SELECT 1 FROM employment_relationships e2 WHERE e2.person_id = e.person_id "
            "AND e2.id <> e.id AND e2.start_date > e.end_date AND e2.start_date <= e.end_date + :gap "
            f"{where2}) GROUP BY 1"
        ),
        {**p, "ps": ps, "pe": pe, "gap": TRANSFER_GAP_DAYS},
    ).all()
    return {r[0]: int(r[1]) for r in rows}


def _hours(db: Session, scope: Scope, ps: date, pe: date) -> dict:
    return _hours_windows(db, scope, [(ps, pe)])[(ps, pe)]


def _hours_windows(db: Session, scope: Scope, windows: list[tuple[date, date]]) -> dict[tuple[date, date], dict]:
    """Timesheet aggregates for several periods in ONE query per path (B-13). Each window is evaluated exactly as a
    single period would be: project/occupation assignment overlap is checked against that window's own dates.

    B-21: windows made of whole calendar months that do not touch a dirty month are read from timesheet_monthly
    (one row per employment x project x month instead of one per day/week); everything else reads the raw table.
    Both paths return identical numbers (tests/test_timesheet_summary.py)."""
    # request-scoped memo per (scope, window): a dashboard asks for the same aggregates from several KPIs
    # (absenteeism, utilisation, overtime, LTIFR, TRIR, night work ...). Dropped by engine.mark_stale_from.
    memo = db.info.setdefault("hours_memo", {})
    sk = scope.key()
    todo = [w for w in windows if (sk, w) not in memo]
    dirty = dirty_from(db, scope.tenant_id)
    fast = [w for w in todo if w[0].day == 1 and w[1] == month_end(w[1]) and (dirty is None or w[1] < dirty)]
    slow = [w for w in todo if w not in fast]
    found: dict[tuple[date, date], tuple] = {}
    if fast:
        found.update(_hours_query(db, scope, fast, summary=True))
    if slow:
        found.update(_hours_query(db, scope, slow, summary=False))
    for w in todo:
        memo[(sk, w)] = found.get(w, (0, 0, 0, 0, 0, 0, 0, 0, 0))
    out = {}
    for w in windows:
        r = memo[(sk, w)]
        out[w] = {
            "rows": int(r[0]), "planned": float(r[1]), "worked": float(r[2]), "overtime": float(r[3]),
            "productive": float(r[4]), "productive_rows": int(r[5]), "planned_with_productive": float(r[6]),
            "persons": int(r[7]), "night": float(r[8]),
        }
    return out


def _hours_query(db: Session, scope: Scope, windows: list[tuple[date, date]], *, summary: bool) -> dict:
    alias = "s" if summary else "t"
    where, p = _emp_where(replace(scope, project_id=None), date_from="w.ps", date_to="w.pe",
                          row_project=f"{alias}.project_id")
    proj = ""
    if scope.project_id:
        proj = f" AND {alias}.project_id = :project"
        p["project"] = scope.project_id
    if summary:
        agg = ("coalesce(sum(s.n),0), coalesce(sum(s.planned),0), coalesce(sum(s.worked),0), "
               "coalesce(sum(s.overtime),0), coalesce(sum(s.productive),0), coalesce(sum(s.n_productive),0), "
               "coalesce(sum(s.planned_productive),0), count(DISTINCT e.person_id), coalesce(sum(s.night),0)")
        src = ("JOIN timesheet_monthly s ON s.tenant_id = :tenant AND s.month BETWEEN w.ps AND w.pe "
               "JOIN employment_relationships e ON e.id = s.employment_id WHERE true")
    else:
        agg = ("count(*), coalesce(sum(t.planned_hours),0), coalesce(sum(t.worked_hours),0), "
               "coalesce(sum(t.overtime_hours),0), coalesce(sum(t.productive_hours),0), count(t.productive_hours), "
               "coalesce(sum(t.planned_hours) FILTER (WHERE t.productive_hours IS NOT NULL),0), "
               "count(DISTINCT e.person_id), coalesce(sum(t.night_hours),0)")
        src = ("JOIN timesheets t ON t.tenant_id = :tenant AND t.work_date BETWEEN w.ps AND w.pe "
               "JOIN employment_relationships e ON e.id = t.employment_id WHERE t.status = 'approved'")
    rows = db.execute(
        text(f"SELECT w.ps, w.pe, {agg} FROM unnest(CAST(:starts AS date[]), CAST(:ends AS date[])) AS w(ps, pe) "
             f"{src} {where}{proj} GROUP BY w.ps, w.pe"),
        {**p, "starts": [w[0] for w in windows], "ends": [w[1] for w in windows]},
    ).all()
    return {(r[0], r[1]): r[2:] for r in rows}


def turnover_rate(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-001: separations in period / average headcount * 100. Internal transfers within scope excluded."""
    seps = _separations(db, scope, ps, pe)
    excluded = set(params.get("exclude_categories", []))
    included = params.get("include_categories")  # KPI-020: only these categories count (e.g. ["voluntary"])
    counted = {k: v for k, v in seps.items() if k not in excluded and (included is None or k in included)}
    n = sum(counted.values())
    avg, avg_lineage = average_headcount(db, scope, ps, pe)
    res = ratio(n, avg, sample_size=round(avg), min_sample=_min_group(), has_source_data=avg > 0 or n > 0)
    if res.status == OK:
        res.breakdown = {"by_termination_category": suppress_small_counts(seps, round(avg), _min_group())}
    unknown = seps.get("unknown", 0)
    lineage = {"tables": ["employment_relationships", "assignments"], "separations": n,
               "excluded_categories": sorted(excluded), "transfer_gap_days": TRANSFER_GAP_DAYS,
               "average_headcount": avg_lineage}
    if included is None:
        res.quality_score = round(100 * (1 - unknown / n), 1) if n else 100.0
        if unknown:
            res.note = f"{unknown} separation(s) have no recorded reason (reported as 'unknown', not assumed)"
    else:
        # a separation without a reason might belong to an included category: quality reflects that uncertainty
        all_seps = sum(seps.values())
        res.quality_score = round(100 * (1 - unknown / all_seps), 1) if all_seps else 100.0
        lineage["included_categories"] = sorted(included)
        if unknown and "unknown" not in included:
            res.note = (f"{unknown} separation(s) have no recorded reason and are NOT counted; the true rate may be "
                        "higher")
    res.lineage = lineage
    return res


def retention_rate(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-002: persons employed in scope at period start still employed in scope at period end."""
    where, p = _emp_where(scope, date_from=":ps", date_to=":ps")
    where_end, _ = _emp_where(scope, alias="e2", date_from=":pe", date_to=":pe")
    r = db.execute(
        text(
            "WITH start_pop AS (SELECT DISTINCT e.person_id FROM employment_relationships e "
            f"WHERE e.start_date <= :ps AND (e.end_date IS NULL OR e.end_date >= :ps) {where}) "
            "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM employment_relationships e2 "
            "WHERE e2.person_id = s.person_id AND e2.start_date <= :pe AND (e2.end_date IS NULL OR e2.end_date >= :pe)"
            f" {where_end})) FROM start_pop s"
        ),
        {**p, "ps": ps, "pe": pe},
    ).one()
    start_n, retained = int(r[0]), int(r[1])
    res = ratio(retained, start_n, sample_size=start_n, min_sample=_min_group(), has_source_data=start_n > 0)
    res.quality_score = 100.0
    res.lineage = {"tables": ["employment_relationships"], "start_population": start_n, "retained": retained}
    return res


def absenteeism_rate(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-003: absence hours (types in params.absence_types) / planned hours * 100."""
    absence_types = params.get("absence_types", ["unexcused", "sick", "annual", "unpaid", "other"])
    hours = _hours(db, scope, ps, pe)
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    rows = db.execute(
        text(
            "SELECT l.leave_type, sum(l.hours * (LEAST(l.end_date, :pe) - GREATEST(l.start_date, :ps) + 1)::numeric "
            "/ (l.end_date - l.start_date + 1)) FROM leave_records l JOIN employment_relationships e "
            "ON e.id = l.employment_id WHERE l.tenant_id = :tenant AND l.start_date <= :pe AND l.end_date >= :ps "
            f"{where} GROUP BY 1"
        ),
        {**p, "ps": ps, "pe": pe},
    ).all()
    by_type = {r[0]: round(float(r[1]), 2) for r in rows}
    absent = sum(v for k, v in by_type.items() if k in absence_types)
    res = ratio(absent, hours["planned"], sample_size=hours["persons"], min_sample=_min_group(),
                has_source_data=hours["rows"] > 0)
    if res.status == OK:
        res.breakdown = {
            "included_hours_by_type": {k: v for k, v in by_type.items() if k in absence_types},
            "excluded_hours_by_type": {k: v for k, v in by_type.items() if k not in absence_types},
            "included_types": absence_types,
        }
    res.quality_score = 100.0 if hours["rows"] else 0.0
    res.lineage = {"tables": ["leave_records", "timesheets"], "timesheet_rows": hours["rows"],
                   "leave_types_found": sorted(by_type), "proration": "calendar-day overlap"}
    return res


def protected_leave_share(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-054 (decision D-6): maternity / parental and related protected leave hours / planned hours x 100.
    Planning figure only - never part of absenteeism. Health-adjacent (KVKK md.6): suppressed unless at least
    HRSWI_MIN_GROUP_SIZE distinct people took such leave in the group, so no individual can be inferred."""
    types = params.get("absence_types", ["parental"])
    hours = _hours(db, scope, ps, pe)
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    r = db.execute(
        text("SELECT coalesce(sum(l.hours * (LEAST(l.end_date, :pe) - GREATEST(l.start_date, :ps) + 1)::numeric "
             "/ (l.end_date - l.start_date + 1)), 0), count(DISTINCT e.person_id) FROM leave_records l "
             "JOIN employment_relationships e ON e.id = l.employment_id WHERE l.tenant_id = :tenant "
             f"AND l.leave_type = ANY(:types) AND l.start_date <= :pe AND l.end_date >= :ps {where}"),
        {**p, "ps": ps, "pe": pe, "types": types},
    ).one()
    leave_h, takers = float(r[0]), int(r[1])
    if 0 < takers < _min_group():
        return CalcResult(SUPPRESSED, quality_score=100.0 if hours["rows"] else 0.0,
                          note=f"Fewer than {_min_group()} people took protected leave in this group",
                          lineage={"tables": ["leave_records", "timesheets"]})
    res = ratio(leave_h, hours["planned"], sample_size=hours["persons"], min_sample=_min_group(),
                has_source_data=hours["rows"] > 0)
    res.quality_score = 100.0 if hours["rows"] else 0.0
    res.lineage = {"tables": ["leave_records", "timesheets"], "included_types": types,
                   "proration": "calendar-day overlap"}
    return res


def _annual_leave_population(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> list[dict]:
    """Population for KPI-055/056 (decision D-7, corrected per HR Director review):
    - person active in scope at period end; current employer = legal entity of the latest active employment;
    - service = ALL spells with that same employer up to period end (4857 md.54), not just the current contract;
    - one-year date approximated as period_end - (service_days - 365);
    - statutory entitlement (md.53) by service years and age, prorated from max(period start, one-year date);
    - long_absent: sick / parental / unpaid days in the window > half of the window's working days.
    Returns dicts with entitlement, taken, one_year_date, long_absent, birth_known."""
    hpd = float(params.get("hours_per_day", 9))
    w_end, p = _emp_where(scope, date_from=":pe", date_to=":pe")
    rows = db.execute(
        text("WITH cur AS (SELECT DISTINCT ON (e.person_id) e.person_id, e.legal_entity_id, pp.birth_year birth "
             "FROM employment_relationships e JOIN people pp ON pp.id = e.person_id "
             f"WHERE e.start_date <= :pe AND (e.end_date IS NULL OR e.end_date >= :pe) {w_end} "
             "ORDER BY e.person_id, e.start_date DESC), "
             "svc AS (SELECT c.person_id, c.legal_entity_id, c.birth, "
             "sum(LEAST(coalesce(x.end_date, :pe), :pe) - x.start_date + 1) service_days "
             "FROM cur c JOIN employment_relationships x ON x.person_id = c.person_id "
             "AND x.legal_entity_id = c.legal_entity_id AND x.start_date <= :pe GROUP BY 1, 2, 3), "
             "lv AS (SELECT x.person_id, l.leave_type, sum(l.hours * (LEAST(l.end_date, :pe) - GREATEST(l.start_date, :ps)"
             " + 1)::numeric / (l.end_date - l.start_date + 1)) h FROM leave_records l "
             "JOIN employment_relationships x ON x.id = l.employment_id JOIN svc ON svc.person_id = x.person_id "
             "AND svc.legal_entity_id = x.legal_entity_id WHERE l.tenant_id = :tenant AND l.start_date <= :pe "
             "AND l.end_date >= :ps AND l.leave_type IN ('annual', 'sick', 'parental', 'unpaid') GROUP BY 1, 2) "
             "SELECT svc.service_days, svc.birth, "
             "coalesce(sum(lv.h) FILTER (WHERE lv.leave_type = 'annual'), 0), "
             "coalesce(sum(lv.h) FILTER (WHERE lv.leave_type <> 'annual'), 0) "
             "FROM svc LEFT JOIN lv ON lv.person_id = svc.person_id WHERE svc.service_days >= 365 "
             "GROUP BY svc.person_id, svc.service_days, svc.birth"),
        {**p, "ps": ps, "pe": pe},
    ).all()
    window_days = (pe - ps).days + 1
    half_working_days = window_days * 5 / 7 / 2
    out = []
    for service_days, birth, annual_h, absent_h in rows:
        years = service_days / 365.25
        ent = 14 if years <= 5 else 20 if years < 15 else 26  # 4857 md.53 (5 years inclusive -> 14 days)
        if birth is not None and (pe.year - birth <= 18 or pe.year - birth >= 50):
            ent = max(ent, 20)
        one_year = pe - timedelta(days=service_days - 365)
        start = max(ps, one_year)
        out.append({"entitlement": ent * ((pe - start).days + 1) / 365, "taken": float(annual_h) / hpd,
                    "one_year_date": one_year, "long_absent": float(absent_h) / hpd > half_working_days,
                    "birth_known": birth is not None})
    return out


def _annual_leave_lineage(pop: list[dict], params: dict, **extra) -> dict:
    return {"tables": ["employment_relationships", "people", "leave_records"],
            "population": "active at period end, >= 1 year of service with the current employer (all spells)",
            "entitlement_rule": "4857 md.53: <=5y 14d, 5-15y 20d, >=15y 26d; age <=18 or >=50 min 20d; "
                                "prorated from max(period start, one-year date)",
            "hours_per_day": params.get("hours_per_day", 9), "eligible": len(pop), **extra}


def annual_leave_usage(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-055 (decision D-7): annual-leave days taken / statutory entitlement days x 100 (group level only).
    Can exceed 100 when carried-over leave is used (a good outcome)."""
    pop = _annual_leave_population(db, scope, ps, pe, params)
    res = ratio(sum(x["taken"] for x in pop), sum(x["entitlement"] for x in pop), sample_size=len(pop),
                min_sample=_min_group(), has_source_data=bool(pop))
    res.quality_score = round(100 * sum(1 for x in pop if x["birth_known"]) / len(pop), 1) if pop else 0.0
    res.lineage = _annual_leave_lineage(pop, params)
    return res


def annual_leave_nonuse(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-056 (decision D-7): eligible persons with no annual leave taken in the period / eligible persons x 100.
    Population: one-year date on/before period start, excluding persons on long sick/parental/unpaid absence
    (reported only as a count). Early-warning indicator at team level - never used to rank individuals."""
    full = _annual_leave_population(db, scope, ps, pe, params)
    long_absent = sum(1 for x in full if x["one_year_date"] <= ps and x["long_absent"])
    pop = [x for x in full if x["one_year_date"] <= ps and not x["long_absent"]]
    res = ratio(sum(1 for x in pop if x["taken"] <= 0), len(pop), sample_size=len(pop), min_sample=_min_group(),
                has_source_data=bool(pop))
    res.quality_score = 100.0 if pop else 0.0
    res.lineage = _annual_leave_lineage(pop, params, excluded_long_absence=long_absent)
    if ((pe - ps).days + 1) < 360:
        res.note = "Meaningful on 12-month windows; shorter periods overstate non-use"
    return res


def capacity_utilization(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-004: productive hours / planned hours, only over rows where productive hours were recorded."""
    h = _hours(db, scope, ps, pe)
    coverage = h["productive_rows"] / h["rows"] if h["rows"] else 0
    min_cov = params.get("min_coverage", 0.5)
    if h["rows"] and coverage < min_cov:
        return CalcResult(INSUFFICIENT, sample_size=h["persons"], quality_score=round(coverage * 100, 1),
                          note=f"Productive hours recorded on only {coverage:.0%} of timesheet rows (min {min_cov:.0%})",
                          lineage={"tables": ["timesheets"], "rows": h["rows"]})
    res = ratio(h["productive"], h["planned_with_productive"], sample_size=h["persons"], min_sample=_min_group(),
                has_source_data=h["rows"] > 0)
    res.quality_score = round(coverage * 100, 1)
    res.lineage = {"tables": ["timesheets"], "rows": h["rows"], "rows_with_productive_hours": h["productive_rows"]}
    return res


def _outputs(db: Session, scope: Scope, ps: date, pe: date) -> dict:
    p: dict = {"tenant": scope.tenant_id, "ps": ps, "pe": pe}
    where = ""
    where += _ent_sql(scope, "w.legal_entity_id", p, row_project="wp.project_id")
    if scope.project_id:
        where += " AND wp.project_id = :project"
        p["project"] = scope.project_id
    if scope.occupation_group:
        where += " AND wp.occupation_group = :occ"
        p["occ"] = scope.occupation_group
    r = db.execute(
        text(
            "SELECT count(*), coalesce(sum(w.labor_hours),0), coalesce(sum(w.rework_hours),0), "
            "coalesce(sum(w.accepted_quantity),0), "
            "coalesce(sum(w.accepted_quantity * ps.std_hours_per_unit) FILTER (WHERE ps.id IS NOT NULL),0), "
            "coalesce(sum(w.labor_hours) FILTER (WHERE ps.id IS NOT NULL AND w.accepted_quantity IS NOT NULL),0), "
            "count(*) FILTER (WHERE ps.id IS NULL), count(*) FILTER (WHERE w.accepted_quantity IS NULL) "
            "FROM work_outputs w JOIN work_packages wp ON wp.id = w.work_package_id "
            "LEFT JOIN production_standards ps ON ps.work_package_id = w.work_package_id "
            " AND ps.valid_from <= w.output_date AND (ps.valid_to IS NULL OR ps.valid_to > w.output_date) "
            f"WHERE w.tenant_id = :tenant AND w.output_date BETWEEN :ps AND :pe {where}"
        ),
        p,
    ).one()
    return {"rows": int(r[0]), "labor": float(r[1]), "rework": float(r[2]), "accepted": float(r[3]),
            "std_hours": float(r[4]), "labor_std": float(r[5]), "no_standard": int(r[6]), "not_inspected": int(r[7])}


def productivity_index(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-005: standard hours earned by quality-accepted output / actual labour hours * 100.
    Only outputs with a valid production standard and an inspection result are included."""
    o = _outputs(db, scope, ps, pe)
    res = ratio(o["std_hours"], o["labor_std"], has_source_data=o["rows"] - o["no_standard"] > 0)
    res.quality_score = round(100 * (o["rows"] - o["no_standard"] - o["not_inspected"]) / o["rows"], 1) if o["rows"] else 0
    res.lineage = {"tables": ["work_outputs", "production_standards", "work_packages"], "output_rows": o["rows"],
                   "excluded_no_standard": o["no_standard"], "excluded_not_inspected": o["not_inspected"]}
    return res


def plan_deviation(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-006: actual - planned headcount (mean over months with a plan). Absolute value; % in breakdown."""
    p: dict = {"tenant": scope.tenant_id, "ps": date(ps.year, ps.month, 1), "pe": pe}
    where = ""
    where += _ent_sql(scope, "legal_entity_id", p, row_project="project_id")
    if scope.project_id:
        where += " AND project_id = :project"
        p["project"] = scope.project_id
    if scope.occupation_group:
        # plans without an occupation cannot be compared to an occupation-filtered actual
        where += " AND occupation_id IN (SELECT id FROM occupations WHERE tenant_id = :tenant AND occupation_group = :occ)"
        p["occ"] = scope.occupation_group
    # only the latest plan version per (entity, project, occupation, month) counts - no double counting
    plans = dict(db.execute(
        text("SELECT period_month, sum(planned_headcount) FROM workforce_plans w WHERE tenant_id = :tenant "
             f"AND period_month BETWEEN :ps AND :pe {where} AND plan_version = (SELECT max(plan_version) "
             "FROM workforce_plans w2 WHERE w2.tenant_id = w.tenant_id AND w2.legal_entity_id = w.legal_entity_id "
             "AND w2.project_id IS NOT DISTINCT FROM w.project_id AND w2.occupation_id IS NOT DISTINCT FROM w.occupation_id "
             "AND w2.period_month = w.period_month) GROUP BY 1"), p).all())
    months = month_starts(ps, pe)
    planned_months = [m for m in months if m in plans]
    if not planned_months:
        return CalcResult(INSUFFICIENT, quality_score=0.0, note="No workforce plan exists for this scope/period",
                          lineage={"tables": ["workforce_plans"]})
    actual = headcount_series(db, scope, [min(month_end(m), pe) for m in planned_months])
    act_vals = list(actual.values())
    planned_avg = sum(float(plans[m]) for m in planned_months) / len(planned_months)
    actual_avg = sum(act_vals) / len(act_vals)
    dev = round(actual_avg - planned_avg, 2)
    res = CalcResult(OK, dev, actual_avg, planned_avg, sample_size=round(actual_avg))
    res.breakdown = {
        "planned_avg": round(planned_avg, 2), "actual_avg": round(actual_avg, 2),
        "deviation_pct": round(dev / planned_avg * 100, 2) if planned_avg else None,
        "monthly": [{"month": m.isoformat(), "planned": float(plans[m]), "actual": actual[min(month_end(m), pe)]}
                    for m in planned_months],
    }
    res.quality_score = round(100 * len(planned_months) / len(months), 1)
    res.lineage = {"tables": ["workforce_plans", "employment_relationships"], "months_with_plan": len(planned_months),
                   "months_in_period": len(months)}
    return res


def overtime_rate(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-007: overtime hours / total actual worked hours (worked_hours includes overtime) * 100."""
    return _overtime_from_hours(_hours(db, scope, ps, pe))


def _overtime_from_hours(h: dict) -> CalcResult:
    res = ratio(h["overtime"], h["worked"], sample_size=h["persons"], min_sample=_min_group(),
                has_source_data=h["rows"] > 0)
    res.quality_score = 100.0 if h["rows"] else 0.0
    res.breakdown = {"overtime_hours": round(h["overtime"], 1), "worked_hours": round(h["worked"], 1),
                     "night_hours": round(h["night"], 1)} if res.status == OK else {}
    res.lineage = {"tables": ["timesheets"], "rows": h["rows"]}
    return res


def training_hours_per_employee(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-008: total training hours / average headcount."""
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    r = db.execute(
        text("SELECT count(*), coalesce(sum(tr.hours),0) FROM training_records tr JOIN employment_relationships e "
             f"ON e.id = tr.employment_id WHERE tr.completed_on BETWEEN :ps AND :pe {where}"),
        {**p, "ps": ps, "pe": pe},
    ).one()
    avg, _ = average_headcount(db, scope, ps, pe)
    res = ratio(float(r[1]), avg, scale=1.0, sample_size=round(avg), min_sample=_min_group(), has_source_data=avg > 0)
    res.quality_score = 100.0
    res.lineage = {"tables": ["training_records", "employment_relationships"], "training_rows": int(r[0])}
    return res


def competency_coverage(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-009: active assignments (at period end) whose holder has all mandatory competencies valid."""
    where, p = _emp_where(scope, date_from=":pe", date_to=":pe", row_project="a.project_id")
    r = db.execute(
        text(
            "WITH req AS (SELECT occupation_id, count(*) n FROM occupation_competencies "
            " WHERE tenant_id = :tenant AND mandatory GROUP BY occupation_id), "
            "act AS (SELECT DISTINCT e.person_id, a.occupation_id FROM assignments a JOIN employment_relationships e "
            " ON e.id = a.employment_id WHERE a.valid_from <= :pe AND (a.valid_to IS NULL OR a.valid_to > :pe) "
            f" AND e.start_date <= :pe AND (e.end_date IS NULL OR e.end_date >= :pe) {where}), "
            "have AS (SELECT act.person_id, act.occupation_id, count(DISTINCT oc.competency_id) h FROM act "
            " JOIN occupation_competencies oc ON oc.occupation_id = act.occupation_id AND oc.mandatory "
            " JOIN employee_competencies ec ON ec.person_id = act.person_id AND ec.competency_id = oc.competency_id "
            "  AND ec.valid_from <= :pe AND (ec.valid_to IS NULL OR ec.valid_to > :pe) GROUP BY 1, 2) "
            "SELECT count(*), count(*) FILTER (WHERE coalesce(h.h, 0) >= req.n) FROM act "
            "JOIN req ON req.occupation_id = act.occupation_id "
            "LEFT JOIN have h ON h.person_id = act.person_id AND h.occupation_id = act.occupation_id"
        ),
        {**p, "pe": pe},
    ).one()
    evaluated, covered = int(r[0]), int(r[1])
    res = ratio(covered, evaluated, sample_size=evaluated, min_sample=_min_group(), has_source_data=evaluated > 0)
    res.quality_score = 100.0 if evaluated else 0.0
    res.lineage = {"tables": ["assignments", "occupation_competencies", "employee_competencies"],
                   "evaluated_assignments": evaluated, "covered": covered, "as_of": pe.isoformat()}
    return res


def _fx(db: Session, ccy: str, to: str, on: date) -> float | None:
    if ccy == to:
        return 1.0
    r = db.execute(
        text("SELECT rate FROM fx_rates WHERE from_ccy = :f AND to_ccy = :t AND rate_date <= :d "
             "AND rate_date > :d - 45 ORDER BY rate_date DESC LIMIT 1"),
        {"f": ccy, "t": to, "d": on},
    ).scalar()
    return float(r) if r is not None else None


def labor_cost_total(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> tuple[float | None, dict]:
    """Total labour cost in reporting currency. Returns (None, info) when any FX rate is missing."""
    to = params.get("reporting_currency", "TRY")
    where, p = _emp_where(replace(scope, project_id=None), date_from=":ps", date_to=":pe",
                          row_project="pc.project_id")
    proj = ""
    if scope.project_id:
        proj = " AND pc.project_id = :project"
        p["project"] = scope.project_id
    rows = db.execute(
        text("SELECT pc.currency, pc.period_month, pc.cost_type, sum(pc.amount) "
             "FROM payroll_costs pc JOIN employment_relationships e ON e.id = pc.employment_id "
             f"WHERE pc.tenant_id = :tenant AND pc.period_month BETWEEN :ps AND :pe {where}{proj} GROUP BY 1, 2, 3"),
        {**p, "ps": date(ps.year, ps.month, 1), "pe": pe},
    ).all()
    # privacy population = largest number of distinct employments costed in any single month (B-21: kept out of the
    # main aggregation, whose count(DISTINCT person) forced a 1M-row sort)
    def population(cost_type_filter: str) -> int:
        return int(db.execute(
            text("SELECT coalesce(max(c), 0) FROM (SELECT pc.period_month, count(*) c FROM (SELECT pc.period_month, "
                 "pc.employment_id FROM payroll_costs pc JOIN employment_relationships e ON e.id = pc.employment_id "
                 f"WHERE pc.tenant_id = :tenant AND pc.period_month BETWEEN :ps AND :pe {cost_type_filter} "
                 f"{where}{proj} GROUP BY 1, 2) pc GROUP BY 1) m"),
            {**p, "ps": date(ps.year, ps.month, 1), "pe": pe},
        ).scalar())

    # employees paid a wage in the busiest month (falls back to any cost row when no gross_wage rows exist)
    persons = population("AND pc.cost_type = 'gross_wage'") if rows else 0
    if rows and persons == 0:
        persons = population("")
    total, missing, by_type, ccys = 0.0, [], {}, set()
    for ccy, month, ctype, amount in rows:
        ccys.add(ccy)
        rate = _fx(db, ccy, to, month_end(month))
        if rate is None:
            missing.append(f"{ccy}->{to}@{month.isoformat()}")
            continue
        conv = float(amount) * rate
        total += conv
        by_type[ctype] = round(by_type.get(ctype, 0) + conv, 2)
    info = {"reporting_currency": to, "source_currencies": sorted(ccys), "by_cost_type": by_type,
            "fx_source": "fx_rates (latest rate within 45 days before month end)", "rows": len(rows),
            "persons": persons, "missing_fx": missing}
    if not rows or missing:
        return None, info
    return total, info


def _output_units(db: Session, scope: Scope, ps: date, pe: date) -> list[str]:
    p: dict = {"tenant": scope.tenant_id, "ps": ps, "pe": pe}
    where = ""
    where += _ent_sql(scope, "w.legal_entity_id", p, row_project="wp.project_id")
    if scope.project_id:
        where += " AND wp.project_id = :project"
        p["project"] = scope.project_id
    if scope.occupation_group:
        where += " AND wp.occupation_group = :occ"
        p["occ"] = scope.occupation_group
    return sorted(db.execute(text(
        "SELECT DISTINCT wp.unit FROM work_outputs w JOIN work_packages wp ON wp.id = w.work_package_id "
        f"WHERE w.tenant_id = :tenant AND w.output_date BETWEEN :ps AND :pe AND w.accepted_quantity IS NOT NULL {where}"),
        p).scalars())


def unit_labor_cost(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-010: labour cost allocated to the output / accepted quantity (project-level indicator)."""
    total, info = labor_cost_total(db, scope, ps, pe, params)
    o = _outputs(db, scope, ps, pe)
    if total is None:
        note = ("Missing FX rate(s): " + ", ".join(info["missing_fx"])) if info["missing_fx"] else "No cost data"
        return CalcResult(INSUFFICIENT, note=note, quality_score=0.0, lineage=info)
    units = _output_units(db, scope, ps, pe)
    if len(units) > 1:
        return CalcResult(INSUFFICIENT, quality_score=0.0, lineage={"tables": ["work_outputs"], "units": units},
                          note="Output measured in several units (" + ", ".join(units) + "); quantities cannot be "
                               "added - filter to a project/work type with a single unit")
    res = ratio(total, o["accepted"], scale=1.0, sample_size=info["persons"], min_sample=_min_group(),
                has_source_data=o["rows"] > 0)
    res.lineage = {**info, "output_rows": o["rows"], "accepted_quantity": o["accepted"]}
    res.quality_score = 100.0 if o["rows"] else 0.0
    return res


def time_to_hire(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-011: mean days from requisition approval to accepted offer, for offers accepted in period."""
    p: dict = {"tenant": scope.tenant_id, "ps": ps, "pe": pe}
    where = ""
    where = _ent_sql(scope, "legal_entity_id", p)  # no project column: project-limited entities excluded
    r = db.execute(
        text("SELECT count(*), coalesce(sum(offer_accepted_on - approved_on),0) FROM requisitions "
             f"WHERE tenant_id = :tenant AND offer_accepted_on BETWEEN :ps AND :pe {where}"
             + (" AND occupation_id IN (SELECT id FROM occupations WHERE tenant_id = :tenant AND occupation_group = :occ)"
                if scope.occupation_group else "")), {**p, "occ": scope.occupation_group}).one()
    res = ratio(float(r[1]), int(r[0]), scale=1.0, sample_size=int(r[0]), has_source_data=int(r[0]) > 0)
    res.quality_score = 100.0 if r[0] else 0.0
    res.lineage = {"tables": ["requisitions"], "filled_requisitions": int(r[0])}
    if scope.project_id:
        res.note = "Requisitions are not project-scoped; project filter not applied"
    return res


def rework_rate(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-013: rework hours / total labour hours on recorded outputs * 100."""
    o = _outputs(db, scope, ps, pe)
    res = ratio(o["rework"], o["labor"], has_source_data=o["rows"] > 0)
    res.quality_score = 100.0 if o["rows"] else 0.0
    res.lineage = {"tables": ["work_outputs"], "output_rows": o["rows"]}
    return res


def _safety_rate(db: Session, scope: Scope, ps: date, pe: date, types: list[str], multiplier: float) -> CalcResult:
    if scope.occupation_group:
        return CalcResult(INSUFFICIENT, quality_score=0.0, lineage={"tables": ["safety_events"]},
                          note="Safety events are not recorded per occupation; rate not available for an occupation filter")
    p: dict = {"tenant": scope.tenant_id, "ps": ps, "pe": pe, "types": types}
    where = ""
    where += _ent_sql(scope, "legal_entity_id", p, row_project="project_id")
    if scope.project_id:
        where += " AND project_id = :project"
        p["project"] = scope.project_id
    rows = dict(db.execute(
        text("SELECT event_type, count(*) FROM safety_events WHERE tenant_id = :tenant "
             f"AND event_date BETWEEN :ps AND :pe AND event_type = ANY(:types) {where} GROUP BY 1"), p).all())
    n = sum(rows.values())
    h = _hours(db, scope, ps, pe)
    res = ratio(n, h["worked"], scale=multiplier, has_source_data=h["rows"] > 0, sample_size=h["persons"],
                min_sample=_min_group())
    res.breakdown = {"events_by_type": rows, "hours_worked": round(h["worked"], 1), "multiplier": multiplier}
    res.quality_score = 100.0 if h["rows"] else 0.0
    res.lineage = {"tables": ["safety_events", "timesheets"], "event_types_counted": types}
    return res


def ltifr(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-014A LTIFR: lost-time injuries (incl. fatalities) per 1,000,000 hours worked."""
    return _safety_rate(db, scope, ps, pe, params.get("event_types", ["lost_time_injury", "fatality"]),
                        params.get("multiplier", 1_000_000))


def trir(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-014B TRIR: recordable injuries per 200,000 hours worked (OSHA convention)."""
    return _safety_rate(db, scope, ps, pe,
                        params.get("event_types", ["fatality", "lost_time_injury", "restricted_work", "medical_treatment"]),
                        params.get("multiplier", 200_000))


def active_headcount(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-016: distinct persons with an active employment on the last day of the period."""
    return _headcount_result(headcount_series(db, scope, [pe])[pe], pe)


def _headcount_result(n: int, pe: date) -> CalcResult:
    return CalcResult(OK, float(n), sample_size=n, quality_score=100.0,
                      lineage={"tables": ["employment_relationships"], "as_of": pe.isoformat()})


def headcount_by_entity(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> dict[int, CalcResult]:
    """KPI-016 for every entity in scope.entity_ids with ONE grouped query (B-21: firm breakdowns of 200+ entities).
    Identical to evaluating active_headcount with scope.entity_ids = {entity}."""
    where, p = _emp_where(scope, date_from=":d", date_to=":d")
    rows = dict(db.execute(
        text("SELECT e.legal_entity_id, count(DISTINCT e.person_id) FROM employment_relationships e "
             f"WHERE e.start_date <= :d AND (e.end_date IS NULL OR e.end_date >= :d) {where} GROUP BY 1"),
        {**p, "d": pe},
    ).all())
    return {eid: _headcount_result(int(rows.get(eid, 0)), pe) for eid in (scope.entity_ids or ())}


BY_ENTITY_BATCH = {"active_headcount": headcount_by_entity}


def new_hire_count(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-017: employments starting in period that are not internal transfers within scope."""
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    where2, _ = _emp_where(scope.entities_only(), alias="e2", **_spell("e2"))
    n = db.execute(
        text("SELECT count(*) FROM employment_relationships e WHERE e.start_date BETWEEN :ps AND :pe "
             f"{where} AND NOT EXISTS (SELECT 1 FROM employment_relationships e2 WHERE e2.person_id = e.person_id "
             "AND e2.id <> e.id AND e2.end_date < e.start_date AND e2.end_date >= e.start_date - :gap "
             f"{where2})"),
        {**p, "ps": ps, "pe": pe, "gap": TRANSFER_GAP_DAYS},
    ).scalar()
    return CalcResult(OK, float(n), sample_size=int(n), quality_score=100.0,
                      lineage={"tables": ["employment_relationships"], "transfer_gap_days": TRANSFER_GAP_DAYS})


def early_turnover_90(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-018: share of hires (start in period) who left within `days` days of starting."""
    days = params.get("days", 90)
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    ents = scope.entities_only()
    w_in, _ = _emp_where(ents, alias="e2", **_spell("e2"))
    w_out, _ = _emp_where(ents, alias="e3", **_spell("e3"))
    # same transfer rule as KPI-001/017: transfers in are not hires, transfers out are not early leavers
    r = db.execute(
        text("SELECT count(*), count(*) FILTER (WHERE e.end_date IS NOT NULL AND e.end_date < e.start_date + :days "
             "AND NOT EXISTS (SELECT 1 FROM employment_relationships e3 WHERE e3.person_id = e.person_id "
             f"AND e3.id <> e.id AND e3.start_date > e.end_date AND e3.start_date <= e.end_date + :gap {w_out})) "
             f"FROM employment_relationships e WHERE e.start_date BETWEEN :ps AND :pe {where} "
             "AND NOT EXISTS (SELECT 1 FROM employment_relationships e2 WHERE e2.person_id = e.person_id "
             f"AND e2.id <> e.id AND e2.end_date < e.start_date AND e2.end_date >= e.start_date - :gap {w_in})"),
        {**p, "ps": ps, "pe": pe, "days": days, "gap": TRANSFER_GAP_DAYS},
    ).one()
    hires, early = int(r[0]), int(r[1])
    res = ratio(early, hires, sample_size=hires, min_sample=_min_group(), has_source_data=True)
    observed_until = date.today()
    res.quality_score = 100.0 if pe + timedelta(days=days) <= observed_until else 50.0
    if pe + timedelta(days=days) > observed_until:
        res.note = f"Cohort not yet fully observed ({days} days after period end); value may still rise"
    res.lineage = {"tables": ["employment_relationships"], "hires": hires, "early_leavers": early, "window_days": days}
    return res


def labor_cost_per_employee(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-019: total employer labour cost / average headcount (sensitive, requires cost.read)."""
    total, info = labor_cost_total(db, scope, ps, pe, params)
    if total is None:
        note = ("Missing FX rate(s): " + ", ".join(info["missing_fx"])) if info["missing_fx"] else "No cost data"
        return CalcResult(INSUFFICIENT, note=note, quality_score=0.0, lineage=info)
    avg, _ = average_headcount(db, scope, ps, pe)
    res = ratio(total, avg, scale=1.0, sample_size=round(avg), min_sample=_min_group())
    res.breakdown = {"by_cost_type": info["by_cost_type"], "reporting_currency": info["reporting_currency"]}
    res.lineage = info
    res.quality_score = 100.0
    return res


# ------------------------------------------------------------------ B-10 calculators (KPI-023/032/034/036/048/050)


def average_tenure(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-023: mean tenure (years) of persons active in scope at period end. Tenure = days since the start of the
    CURRENT spell (a re-hire starts from zero; earlier spells are not added). A person with several concurrent active
    in-scope spells counts once, with the longest-running one. Years = days / 365.25."""
    where, p = _emp_where(scope, date_from=":pe", date_to=":pe")
    r = db.execute(
        text("SELECT count(*), coalesce(sum(days), 0) FROM (SELECT e.person_id, max(CAST(:pe AS date) - e.start_date) AS days "
             "FROM employment_relationships e WHERE e.start_date <= :pe AND (e.end_date IS NULL OR e.end_date >= :pe) "
             f"{where} GROUP BY e.person_id) t"),
        {**p, "pe": pe},
    ).one()
    n, days = int(r[0]), float(r[1])
    res = ratio(days / 365.25, n, scale=1.0, sample_size=n, min_sample=_min_group(), has_source_data=n > 0)
    res.quality_score = 100.0 if n else 0.0
    res.lineage = {"tables": ["employment_relationships"], "persons": n, "as_of": pe.isoformat(),
                   "method": "current spell; days / 365.25"}
    return res


def night_work_share(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-032: night hours / worked hours * 100 on approved timesheets."""
    return _night_from_hours(_hours(db, scope, ps, pe))


def _night_from_hours(h: dict) -> CalcResult:
    res = ratio(h["night"], h["worked"], sample_size=h["persons"], min_sample=_min_group(),
                has_source_data=h["rows"] > 0)
    res.quality_score = 100.0 if h["rows"] else 0.0
    if res.status == OK:
        res.breakdown = {"night_hours": round(h["night"], 1), "worked_hours": round(h["worked"], 1)}
    res.lineage = {"tables": ["timesheets"], "rows": h["rows"], "persons": h["persons"]}
    return res


def downtime_lost_hours_share(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-034: downtime lost hours / planned timesheet hours in the same scope * 100.
    Downtime events carry project and (optionally) legal entity but no occupation."""
    if scope.occupation_group:
        return CalcResult(INSUFFICIENT, quality_score=0.0, lineage={"tables": ["downtime_events"]},
                          note="Downtime events are not recorded per occupation; share not available for an "
                               "occupation filter")
    p: dict = {"tenant": scope.tenant_id, "ps": ps, "pe": pe}
    where = ""
    if scope.entity_ids is not None:
        # events without a legal entity cannot be attributed to an entity-filtered scope (counted in lineage)
        where += _ent_sql(scope, "legal_entity_id", p, row_project="project_id")
    if scope.project_id:
        where += " AND project_id = :project"
        p["project"] = scope.project_id
    rows = db.execute(
        text("SELECT cause, count(*), sum(lost_hours) FROM downtime_events "
             f"WHERE tenant_id = :tenant AND event_date BETWEEN :ps AND :pe {where} GROUP BY cause"), p).all()
    unattributed = 0
    if scope.entity_ids is not None:
        unattributed = int(db.execute(text(
            "SELECT count(*) FROM downtime_events WHERE tenant_id = :tenant AND event_date BETWEEN :ps AND :pe "
            "AND legal_entity_id IS NULL" + (" AND project_id = :project" if scope.project_id else "")), p).scalar())
    lost = sum(float(r[2]) for r in rows)
    by_cause = {r[0]: round(float(r[2]), 2) for r in rows}
    h = _hours(db, scope, ps, pe)
    res = ratio(lost, h["planned"], sample_size=h["persons"], min_sample=_min_group(), has_source_data=h["rows"] > 0)
    res.quality_score = 100.0 if h["rows"] else 0.0
    if res.status == OK:
        res.breakdown = {"lost_hours_by_cause": by_cause, "planned_hours": round(h["planned"], 1)}
    res.lineage = {"tables": ["downtime_events", "timesheets"], "timesheet_rows": h["rows"], "persons": h["persons"],
                   "downtime_events": sum(int(r[1]) for r in rows),
                   "downtime_events_excluded_no_entity": unattributed}
    if unattributed and res.status == OK:
        res.note = f"{unattributed} downtime event(s) have no legal entity and are excluded from an entity filter"
    return res


def overtime_cost_share(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-036 (sensitive): overtime cost / total labour cost * 100, both in reporting currency (same FX logic as
    KPI-019). A missing FX rate makes the result insufficient - never a partial total."""
    total, info = labor_cost_total(db, scope, ps, pe, params)
    if total is None:
        note = ("Missing FX rate(s): " + ", ".join(info["missing_fx"])) if info["missing_fx"] else "No cost data"
        return CalcResult(INSUFFICIENT, note=note, quality_score=0.0, lineage=info)
    overtime = info["by_cost_type"].get("overtime", 0.0)
    res = ratio(overtime, total, sample_size=info["persons"], min_sample=_min_group())
    if res.status == OK:
        res.breakdown = {"overtime_cost": round(overtime, 2), "total_labour_cost": round(total, 2),
                         "reporting_currency": info["reporting_currency"]}
    res.lineage = info
    res.quality_score = 100.0
    return res


def _case_variants(*words: str) -> list[str]:
    # matched in SQL against the trimmed raw value AND its upper(): upper() of non-ASCII letters depends on the DB
    # collation (e.g. Turkish dotless i, Cyrillic under the C locale), so every spelling variant is listed explicitly
    return sorted({v for w in words for v in (w, w.upper(), w.lower(), w.capitalize())})


_FEMALE = _case_variants("f", "female", "k", "kadın", "kadin", "w", "woman", "ж", "жен", "женский")
_MALE = _case_variants("m", "male", "e", "erkek", "man", "м", "муж", "мужской")


def female_share(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-048: female persons / persons with a recorded gender (active in scope at period end) * 100.
    Unknown/unrecorded gender is excluded from the denominator (counted in lineage). Because a tiny female or male
    count re-identifies people, the result is suppressed when EITHER count is below the publishable minimum."""
    where, p = _emp_where(scope, date_from=":pe", date_to=":pe")
    r = db.execute(
        text("SELECT count(*), count(*) FILTER (WHERE g = ANY(:fem) OR upper(g) = ANY(:fem)), "
             "count(*) FILTER (WHERE g = ANY(:male) OR upper(g) = ANY(:male)) FROM ("
             "SELECT DISTINCT e.person_id, trim(pp.gender) AS g FROM employment_relationships e "
             "JOIN people pp ON pp.id = e.person_id WHERE e.start_date <= :pe AND (e.end_date IS NULL OR "
             f"e.end_date >= :pe) {where}) t"),
        {**p, "pe": pe, "fem": _FEMALE, "male": _MALE},
    ).one()
    total, female, male = int(r[0]), int(r[1]), int(r[2])
    known = female + male
    lineage = {"tables": ["employment_relationships", "people"], "persons": total, "unknown_gender": total - known,
               "as_of": pe.isoformat()}
    min_n = _min_group()
    if total and (female < min_n or male < min_n) and known >= min_n:
        return CalcResult(SUPPRESSED, quality_score=None, lineage=lineage,
                          note=f"Female or male count below the minimum publishable size ({min_n})")
    res = ratio(female, known, sample_size=known, min_sample=min_n, has_source_data=total > 0)
    if res.status == OK:
        res.breakdown = {"female": female, "male": male}
    res.quality_score = round(100 * known / total, 1) if total else 0.0
    if res.status == OK and known < total:
        res.note = f"{total - known} person(s) without a recorded gender are excluded from the denominator"
    res.lineage = lineage
    return res


def subcontractor_share(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-050: persons employed by a subcontractor entity / all persons in scope (both at period end) * 100.
    A person with concurrent spells counts once in the denominator and once in the numerator if any spell is with a
    subcontractor."""
    where, p = _emp_where(scope, date_from=":pe", date_to=":pe")
    r = db.execute(
        text("SELECT count(DISTINCT e.person_id), count(DISTINCT e.person_id) FILTER (WHERE le.entity_type = "
             "'subcontractor') FROM employment_relationships e JOIN legal_entities le ON le.id = e.legal_entity_id "
             f"WHERE e.start_date <= :pe AND (e.end_date IS NULL OR e.end_date >= :pe) {where}"),
        {**p, "pe": pe},
    ).one()
    total, sub = int(r[0]), int(r[1])
    res = ratio(sub, total, sample_size=total, min_sample=_min_group(), has_source_data=total > 0)
    if res.status == OK:
        res.breakdown = {"by_employer_type": suppress_small_counts({"subcontractor": sub, "other": total - sub},
                                                                  total, _min_group())}
    res.quality_score = 100.0 if total else 0.0
    res.lineage = {"tables": ["employment_relationships", "legal_entities"], "persons": total, "as_of": pe.isoformat()}
    return res


CALCULATORS: dict[str, Callable[[Session, Scope, date, date, dict], CalcResult]] = {
    "turnover_rate": turnover_rate,
    "retention_rate": retention_rate,
    "absenteeism_rate": absenteeism_rate,
    "protected_leave_share": protected_leave_share,
    "annual_leave_usage": annual_leave_usage,
    "annual_leave_nonuse": annual_leave_nonuse,
    "capacity_utilization": capacity_utilization,
    "productivity_index": productivity_index,
    "plan_deviation": plan_deviation,
    "overtime_rate": overtime_rate,
    "training_hours_per_employee": training_hours_per_employee,
    "competency_coverage": competency_coverage,
    "unit_labor_cost": unit_labor_cost,
    "time_to_hire": time_to_hire,
    "rework_rate": rework_rate,
    "ltifr": ltifr,
    "trir": trir,
    "active_headcount": active_headcount,
    "new_hire_count": new_hire_count,
    "early_turnover_90": early_turnover_90,
    "labor_cost_per_employee": labor_cost_per_employee,
    "average_tenure": average_tenure,
    "night_work_share": night_work_share,
    "downtime_lost_hours_share": downtime_lost_hours_share,
    "overtime_cost_share": overtime_cost_share,
    "female_share": female_share,
    "subcontractor_share": subcontractor_share,
}


# B-13: calculators that can evaluate many periods in one SQL pass. Each MUST produce exactly what the per-period
# calculator in CALCULATORS returns for every window (tests/test_kpi_extended.py proves it); only the I/O is batched.
Windows = list[tuple[date, date]]


def _series_from_hours(build: Callable[[dict], CalcResult]):
    def run(db: Session, scope: Scope, windows: Windows, params: dict) -> dict[tuple[date, date], CalcResult]:
        return {w: build(h) for w, h in _hours_windows(db, scope, windows).items()}
    return run


def _headcount_windows(db: Session, scope: Scope, windows: Windows, params: dict) -> dict[tuple[date, date], CalcResult]:
    hc = headcount_series(db, scope, sorted({w[1] for w in windows}))
    return {w: _headcount_result(hc[w[1]], w[1]) for w in windows}


SERIES_CALCULATORS: dict[str, Callable[[Session, Scope, Windows, dict], dict[tuple[date, date], CalcResult]]] = {
    "active_headcount": _headcount_windows,
    "overtime_rate": _series_from_hours(_overtime_from_hours),
    "night_work_share": _series_from_hours(_night_from_hours),
}
