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
    LOW_EXPOSURE,
    OK,
    SUPPRESSED,
    CalcResult,
    month_end,
    month_starts,
    ratio,
    suppress_small_counts,
)
from app.analytics.patterns import Holidays, PatternResolver, day_weights, leave_days_in_window
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


def _separations(db: Session, scope: Scope, ps: date, pe: date, *, codes: list[str] | None = None) -> dict[str, int]:
    """Separations by termination category (internal transfers excluded). `codes` (ADR-023): count only spells whose
    stored SGK exit code is one of these (withheld / health codes are never stored, so they can never be counted)."""
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    where2, _ = _emp_where(scope.entities_only(), alias="e2", **_spell("e2"))
    if codes is not None:
        where += " AND e.termination_reason_code = ANY(:codes)"
        p["codes"] = list(codes)
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
    return _turnover_v2(db, scope, ps, pe, params, res, seps, n, avg)


def _turnover_v2(db: Session, scope: Scope, ps: date, pe: date, params: dict, res: CalcResult, seps: dict[str, int],
                 n: int, avg: float) -> CalcResult:
    """KPI-020 v2 (WP-5 3.5 + HR Director review; ADR-023). u = unknown / all separations:
    u <= bands[0] -> value + colour; bands[0] < u <= bands[1] -> value without colour (status_reason
    unknown_category_share, ADR-018) + breakdown.voluntary_range [counted, counted + unknown] / average headcount;
    u > bands[1] -> insufficient_data. Low-information note when (unknown + other) / all > low_information_share.
    voluntary_just_cause: counted separations with these stored SGK codes (23 / 25; never the health code 24)."""
    bands = params.get("unknown_share_bands")
    low_info = params.get("low_information_share")
    jc_codes = params.get("voluntary_just_cause_codes")
    all_seps = sum(seps.values())
    unknown = seps.get("unknown", 0)
    u = unknown / all_seps if all_seps else 0.0
    if jc_codes and res.status == OK:
        included = params.get("include_categories")
        jc = sum(v for k, v in _separations(db, scope, ps, pe, codes=jc_codes).items()
                 if included is None or k in included)
        res.breakdown.update(suppress_small_counts({"voluntary_just_cause": jc}, round(avg), _min_group()))
    if low_info is not None and all_seps:
        share = (unknown + seps.get("other", 0)) / all_seps
        res.lineage["low_information_share"] = round(share, 4)
        if share > low_info and res.status == OK:
            msg = (f"Low information share: {share * 100:.1f} % of separations are 'unknown' or 'other' (> "
                   f"{low_info * 100:g} %) - check the exit reason coding")
            res.note = f"{res.note}; {msg}" if res.note else msg
    if not bands or res.status != OK:
        return res
    lo, hi = bands
    res.lineage["unknown_share"] = round(u, 4)
    if u > hi:
        out = CalcResult(INSUFFICIENT, None, None, None, res.sample_size, res.quality_score, lineage=res.lineage,
                         note=f"{u * 100:.1f} % of separations have no category (> {hi * 100:g} %): the rate is not "
                         "shown - complete the exit reasons / SGK exit codes")
        return out
    if u > lo:
        days = (pe - ps).days + 1
        rng = [round(n / avg * 100, 4), round((n + unknown) / avg * 100, 4)]
        res.breakdown["voluntary_range"] = rng
        res.breakdown["voluntary_range_annualized"] = [round(v * 365 / days, 4) for v in rng]
        res.status_reason = "unknown_category_share"
        msg = (f"category data incomplete ({u * 100:.1f} % unknown): the true rate lies between {rng[0]:g} % and "
               f"{rng[1]:g} %")
        res.note = f"{res.note}; {msg}" if res.note else msg
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


# Health-derived leave types whose per-type hours are masked in breakdowns when fewer than HRSWI_MIN_GROUP_SIZE distinct
# people have such a record in scope x period (WP-1 3.6 / acceptance 9, KVKK md.6). parental is not masked here: KPI-054
# applies its own taker rule and the D-6 tests read excluded parental hours.
_MASKED_LEAVE_TYPES = ("work_injury",)


def absenteeism_rate(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-003/028/053: absence hours (types in params.absence_types) / planned hours * 100.
    params.excluded_types (ADR-019) declares the types deliberately left out (validated: absence_types +
    excluded_types = every leave type), so no type is excluded just because it is missing from a list; the declared
    list is echoed in the breakdown. work_injury hours are masked (null) when 1..min_group-1 people have such a record."""
    absence_types = params.get("absence_types", ["unexcused", "sick", "annual", "unpaid", "other"])
    declared_excluded = params.get("excluded_types")
    hours = _hours(db, scope, ps, pe)
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    cap = _capacity(db, scope, ps, pe, params, where, p)
    rows = db.execute(
        text(
            "SELECT l.leave_type, sum(l.hours * (LEAST(l.end_date, :pe) - GREATEST(l.start_date, :ps) + 1)::numeric "
            "/ (l.end_date - l.start_date + 1)), count(DISTINCT e.person_id) FROM leave_records l "
            "JOIN employment_relationships e ON e.id = l.employment_id WHERE l.tenant_id = :tenant "
            f"AND l.start_date <= :pe AND l.end_date >= :ps {where} GROUP BY 1"
        ),
        {**p, "ps": ps, "pe": pe},
    ).all()
    by_type = {r[0]: round(float(r[1]), 2) for r in rows}
    people = {r[0]: int(r[2]) for r in rows}
    absent = sum(v for k, v in by_type.items() if k in absence_types)
    res = ratio(absent, hours["planned"] + cap["addback_hours"], sample_size=hours["persons"], min_sample=_min_group(),
                has_source_data=hours["rows"] > 0)
    if res.status == OK:
        masked = sorted(t for t in _MASKED_LEAVE_TYPES if 0 < people.get(t, 0) < _min_group())
        shown = {k: (None if k in masked else v) for k, v in by_type.items()}
        res.breakdown = {
            "included_hours_by_type": {k: v for k, v in shown.items() if k in absence_types},
            "excluded_hours_by_type": {k: v for k, v in shown.items() if k not in absence_types},
            "included_types": absence_types,
        }
        if declared_excluded is not None:
            res.breakdown["excluded_types"] = declared_excluded
        if masked:
            res.breakdown["masked_types"] = masked
        if cap["basis"]:
            res.breakdown["denominator"] = _capacity_breakdown(hours, cap)
    res.quality_score = 100.0 if hours["rows"] else 0.0
    res.lineage = {"tables": ["leave_records", "timesheets"], "timesheet_rows": hours["rows"],
                   "leave_types_found": sorted(by_type), "proration": "calendar-day overlap"}
    _capacity_quality(res, hours, cap)
    return res


# WP-4 3.5 + HR Director review condition 2 / S-2 (ADR-024): denominator_basis = planned_capacity
_CAPACITY_NOTE = ("Planned hours on leave days could not be verified for {n} employee-day(s) (period timesheet rows, "
                  "no daily granularity): the rate may be overstated")


def _capacity(db: Session, scope: Scope, ps: date, pe: date, params: dict, where: str, p: dict) -> dict:
    """Leave hours added back to the planned-hours denominator (KPI-003/028/053/054 new version, WP-4 3.5).
    Only with params.denominator_basis = 'planned_capacity'. Per employee-day (review condition 2): the hours of a
    leave day (every leave type except public_holiday - capacity lost, also parental; D-6 numerators unchanged) are
    added only when no approved timesheet row with planned_hours > 0 exists for that employee and day. Days of a cell
    (legal entity x month) whose timesheet feed is declared planned_on_leave_days = 'included' are skipped (the source
    writes planned hours on leave days - scenario A). A day covered only by a period row (period_days > 1) cannot be
    checked: nothing is added and it is counted as unverified (quality 50 + note, S-2). Leave hours are spread over
    the working days of the record's work pattern (Mon-Fri without a pattern) weighted by public-holiday fractions."""
    out = {"basis": None, "addback_hours": 0.0, "addback_days": 0, "unverified_days": 0}
    if params.get("denominator_basis") != "planned_capacity":
        return out
    out["basis"] = "planned_capacity"
    proj = " OR c.project_id = :project" if scope.project_id else ""
    included = (f"SELECT 1 FROM data_feed_coverage c WHERE c.tenant_id = :tenant AND c.feed_type = 'timesheet' "
                f"AND c.legal_entity_id = e.legal_entity_id AND c.planned_on_leave_days = 'included' "
                f"AND (c.project_id IS NULL{proj})")
    rows = db.execute(text(
        "SELECT l.employment_id, e.legal_entity_id, l.start_date, l.end_date, l.hours, l.work_pattern_id "
        "FROM leave_records l JOIN employment_relationships e ON e.id = l.employment_id WHERE l.tenant_id = :tenant "
        f"AND l.start_date <= :pe AND l.end_date >= :ps AND l.leave_type <> 'public_holiday' {where} "
        "AND EXISTS (SELECT 1 FROM generate_series(GREATEST(l.start_date, :ps), LEAST(l.end_date, :pe), "
        f"interval '1 day') g(d) WHERE NOT EXISTS ({included} AND c.period_month = date_trunc('month', g.d)::date))"),
        {**p, "ps": ps, "pe": pe}).all()
    if not rows:
        return out
    emp_ids = {r[0] for r in rows}
    cells = {(r[0], r[1]) for r in db.execute(text(
        "SELECT c.legal_entity_id, c.period_month FROM data_feed_coverage c WHERE c.tenant_id = :tenant "
        "AND c.feed_type = 'timesheet' AND c.planned_on_leave_days = 'included' "
        f"AND (c.project_id IS NULL{' OR c.project_id = :project' if scope.project_id else ''}) "
        "AND c.period_month BETWEEN :m0 AND :pe"),
        {"tenant": scope.tenant_id, "m0": date(ps.year, ps.month, 1), "pe": pe,
         **({"project": scope.project_id} if scope.project_id else {})})}
    daily: set[tuple[int, date]] = set()
    periods: dict[int, list[tuple[date, date]]] = {}
    for emp, wd, n, _planned in db.execute(text(
            "SELECT t.employment_id, t.work_date, t.period_days, t.planned_hours FROM timesheets t "
            "WHERE t.tenant_id = :tenant AND t.status = 'approved' AND t.planned_hours > 0 "
            "AND t.employment_id = ANY(:emps) AND t.work_date BETWEEN :lo AND :pe"),
            {"tenant": scope.tenant_id, "emps": list(emp_ids), "lo": ps - timedelta(days=366), "pe": pe}):
        if (n or 1) > 1:
            periods.setdefault(emp, []).append((wd, wd + timedelta(days=n - 1)))
        else:
            daily.add((emp, wd))
    resolver, holidays = PatternResolver(db, scope.tenant_id, emp_ids), Holidays(db)
    for emp, ent, sd, ed, h, wp in rows:
        pat = resolver.by_id(wp) or resolver.resolve(emp, ent, sd)
        weights = day_weights(pat, holidays, sd, ed)
        total = sum(weights.values())
        if total <= 0:
            continue
        for d, w in weights.items():
            if d < ps or d > pe or (ent, date(d.year, d.month, 1)) in cells or (emp, d) in daily:
                continue
            if any(a <= d <= b for a, b in periods.get(emp, ())):
                out["unverified_days"] += 1
                continue
            out["addback_hours"] += float(h) * w / total
            out["addback_days"] += 1
    out["addback_hours"] = round(out["addback_hours"], 2)
    return out


def _capacity_breakdown(hours: dict, cap: dict) -> dict:
    return {"basis": cap["basis"], "planned_hours": round(hours["planned"], 2),
            "leave_addback_hours": cap["addback_hours"]}


def _capacity_quality(res: CalcResult, hours: dict, cap: dict) -> None:
    if not cap["basis"]:
        return
    res.lineage.update({"denominator_basis": cap["basis"], "leave_addback_days": cap["addback_days"],
                        "unverified_leave_days": cap["unverified_days"]})
    if cap["unverified_days"] and hours["rows"]:
        res.quality_score = min(res.quality_score or 0.0, 50.0)
        res.note = _CAPACITY_NOTE.format(n=cap["unverified_days"])


def protected_leave_share(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-054 (decision D-6): maternity / parental and related protected leave hours / planned hours x 100.
    Planning figure only - never part of absenteeism. Health-adjacent (KVKK md.6): suppressed unless at least
    HRSWI_MIN_GROUP_SIZE distinct people took such leave in the group, so no individual can be inferred."""
    types = params.get("absence_types", ["parental"])
    hours = _hours(db, scope, ps, pe)
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    cap = _capacity(db, scope, ps, pe, params, where, p)
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
    res = ratio(leave_h, hours["planned"] + cap["addback_hours"], sample_size=hours["persons"],
                min_sample=_min_group(), has_source_data=hours["rows"] > 0)
    if res.status == OK and cap["basis"]:
        res.breakdown["denominator"] = _capacity_breakdown(hours, cap)
    res.quality_score = 100.0 if hours["rows"] else 0.0
    res.lineage = {"tables": ["leave_records", "timesheets"], "included_types": types,
                   "proration": "calendar-day overlap"}
    _capacity_quality(res, hours, cap)
    return res


def _annual_leave_population(db: Session, scope: Scope, ps: date, pe: date, params: dict,
                             long_types: tuple[str, ...] = ()) -> list[dict]:
    """Population for KPI-055/056 (decision D-7, corrected per HR Director review):
    - person active in scope at period end; current employer = legal entity of the latest active employment;
    - service = ALL spells with that same employer up to period end (4857 md.54), not just the current contract;
    - one-year date approximated as period_end - (service_days - 365);
    - statutory entitlement (md.53) by service years and age, prorated from max(period start, one-year date);
    - long_absent: days of `long_types` (KPI-056 only) in the window > half of the window's working days.
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
             "AND l.end_date >= :ps AND l.leave_type = ANY(:types) GROUP BY 1, 2) "
             "SELECT svc.service_days, svc.birth, "
             "coalesce(sum(lv.h) FILTER (WHERE lv.leave_type = 'annual'), 0), "
             "coalesce(sum(lv.h) FILTER (WHERE lv.leave_type <> 'annual'), 0) "
             "FROM svc LEFT JOIN lv ON lv.person_id = svc.person_id WHERE svc.service_days >= 365 "
             "GROUP BY svc.person_id, svc.service_days, svc.birth"),
        {**p, "ps": ps, "pe": pe, "types": ["annual", *[t for t in long_types if t != "annual"]]},
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
    Can exceed 100 when carried-over leave is used (a good outcome). Versions with params.day_basis = 'leave_days'
    (ADR-024, WP-4 3.3-3.4): leave days by work pattern, contractual entitlement, md.54 spell union / leave seniority
    date, pattern coverage gate (min_pattern_coverage)."""
    if params.get("day_basis") == "leave_days":
        return _leave_days_result(db, scope, ps, pe, params, nonuse=False)
    pop = _annual_leave_population(db, scope, ps, pe, params)
    res = ratio(sum(x["taken"] for x in pop), sum(x["entitlement"] for x in pop), sample_size=len(pop),
                min_sample=_min_group(), has_source_data=bool(pop))
    res.quality_score = round(100 * sum(1 for x in pop if x["birth_known"]) / len(pop), 1) if pop else 0.0
    res.lineage = _annual_leave_lineage(pop, params)
    return res


# KPI-056 versions without the long_absence_types parameter keep their original list (historical semantics); the
# catalog's current version carries app.models.LONG_ABSENCE_TYPES explicitly (adds work_injury, ADR-019).
_LEGACY_LONG_ABSENCE = ("sick", "parental", "unpaid")


def annual_leave_nonuse(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-056 (decision D-7): eligible persons with no annual leave taken in the period / eligible persons x 100.
    Population: one-year date on/before period start, excluding persons on long absence of params.long_absence_types
    (sick/parental/unpaid/work_injury; reported only as a count) - an injured person is not "not taking leave"
    (WP-1 example 3). Early-warning indicator at team level - never used to rank individuals."""
    long_types = tuple(params.get("long_absence_types", _LEGACY_LONG_ABSENCE))
    if params.get("day_basis") == "leave_days":  # ADR-024 version (WP-4 3.4)
        return _leave_days_result(db, scope, ps, pe, params, nonuse=True, long_types=long_types)
    full = _annual_leave_population(db, scope, ps, pe, params, long_types)
    long_absent = sum(1 for x in full if x["one_year_date"] <= ps and x["long_absent"])
    pop = [x for x in full if x["one_year_date"] <= ps and not x["long_absent"]]
    res = ratio(sum(1 for x in pop if x["taken"] <= 0), len(pop), sample_size=len(pop), min_sample=_min_group(),
                has_source_data=bool(pop))
    res.quality_score = 100.0 if pop else 0.0
    res.lineage = _annual_leave_lineage(pop, params, excluded_long_absence=long_absent,
                                        long_absence_types=list(long_types))
    if ((pe - ps).days + 1) < 360:
        res.note = "Meaningful on 12-month windows; shorter periods overstate non-use"
    return res


# ---------------------------------------------------------------- KPI-055/056 leave-days version (ADR-024, WP-4 3.3-3.4)
_STATUTORY_RULE = ("4857 md.53: <=5y 14d, 5-15y 20d, >=15y 26d; age <=18 or >=50 min 20d; entitlement = max(statutory, "
                   "approved contractual / collective-agreement rules); prorated from max(period start, one-year date)")
_SENIORITY_MISMATCH_DAYS = 30  # WP-4 3.3: leave seniority date vs spell union


def _statutory_days(years: float, birth: int | None, pe: date) -> int:
    ent = 14 if years <= 5 else 20 if years < 15 else 26  # 4857 md.53 (5 years inclusive -> 14 days)
    if birth is not None and (pe.year - birth <= 18 or pe.year - birth >= 50):
        ent = max(ent, 20)
    return ent


def _rule_days(rule_type: str, value: dict, statutory: int, years: float) -> float | None:
    if rule_type == "extra_days":
        return statutory + float(value.get("days", 0))
    bands = sorted(value.get("bands") or [], key=lambda b: b["min_years"])
    fit = [b for b in bands if years >= b["min_years"]]
    return float(fit[-1]["days"]) if fit else None


def _union_days(spells: list[tuple[date, date]]) -> int:
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(spells):
        if cur_e is None or s > cur_e + timedelta(days=1):
            if cur_e is not None:
                total += (cur_e - cur_s).days + 1
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += (cur_e - cur_s).days + 1
    return total


def _leave_days_population(db: Session, scope: Scope, ps: date, pe: date, params: dict,
                           long_types: tuple[str, ...]) -> tuple[list[dict], dict]:
    """Persons active in scope at period end with their current employer (D-7), per WP-4 3.3:
    - service: pe - leave_seniority_date + 1 when the payroll supplies it, else the UNION of the spells with the
      current employer (overlaps once; md.54); > 30 days apart -> seniority_mismatch; no seniority date and the
      earliest loaded spell starts exactly on the employer's first employment-coverage month -> possibly truncated;
    - entitlement = max(statutory md.53, every approved rule valid at pe - employment / collective agreement /
      legal entity / project of the primary assignment); unapproved rules only produce a note (tunnel +4, AL-02);
    - annual-leave days (and long-absence days for KPI-056) from leave_records.days, else hours / pattern daily
      hours, else hours / params.hours_per_day (default_9h); window share by pattern working days."""
    hpd = float(params.get("hours_per_day", 9))
    w_end, p = _emp_where(scope, date_from=":pe", date_to=":pe")
    cur = db.execute(text(
        "SELECT DISTINCT ON (e.person_id) e.id, e.person_id, e.legal_entity_id, pp.birth_year, "
        "e.collective_agreement_code, e.leave_seniority_date FROM employment_relationships e "
        "JOIN people pp ON pp.id = e.person_id "
        f"WHERE e.start_date <= :pe AND (e.end_date IS NULL OR e.end_date >= :pe) {w_end} "
        "ORDER BY e.person_id, e.start_date DESC"), {**p, "pe": pe}).all()
    meta = {"day_basis": {}, "pattern_known": 0, "seniority_truncated": 0, "seniority_mismatch": 0,
            "contractual_rule_persons": 0, "pending_rule_persons": 0, "pending_reasons": set(),
            "entitlement_rule_missing": 0, "group_transfer_spells": 0, "advance_leave_persons": 0}
    if not cur:
        return [], meta
    persons = [r[1] for r in cur]
    spells: dict[tuple[int, int], list] = {}
    other: dict[int, list] = {}
    all_emp_ids = set()
    ent_of = {r[1]: r[2] for r in cur}
    for pid, eid, ent, sd, ed in db.execute(text(
            "SELECT x.person_id, x.id, x.legal_entity_id, x.start_date, x.end_date FROM employment_relationships x "
            "WHERE x.tenant_id = :tenant AND x.person_id = ANY(:persons) AND x.start_date <= :pe"),
            {"tenant": scope.tenant_id, "persons": persons, "pe": pe}):
        if ent == ent_of[pid]:
            spells.setdefault((pid, ent), []).append((sd, min(ed or pe, pe), eid))
            all_emp_ids.add(eid)
        else:
            other.setdefault(pid, []).append((sd, ed))
    leaves: dict[int, list] = {}
    for eid, lt, sd, ed, h, days, basis, wp in db.execute(text(
            "SELECT l.employment_id, l.leave_type, l.start_date, l.end_date, l.hours, l.days, l.day_basis, "
            "l.work_pattern_id FROM leave_records l WHERE l.tenant_id = :tenant AND l.employment_id = ANY(:emps) "
            "AND l.start_date <= :pe AND l.end_date >= :ps AND l.leave_type = ANY(:types)"),
            {"tenant": scope.tenant_id, "emps": list(all_emp_ids), "ps": ps, "pe": pe,
             "types": ["annual", *[t for t in long_types if t != "annual"]]}):
        leaves.setdefault(eid, []).append((lt, sd, ed, float(h), None if days is None else float(days), basis, wp))
    first_cov = dict(db.execute(text(
        "SELECT legal_entity_id, min(period_month) FROM data_feed_coverage WHERE tenant_id = :tenant "
        "AND feed_type = 'employment' AND project_id IS NULL AND completeness IN ('complete', 'partial') "
        "GROUP BY 1"), {"tenant": scope.tenant_id}).all())
    rules = db.execute(text(
        "SELECT scope_level, employment_id, collective_agreement_code, legal_entity_id, project_id, rule_type, value, "
        "approved_at IS NOT NULL AND length(source_document) > 0, pending_reason FROM leave_entitlement_rules "
        "WHERE tenant_id = :tenant AND valid_from <= :pe AND (valid_to IS NULL OR valid_to > :pe)"),
        {"tenant": scope.tenant_id, "pe": pe}).all()
    projects: dict[int, int] = {}
    if any(r[0] == "project" for r in rules):
        for eid, prj, _prim in db.execute(text(
                "SELECT a.employment_id, a.project_id, a.is_primary FROM assignments a WHERE a.tenant_id = :tenant "
                "AND a.employment_id = ANY(:emps) AND a.project_id IS NOT NULL AND a.valid_from <= :pe "
                "AND (a.valid_to IS NULL OR a.valid_to > :pe) ORDER BY a.is_primary DESC, a.valid_from DESC"),
                {"tenant": scope.tenant_id, "emps": [r[0] for r in cur], "pe": pe}):
            projects.setdefault(eid, prj)
    resolver, holidays = PatternResolver(db, scope.tenant_id, all_emp_ids), Holidays(db)
    window_days = (pe - ps).days + 1
    out = []
    for cur_id, pid, ent, birth, ta_code, lsd in cur:
        own = spells.get((pid, ent), [])
        union = _union_days([(s, e) for s, e, _ in own])
        service = (pe - lsd).days + 1 if lsd else union
        if lsd and abs(service - union) > _SENIORITY_MISMATCH_DAYS:
            meta["seniority_mismatch"] += 1
        earliest = min(s for s, _, _ in own)
        truncated = lsd is None and first_cov.get(ent) is not None and earliest == first_cov[ent]
        pat = resolver.resolve(cur_id, ent, pe)
        taken = absent = 0.0
        for _s, _e, eid in own:
            for lt, sd, ed, h, days, basis, wp in leaves.get(eid, ()):
                lp = resolver.by_id(wp) or resolver.resolve(eid, ent, sd)
                d, derived = leave_days_in_window(days=days, hours=h, sd=sd, ed=ed, ps=ps, pe=pe, pattern=lp,
                                                  holidays=holidays, fallback_hpd=hpd)
                b = basis or derived or "source_days"
                if service >= 365:
                    meta["day_basis"][b] = meta["day_basis"].get(b, 0) + 1
                if lt == "annual":
                    taken += d
                else:
                    absent += d
        if service < 365:
            if taken > 0:
                meta["advance_leave_persons"] += 1  # S-3: lineage count only, never a KPI
            continue
        years = service / 365.25
        statutory = _statutory_days(years, birth, pe)
        ent_days, pending, ta_rule = float(statutory), [], False
        for lvl, r_emp, r_code, r_ent, r_prj, rtype, value, active, reason in rules:
            applies = ((lvl == "employment" and r_emp == cur_id) or (lvl == "legal_entity" and r_ent == ent)
                       or (lvl == "collective_agreement" and ta_code is not None and r_code == ta_code)
                       or (lvl == "project" and r_prj is not None and projects.get(cur_id) == r_prj))
            if not applies:
                continue
            ta_rule |= lvl == "collective_agreement"
            if not active:
                pending.append(reason or "unapproved")
                continue
            days_rule = _rule_days(rtype, value or {}, statutory, years)
            if days_rule is not None:
                ent_days = max(ent_days, days_rule)  # never below the statutory floor (L-4)
        if ent_days > statutory:
            meta["contractual_rule_persons"] += 1
        if pending:
            meta["pending_rule_persons"] += 1
            meta["pending_reasons"] |= set(pending)
        if ta_code is not None and not ta_rule:
            meta["entitlement_rule_missing"] += 1
        if any(e2 is not None and s0 - timedelta(days=TRANSFER_GAP_DAYS) <= e2 < s0
               for s0, _, _ in own for _s2, e2 in other.get(pid, ())):
            meta["group_transfer_spells"] += 1  # HR Director review D (L-5): count only, never merged
        if pat is not None:
            meta["pattern_known"] += 1
        meta["seniority_truncated"] += int(truncated)
        one_year = pe - timedelta(days=service - 365)
        start = max(ps, one_year)
        dpw = pat.days_per_week if pat else 5
        out.append({"entitlement": ent_days * ((pe - start).days + 1) / 365, "taken": taken,
                    "one_year_date": one_year, "long_absent": absent > window_days * dpw / 7 / 2,
                    "birth_known": birth is not None, "pattern_known": pat is not None, "truncated": truncated})
    return out, meta


def _leave_days_result(db: Session, scope: Scope, ps: date, pe: date, params: dict, *, nonuse: bool,
                       long_types: tuple[str, ...] = ()) -> CalcResult:
    """KPI-055 / KPI-056 leave-days version. Pattern coverage = share of the eligible population whose work pattern
    is known at period end; below params.min_pattern_coverage -> insufficient_data, quality = coverage x 100
    (HR Director S-1: 0.8, no transition value). Otherwise unknown-pattern persons use the hours_per_day fallback and
    quality = min(birth year known, pattern coverage, not truncated) x 100."""
    full, meta = _leave_days_population(db, scope, ps, pe, params, long_types)
    if nonuse:
        base = [x for x in full if x["one_year_date"] <= ps]
        long_absent = sum(1 for x in base if x["long_absent"])
        pop = [x for x in base if not x["long_absent"]]
    else:
        base = pop = full
    coverage = sum(1 for x in base if x["pattern_known"]) / len(base) if base else 0.0
    lineage = {"tables": ["employment_relationships", "people", "leave_records", "work_patterns",
                          "leave_entitlement_rules"],
               "population": "active at period end, >= 1 year of service with the current employer (leave seniority "
                             "date, else union of all spells - 4857 md.54)",
               "entitlement_rule": _STATUTORY_RULE, "day_basis": "leave_days",
               "hours_per_day_fallback": params.get("hours_per_day", 9), "eligible": len(pop),
               "pattern_coverage": round(coverage, 4), "day_basis_counts": dict(sorted(meta["day_basis"].items())),
               "contractual_rule_count": meta["contractual_rule_persons"],
               "seniority_truncated": sum(1 for x in base if x["truncated"]),
               "seniority_mismatch": meta["seniority_mismatch"], "group_transfer_spells": meta["group_transfer_spells"],
               "advance_leave_persons": meta["advance_leave_persons"],
               "entitlement_rule_missing": meta["entitlement_rule_missing"],
               "pending_entitlement_rules": meta["pending_rule_persons"]}
    if nonuse:
        lineage.update({"excluded_long_absence": long_absent, "long_absence_types": list(long_types)})
    min_cov = float(params.get("min_pattern_coverage", 0.8))
    unknown = sum(1 for x in base if not x["pattern_known"])
    if len(base) >= _min_group() and coverage < min_cov:
        return CalcResult(INSUFFICIENT, sample_size=len(base), quality_score=round(coverage * 100, 1),
                          lineage=lineage, note=f"Work pattern unknown for {unknown} of {len(base)} eligible person(s) "
                                                f"(pattern coverage {coverage:.0%} < {min_cov:.0%})")
    if nonuse:
        res = ratio(sum(1 for x in pop if x["taken"] <= 0), len(pop), sample_size=len(pop), min_sample=_min_group(),
                    has_source_data=bool(pop))
    else:
        res = ratio(sum(x["taken"] for x in pop), sum(x["entitlement"] for x in pop), sample_size=len(pop),
                    min_sample=_min_group(), has_source_data=bool(pop))
    if base:
        shares = [sum(1 for x in base if x["birth_known"]) / len(base), coverage,
                  sum(1 for x in base if not x["truncated"]) / len(base)]
        res.quality_score = round(100 * min(shares), 1) if not nonuse else round(100 * min(shares[1:]), 1)
    else:
        res.quality_score = 0.0
    res.lineage = lineage
    notes = []
    if unknown and res.status == OK:
        notes.append(f"Work pattern unknown for {unknown} person(s): their leave days use "
                     f"{params.get('hours_per_day', 9)} h per day")
    if meta["pending_rule_persons"] and res.status == OK:
        notes.append(f"{meta['pending_rule_persons']} person(s) are covered by entitlement rule(s) awaiting approval / "
                     f"legal confirmation ({', '.join(sorted(meta['pending_reasons']))}; e.g. underground +4 days): "
                     "utilisation may be overstated" if not nonuse else
                     f"{meta['pending_rule_persons']} person(s) are covered by entitlement rule(s) awaiting approval")
    if nonuse and ((pe - ps).days + 1) < 360:
        notes.append("Meaningful on 12-month windows; shorter periods overstate non-use")
    if notes:
        res.note = "; ".join(notes)
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


def _safety_where(scope: Scope, p: dict) -> str:
    """Entity/project filter for safety_events (events are not recorded per occupation)."""
    where = ""
    where += _ent_sql(scope, "legal_entity_id", p, row_project="project_id")
    if scope.project_id:
        where += " AND project_id = :project"
        p["project"] = scope.project_id
    return where


# ADR-021 / WP-6 HSE 3.7: cases of these categories enter a rate only when their employer contributes hours worked
# to the denominator in that month; third-party cases never do (no hours in the denominator).
_HOURS_DEPENDENT_CATEGORIES = ("subcontractor_worker", "temporary_agency_worker")


def _entity_months_with_hours(db: Session, scope: Scope, ps: date, pe: date) -> set[tuple[int, date]]:
    """(legal entity, month) cells that have approved hours worked > 0 inside the scope (same filters as _hours)."""
    where, p = _emp_where(replace(scope, project_id=None), date_from=":ps", date_to=":pe", row_project="t.project_id")
    proj = ""
    if scope.project_id:
        proj = " AND t.project_id = :project"
        p["project"] = scope.project_id
    rows = db.execute(text(
        "SELECT e.legal_entity_id, CAST(date_trunc('month', t.work_date) AS date) FROM timesheets t "
        "JOIN employment_relationships e ON e.id = t.employment_id WHERE t.tenant_id = :tenant "
        f"AND t.status = 'approved' AND t.work_date BETWEEN :ps AND :pe {where}{proj} "
        "GROUP BY 1, 2 HAVING sum(t.worked_hours) > 0"), {**p, "ps": ps, "pe": pe}).all()
    return {(r[0], r[1]) for r in rows}


def _safety_rate(db: Session, scope: Scope, ps: date, pe: date, types: list[str], multiplier: float,
                 min_exposure_hours: float | None = None, exclude_out_of_rate: bool = False) -> CalcResult:
    """WP-2 R1: the rate is always computed; when the hours worked in the window are below `min_exposure_hours`
    the result carries status_reason = low_exposure and the engine gives it no colour (equality = coloured).
    `exclude_out_of_rate` (ADR-021, WP-6 HSE 3.7): numerator and denominator from the same population - third-party
    cases and subcontractor / agency cases whose employer has no hours worked in the scope that month are left out of
    the rate and reported as out-of-rate case counts (they still count in the fatality alert). One row = one case."""
    if scope.occupation_group:
        return CalcResult(INSUFFICIENT, quality_score=0.0, lineage={"tables": ["safety_events"]},
                          note="Safety events are not recorded per occupation; rate not available for an occupation filter")
    p: dict = {"tenant": scope.tenant_id, "ps": ps, "pe": pe, "types": types}
    where = _safety_where(scope, p)
    cases = db.execute(
        text("SELECT event_type, worker_category, legal_entity_id, CAST(date_trunc('month', event_date) AS date), "
             "count(*) FROM safety_events WHERE tenant_id = :tenant "
             f"AND event_date BETWEEN :ps AND :pe AND event_type = ANY(:types) {where} GROUP BY 1, 2, 3, 4"), p).all()
    rows: dict[str, int] = {}
    out_of_rate = {"third_party": 0, "no_hours_in_denominator": 0}
    with_hours = None
    for et, cat, eid, month, n in cases:
        if exclude_out_of_rate and cat == "third_party":
            out_of_rate["third_party"] += n
            continue
        if exclude_out_of_rate and cat in _HOURS_DEPENDENT_CATEGORIES:
            if with_hours is None:
                with_hours = _entity_months_with_hours(db, scope, ps, pe)
            if (eid, month) not in with_hours:
                out_of_rate["no_hours_in_denominator"] += n
                continue
        rows[et] = rows.get(et, 0) + int(n)
    n = sum(rows.values())
    h = _hours(db, scope, ps, pe)
    res = ratio(n, h["worked"], scale=multiplier, has_source_data=h["rows"] > 0, sample_size=h["persons"],
                min_sample=_min_group())
    res.breakdown = {"events_by_type": rows, "hours_worked": round(h["worked"], 1), "multiplier": multiplier}
    res.quality_score = 100.0 if h["rows"] else 0.0
    res.lineage = {"tables": ["safety_events", "timesheets"], "event_types_counted": types}
    excluded = sum(out_of_rate.values())
    if exclude_out_of_rate and excluded:
        res.breakdown["out_of_rate_cases"] = out_of_rate
        res.note = (f"Out-of-rate cases: {excluded} (third party {out_of_rate['third_party']}, employer without hours "
                    f"worked in the denominator {out_of_rate['no_hours_in_denominator']}) - not in the rate, still in "
                    "the fatality alert")
    if res.status == OK and min_exposure_hours is not None and h["worked"] < min_exposure_hours:
        res.status_reason = LOW_EXPOSURE
        low = (f"Low exposure: {h['worked']:,.0f} h worked < {min_exposure_hours:,.0f} h minimum - value shown "
               f"with event counts, no colour")
        res.note = f"{low}; {res.note}" if res.note else low
    return res


def ltifr(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-014A LTIFR: lost-time injuries (incl. fatalities) per 1,000,000 hours worked."""
    return _safety_rate(db, scope, ps, pe, params.get("event_types", ["lost_time_injury", "fatality"]),
                        params.get("multiplier", 1_000_000), params.get("threshold_min_exposure_hours"),
                        params.get("exclude_out_of_rate_cases", False))


def trir(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-014B TRIR: recordable injuries per 200,000 hours worked (OSHA convention)."""
    return _safety_rate(db, scope, ps, pe,
                        params.get("event_types", ["fatality", "lost_time_injury", "restricted_work", "medical_treatment"]),
                        params.get("multiplier", 200_000), params.get("threshold_min_exposure_hours"),
                        params.get("exclude_out_of_rate_cases", False))


def near_miss_rate(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-043 (WP-2 R3): near-miss reports per 200,000 hours worked - same denominator as KPI-014A/B, direction
    neutral, no thresholds. Below the exposure minimum the value carries status_reason low_exposure ('low exposure'
    label; there is no colour anyway). Near misses have no worker category, so nothing is out of rate."""
    return _safety_rate(db, scope, ps, pe, ["near_miss"], params.get("multiplier", 200_000),
                        params.get("threshold_min_exposure_hours"))


def fatality_alerts(db: Session, scope: Scope, ps: date, pe: date) -> list[dict]:
    """WP-2 R2 (ADR-018, ADR-021): any fatality in scope x window surfaces an alert, independent of the rate - of
    its exposure rule, small-group suppression, the occupation filter (events are not per occupation, so the filter
    is ignored here), the coverage gate and the formula's event_types. Counts ALL fatality records in scope, every
    worker category included (third party, subcontractor without hours). Group-level only: count, month and - when
    the cases carry one - the worker category; never a person, description or day-level date. Not subject to
    minimum group size (HR Director WP-2 Q7). A fatality later reclassified to another class leaves a
    'fatality_reclassified' alert in every window that contains it (WP-2 R2.5, example L). Scope = the reader's
    authorised scope (subcontractor fatalities without a data-sharing grant are NOT shown - PO-3 open)."""
    p: dict = {"tenant": scope.tenant_id, "ps": ps, "pe": pe}
    where = _safety_where(scope, p)
    rows = db.execute(
        text("SELECT to_char(event_date, 'YYYY-MM'), worker_category, event_type, count(*) FROM safety_events "
             "WHERE tenant_id = :tenant AND event_date BETWEEN :ps AND :pe "
             f"AND (event_type = 'fatality' OR reclassified_from_fatality) {where} GROUP BY 1, 2, 3 ORDER BY 1"),
        p).all()
    out = []
    for kind, fatal in (("fatality", True), ("fatality_reclassified", False)):
        sel = [r for r in rows if (r[2] == "fatality") == fatal]
        n = sum(int(r[3]) for r in sel)
        if not n:
            continue
        by_month: dict[str, int] = {}
        for r in sel:
            by_month[r[0]] = by_month.get(r[0], 0) + int(r[3])
        alert = {"type": kind, "count": n, "by_month": by_month}
        if fatal and any(r[1] for r in sel):  # legacy rows have no category: the breakdown is added only with data
            cats: dict[str, int] = {}
            for r in sel:
                cats[r[1] or "unknown"] = cats.get(r[1] or "unknown", 0) + int(r[3])
            alert["by_worker_category"] = dict(sorted(cats.items()))
        out.append(alert)
    return out


# Calculators whose results carry rate-independent alerts (engine.evaluate; never frozen, never suppressed).
ALERTS: dict[str, Callable[[Session, Scope, date, date], list[dict]]] = {"ltifr": fatality_alerts,
                                                                          "trir": fatality_alerts}


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


_PLANNED_END_CATS = "('contract_end', 'project_end')"


def _early_cohort(db: Session, scope: Scope, ps: date, pe: date, days: int, mode: str, basis: str) -> dict:
    """Cohort counts for KPI-018/021/022. Transfers in are not hires, transfers out are not early leavers (same rule
    as KPI-001/017, ADR-017 helpers). mode (WP-5 3.4): none = v1; category = contract/project end excluded;
    verified = planned_end_verified IS TRUE excluded (NULL is never excluded). basis (PO-4 switch): what 'same
    employer' means for repeated planned ends - legal_entity (safe default) or group (any entity in scope)."""
    where, p = _emp_where(scope, date_from=":ps", date_to=":pe")
    ents = scope.entities_only()
    w_in, _ = _emp_where(ents, alias="e2", **_spell("e2"))
    w_out, _ = _emp_where(ents, alias="e3", **_spell("e3"))
    excl = {"none": "false", "category": f"e.termination_category IN {_PLANNED_END_CATS}",
            "verified": f"e.planned_end_verified IS TRUE AND e.termination_category IN {_PLANNED_END_CATS}"}[mode]
    excl4 = excl.replace("e.", "e4.")
    if basis == "group":
        w4, _ = _emp_where(ents, alias="e4", **_spell("e4"))
    else:
        w4 = " AND e4.tenant_id = :tenant AND e4.legal_entity_id = c.legal_entity_id"
    r = db.execute(
        text("WITH c AS (SELECT e.id, e.person_id, e.legal_entity_id, e.end_date, "
             "coalesce(e.termination_category, 'unknown') AS tc, e.planned_end_verified AS pev, "
             f"({excl}) AS excl, "
             "(e.end_date IS NOT NULL AND e.end_date < e.start_date + :days "
             "AND NOT EXISTS (SELECT 1 FROM employment_relationships e3 WHERE e3.person_id = e.person_id "
             f"AND e3.id <> e.id AND e3.start_date > e.end_date AND e3.start_date <= e.end_date + :gap {w_out})) "
             f"AS early FROM employment_relationships e WHERE e.start_date BETWEEN :ps AND :pe {where} "
             "AND NOT EXISTS (SELECT 1 FROM employment_relationships e2 WHERE e2.person_id = e.person_id "
             f"AND e2.id <> e.id AND e2.end_date < e.start_date AND e2.end_date >= e.start_date - :gap {w_in})) "
             "SELECT count(*), count(*) FILTER (WHERE early), count(*) FILTER (WHERE early AND excl), "
             f"count(*) FILTER (WHERE early AND NOT excl AND tc IN {_PLANNED_END_CATS} AND pev IS NULL), "
             f"count(*) FILTER (WHERE early AND (tc = 'unknown' OR (tc IN {_PLANNED_END_CATS} AND pev IS NULL))), "
             "count(DISTINCT c.person_id) FILTER (WHERE early AND excl AND EXISTS (SELECT 1 FROM "
             "employment_relationships e4 WHERE e4.person_id = c.person_id AND e4.id <> c.id "
             f"AND e4.end_date < c.end_date AND e4.end_date >= c.end_date - 365 AND {excl4} {w4})) FROM c"),
        {**p, "ps": ps, "pe": pe, "days": days, "gap": TRANSFER_GAP_DAYS},
    ).one()
    return {"hires": int(r[0]), "early_all": int(r[1]), "excluded": int(r[2]), "unverified_counted": int(r[3]),
            "ambiguous": int(r[4]), "repeated": int(r[5]), "early": int(r[1]) - int(r[2])}


def early_turnover_90(db: Session, scope: Scope, ps: date, pe: date, params: dict) -> CalcResult:
    """KPI-018/021/022: share of hires (start in period) who left within `days` days of starting.
    v2 (WP-5 3.4, ADR-023): `planned_end_exclusion` removes verified planned contract/project ends from the numerator
    (denominator unchanged), reports the excluded share, repeated planned ends and a coding-shift alarm."""
    days = params.get("days", 90)
    mode = params.get("planned_end_exclusion", "none")
    basis = params.get("same_employer_basis", "legal_entity")
    alarm_points = params.get("planned_end_alarm_points")
    c = _early_cohort(db, scope, ps, pe, days, mode, basis)
    hires, early = c["hires"], c["early"]
    res = ratio(early, hires, sample_size=hires, min_sample=_min_group(), has_source_data=True)
    observed_until = date.today()
    res.quality_score = 100.0 if pe + timedelta(days=days) <= observed_until else 50.0
    if pe + timedelta(days=days) > observed_until:
        res.note = f"Cohort not yet fully observed ({days} days after period end); value may still rise"
    res.lineage = {"tables": ["employment_relationships"], "hires": hires, "early_leavers": early, "window_days": days}
    if mode == "none":
        return res
    # classification quality: early leavers whose planned-end status could not be established (WP-5 3.4)
    if c["early_all"]:
        res.quality_score = min(res.quality_score, round(100 * (1 - c["ambiguous"] / c["early_all"]), 1))
    res.lineage.update({"planned_end_exclusion": mode, "same_employer_basis": basis,
                        "early_leavers_before_exclusion": c["early_all"], "planned_end_excluded": c["excluded"],
                        "planned_end_unverified_counted": c["unverified_counted"],
                        "repeated_short_fixed_term_persons": c["repeated"]})
    if res.status != OK:
        return res
    share = round(c["excluded"] / hires * 100, 4) if hires else 0.0
    res.breakdown["planned_end_excluded_share"] = share
    notes = [res.note] if res.note else []
    if c["excluded"]:
        notes.append(f"{c['excluded']} verified planned contract/project end(s) excluded ({share:g} % of hires)")
    if c["repeated"]:  # HR Director S-2: visible as a number until legal answer L-11
        notes.append(f"{c['repeated']} person(s) with repeated planned ends within 12 months (same employer)")
    if alarm_points:
        span = pe - ps + timedelta(days=1)
        hist = [_early_cohort(db, scope, ps - span * k, pe - span * k, days, mode, basis) for k in (2, 1)]
        if all(h["hires"] for h in hist):
            first = hist[0]
            v_first, s_first = first["early"] / first["hires"] * 100, first["excluded"] / first["hires"] * 100
            if res.value < v_first and share - s_first >= alarm_points:
                res.lineage["planned_end_alarm"] = True
                notes.append(f"early turnover fell while the planned-end share rose by {share - s_first:.1f} points "
                             "over 3 periods - check the exit coding (data quality)")
    res.note = "; ".join(notes) or None
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
    "near_miss_rate": near_miss_rate,
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
