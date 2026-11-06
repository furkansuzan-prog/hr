"""Timesheet import, entity_type = "timesheet" (wave-2 item 12, ADR-022).

Source: docs/hr/data/WP-6_import_field_dictionaries.md 3.1-3.2 + HR Director review (conditions 1-6); the WP-4
review (condition 2: `period_days > 1` = no daily granularity) for the planned-on-leave observation. Review conditions
override the spec text where they differ. Work patterns (WP-4, item 15) are NOT implemented: the weekly overtime check
Work patterns (ADR-024, item 15): the weekly overtime check uses the employee's pattern weekly hours (45 h where no
pattern is known), the planned-on-leave observation counts the pattern's working days (Mon-Fri without a pattern) minus
full public holidays, and `pattern_timesheet_mismatch` compares planned days with the pattern (WP-4 section 8).

- one row = one employee x day x project (`period_days > 1` = a period total starting at work_date, flagged
  `non_daily_granularity`); natural key (employment, work_date, project) = uq_timesheet_day (NULLS NOT DISTINCT);
- employment resolution as the leave import (identity map -> employer's employee number, on work_date);
- project attribution (ADR-017): `project_code` when given; blank -> the employment's assignment project on that day
  when there is exactly one (warning `project_derived_from_assignment`), otherwise NULL + `project_code_missing`
  (a row without a project is invisible to project-limited viewers and in project views);
- blank numeric = NULL, except overtime / night hours (blank -> 0, the dictionary says so); no default status: an
  unmapped status column needs the batch option approved_source = true, otherwise rows are stored as `draft` (never
  counted) + `approval_unknown`;
- re-import: same key + same values -> `already_imported` (skipped); same key + other values -> skipped with
  `already_imported_changed` unless the batch was uploaded with mode = replace, then corrected in place
  (record_version + 1, audit_logs `timesheet.correct`, warning `timesheet_corrected`). Closed months are rejected
  (`period_closed`, D-2), so a correction only ever touches open periods. Rows missing from a later file are never
  deleted;
- cross-row checks are group-level counts only (no person list): `overtime_weekly_inconsistent`,
  `leave_and_work_same_day`, `planned_on_leave_mismatch`, `pattern_timesheet_mismatch`;
- KVKK md.4: name / national-id / health columns are dropped on upload before anything is stored (names of the
  columns only); a `day_type = leave` row never creates a leave record (D-6).
The timesheet_monthly summary is kept exact by the DB triggers of migration 0005 (insert/update of timesheets marks
the month dirty); commit calls mark_stale_from via imports._invalidate_from_committed.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.patterns import DEFAULT_WEEKDAYS, Holidays, PatternResolver
from app.models import (
    TIMESHEET_CORRECT_ACTION,
    Assignment,
    AuditLog,
    DataQualityIssue,
    EmploymentRelationship,
    ImportBatch,
    ImportStagingRow,
    LeaveRecord,
    LegalEntity,
    PeriodClose,
    PersonIdentifier,
    Project,
    Shift,
    Timesheet,
)
from app.security.access import AccessContext

TIMESHEET_FIELDS = {
    "external_id": {"required": True, "aliases": ["sicil", "sicil no", "personel no", "employee id", "employee number",
                                                   "табельный номер"]},
    "source_system": {"required": False, "aliases": ["kaynak", "source", "система"]},
    "legal_entity_code": {"required": True, "aliases": ["firma", "firma kodu", "şirket", "company", "employer",
                                                        "компания"]},
    "work_date": {"required": True, "aliases": ["tarih", "çalışma tarihi", "gün", "date", "work date", "дата"]},
    "project_code": {"required": False, "aliases": ["proje", "proje kodu", "şantiye", "project", "site", "проект"]},
    "shift_code": {"required": False, "aliases": ["vardiya", "shift", "смена"]},
    "planned_hours": {"required": True, "aliases": ["planlı saat", "normal saat", "planned hours", "плановые часы"]},
    "worked_hours": {"required": True, "aliases": ["çalışılan saat", "fiili saat", "worked hours", "отработано"]},
    "overtime_hours": {"required": False, "aliases": ["fazla mesai", "fazla çalışma", "overtime", "сверхурочные"]},
    "night_hours": {"required": False, "aliases": ["gece saati", "night hours", "ночные часы"]},
    "productive_hours": {"required": False, "aliases": ["üretken saat", "iş paketi saati", "productive hours",
                                                        "производительные часы"]},
    "day_type": {"required": False, "aliases": ["gün tipi", "day type", "тип дня"]},
    "status": {"required": False, "aliases": ["onay", "durum", "status", "статус"]},
    "period_days": {"required": False, "aliases": ["dönem gün", "period days"]},
}
MODES = ("skip", "replace")
WEEKLY_HOURS = 45  # 4857 md. 63 - fallback when no work pattern is known (ADR-024; L-15: weekly basis, warning only)
PATTERN_MISMATCH_SHARE = 0.5  # WP-4 section 8: >= 50 % of an employee's complete weeks off the pattern
DAILY_MAX = 11  # md. 63 (L-16: warning, the row is loaded)
NIGHT_MAX = 7.5  # md. 69 (L-16: warning)
CERTIFIED_LEAVE = ("annual", "sick", "work_injury")  # WP-6 3.2.2 "yıllık/raporlu izin" (E.1 paper-leave check)

# KVKK md.4 / md.6: a timesheet needs the employee number, never the name, national id or health detail
_PROHIBITED_SUBSTRINGS = ("ad soyad", "adi soyadi", "first name", "last name", "surname", "tckn", "tc kimlik",
                          "kimlik no", "national id", "passport", "pasaport", "фамилия", "паспорт", "tani", "teshis",
                          "diagnos", "icd", "hastalik adi", "диагноз", "medical detail")
_PROHIBITED_WORDS = ("ad", "adi", "soyad", "soyadi", "isim", "name", "tc", "имя")

_DAY_TYPES = {
    "work": "work", "workday": "work", "work day": "work", "working day": "work", "is gunu": "work",
    "calisma": "work", "calisma gunu": "work", "normal": "work", "рабочий": "work", "рабочий день": "work",
    "weekly rest": "weekly_rest", "rest day": "weekly_rest", "weekend": "weekly_rest", "hafta tatili": "weekly_rest",
    "ht": "weekly_rest", "выходной": "weekly_rest", "выходной день": "weekly_rest",
    "public holiday": "public_holiday", "holiday": "public_holiday", "resmi tatil": "public_holiday",
    "genel tatil": "public_holiday", "bayram": "public_holiday", "ulusal bayram": "public_holiday",
    "праздник": "public_holiday", "праздничный день": "public_holiday",
    "leave": "leave", "izin": "leave", "izinli": "leave", "отпуск": "leave",
    "unknown": "unknown", "bilinmiyor": "unknown", "неизвестно": "unknown",
}
_STATUSES = {"approved": "approved", "onayli": "approved", "onaylandi": "approved", "onay": "approved",
             "утвержден": "approved", "утверждено": "approved", "draft": "draft", "taslak": "draft",
             "onay bekliyor": "draft", "pending": "draft", "черновик": "draft"}
# stored value of every comparable field (key fields employment / work_date / project are not compared)
_COMPARED = ("shift_id", "planned_hours", "worked_hours", "overtime_hours", "night_hours", "productive_hours",
             "day_type", "status", "period_days")


def _fold(s: str) -> str:
    from app.services.imports import _norm

    return _norm(s)


def prohibited_columns(headers: list[str]) -> list[str]:
    out = []
    for h in headers:
        f = _fold(h)
        toks = set(re.findall(r"\w+", f))
        if any(k in f for k in _PROHIBITED_SUBSTRINGS) or any(w in toks for w in _PROHIBITED_WORDS):
            out.append(h)
    return out


def field_alias_conflicts(dropped: list[str]) -> list[str]:
    names = {_fold(a) for f, meta in TIMESHEET_FIELDS.items() for a in [f, f.replace("_", " "), *meta["aliases"]]}
    return [h for h in dropped if _fold(h) in names]


def normalize_options(raw: dict | None) -> dict:
    """Upload options of a timesheet batch: mode (skip | replace, default skip) and approved_source (bool)."""
    raw = raw or {}
    mode = raw.get("mode") or "skip"
    if mode not in MODES:
        raise ValueError("mode must be 'skip' or 'replace'")
    approved = raw.get("approved_source")
    if approved is not None and not isinstance(approved, bool):
        raise ValueError("approved_source must be true or false")
    return {"mode": mode, "approved_source": bool(approved)}


def _hours(v: str) -> float:
    f = float(v.replace(",", "."))
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError(v)
    return round(f, 2)


def _num(v) -> float | None:
    return None if v is None else round(float(v), 2)


class _Resolver:
    """Employment of an employee at a date, cached per (source, external id, employer): identity map first, then the
    employer's employee number (same order as imports._resolve_employment, one query per employee instead of per row)."""

    def __init__(self, db: Session, tenant_id: int):
        self.db, self.tenant_id, self.cache = db, tenant_id, {}

    def __call__(self, source: str, external_id: str, entity_id: int, on: date):
        k = (source, external_id, entity_id)
        if k not in self.cache:
            by_identity = list(self.db.execute(select(EmploymentRelationship).join(
                PersonIdentifier, PersonIdentifier.person_id == EmploymentRelationship.person_id).where(
                PersonIdentifier.tenant_id == self.tenant_id, PersonIdentifier.source_system == source,
                PersonIdentifier.external_id == external_id,
                EmploymentRelationship.legal_entity_id == entity_id)).scalars())
            by_number = list(self.db.execute(select(EmploymentRelationship).where(
                EmploymentRelationship.tenant_id == self.tenant_id, EmploymentRelationship.employee_number == external_id,
                EmploymentRelationship.legal_entity_id == entity_id)).scalars())
            self.cache[k] = (by_identity, by_number)
        for spells in self.cache[k]:
            found = [e for e in spells if e.start_date <= on and (e.end_date is None or e.end_date >= on)]
            if found:
                return found
        return []


def _months(sd: date, ed: date) -> set[date]:
    out, m = set(), date(sd.year, sd.month, 1)
    while m <= ed:
        out.add(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def validate(db: Session, ctx: AccessContext, batch: ImportBatch, mapping: dict) -> dict:
    from app.services import coverage as cov
    from app.services.imports import OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, _import_scope, _parse_date, summary
    from app.services.safety_import import _entity_on_project

    scope = _import_scope(ctx)
    today = date.today()
    opts = normalize_options(batch.options)
    status_mapped = bool(mapping.get("status"))
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    projects = {p.code: p for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    shifts = {s.code: s.id for s in db.execute(select(Shift).where(Shift.tenant_id == ctx.tenant_id)).scalars()}
    closed = set(db.execute(select(PeriodClose.period_month).where(
        PeriodClose.tenant_id == ctx.tenant_id, PeriodClose.status == "closed")).scalars())
    resolve = _Resolver(db, ctx.tenant_id)
    on_project: dict[tuple[int, int], bool] = {}
    staged = list(db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                             .order_by(ImportStagingRow.row_number)).scalars())
    parsed: list[tuple[ImportStagingRow, dict, list[dict]]] = []
    for row in staged:
        m = {f: (str(row.raw.get(src, "") or "").strip() if src else "") for f, src in mapping.items()}
        errs: list[dict] = []

        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        for f, meta in TIMESHEET_FIELDS.items():
            if meta["required"] and not m.get(f):
                err("required", f"'{f}' is required")
        src = m.get("source_system") or "import"
        if len(src) > 50:
            err("field_too_long", "source_system longer than 50 characters")
        le = ents.get(m.get("legal_entity_code", ""))
        if m.get("legal_entity_code") and le is None:
            err("unknown_legal_entity", f"legal entity '{m['legal_entity_code']}' not found")
        elif le is not None and scope is not None and le.id not in scope:
            err("scope", f"no import permission for legal entity '{le.code}'")
        period_days = 1
        if m.get("period_days"):
            try:
                pdv = float(m["period_days"].replace(",", "."))
                if pdv != int(pdv) or not 1 <= pdv <= 366:
                    raise ValueError
                period_days = int(pdv)
            except ValueError:
                err("period_days_format", "period_days must be a whole number of days 1-366")
        if period_days > 1:
            err("non_daily_granularity", "row is a period total (period_days > 1): daily checks and the WP-4 "
                                         "planned-on-leave detection cannot be applied", "warning")
        wd = None
        try:
            wd = _parse_date(m.get("work_date", ""))
        except ValueError as e:
            err("date_format", str(e))
        if wd is not None and wd > today:
            err("future_date", "work_date is after the import day")
            wd = None
        end = wd + timedelta(days=period_days - 1) if wd else None
        if wd is not None and _months(wd, end) & closed:
            err("period_closed", "work_date falls in a closed period (D-2: only the main contractor can reopen it)")
        prj = None
        if m.get("project_code"):
            prj = projects.get(m["project_code"])
            if prj is None:
                err("unknown_project", f"project '{m['project_code']}' not found")
            elif le is not None:
                k = (le.id, prj.id)
                if k not in on_project:
                    on_project[k] = _entity_on_project(db, ctx.tenant_id, le.id, prj)
                if not on_project[k]:
                    err("entity_not_on_project", "legal entity is neither the project owner nor a contractor on it",
                        "warning")
        shift_id = None
        if m.get("shift_code"):
            shift_id = shifts.get(m["shift_code"])
            if shift_id is None:
                err("unknown_shift", f"shift '{m['shift_code']}' not found -> stored without shift", "warning")
        cap = 24 * period_days
        h: dict[str, float | None] = {}
        for f in ("planned_hours", "worked_hours", "overtime_hours", "night_hours", "productive_hours"):
            h[f] = None
            if m.get(f):
                try:
                    h[f] = _hours(m[f])
                except ValueError:
                    err("hours_format", f"'{f}' must be a number")
                    continue
                if not 0 <= h[f] <= cap:
                    err("hours_range", f"'{f}' must be between 0 and {cap:g} h")
                    h[f] = None
            elif f in ("overtime_hours", "night_hours"):
                h[f] = 0.0  # WP-6 3.2.1: blank -> 0 (sources leave these empty when there is none)
        worked = h["worked_hours"]
        if worked is not None:
            if h["overtime_hours"] is not None and h["overtime_hours"] > worked:
                err("overtime_exceeds_worked", "overtime_hours greater than worked_hours (overtime is included in "
                                               "worked hours)")
            if h["night_hours"] is not None and h["night_hours"] > worked:
                err("night_exceeds_worked", "night_hours greater than worked_hours")
            if h["productive_hours"] is not None and h["productive_hours"] > worked:
                err("productive_exceeds_worked", "productive_hours greater than worked_hours")
            if period_days == 1 and worked > DAILY_MAX:
                err("daily_max_exceeded", f"worked_hours above {DAILY_MAX} h a day (4857 md. 63) -> loaded", "warning")
        if period_days == 1 and h["night_hours"] is not None and h["night_hours"] > NIGHT_MAX:
            err("night_over_7_5", f"night_hours above {NIGHT_MAX} h (4857 md. 69) -> loaded", "warning")
        day_type = None
        if m.get("day_type"):
            day_type = _DAY_TYPES.get(_fold(m["day_type"]))
            if day_type is None:
                day_type = "unknown"
                err("unknown_day_type", "day_type not recognised (work, weekly_rest, public_holiday, leave) -> "
                                        "stored as 'unknown'", "warning")
        if status_mapped:
            if m.get("status"):
                status = _STATUSES.get(_fold(m["status"]))
                if status is None:
                    err("status_unknown", "status not recognised (approved / draft); no default is assigned")
            else:
                status = "draft"
                err("approval_unknown", "empty approval status -> stored as draft (not counted in KPIs)", "warning")
        else:
            status = "approved" if opts["approved_source"] else "draft"
        emp = None
        if le is not None and wd is not None and not [e for e in errs if e["rule"] == "scope"] and m.get("external_id"):
            found = resolve(src, m["external_id"], le.id, wd)
            if not found:
                err("no_employment", f"no employment of '{m['external_id']}' with '{le.code}' on {wd.isoformat()}")
            elif len(found) > 1:
                err("ambiguous_employment", "several employments match; resolve the identity first")
            else:
                emp = found[0]
                if period_days > 1 and emp.end_date and end > emp.end_date:
                    err("past_employment_end", "period row ends after the employment end date", "warning")
        mp = {"external_id": m.get("external_id"), "legal_entity_code": m.get("legal_entity_code"),
              "legal_entity_id": le.id if le else None, "employment_id": emp.id if emp else None,
              "source_system": src, "work_date": wd.isoformat() if wd else None, "period_days": period_days,
              "project_id": prj.id if prj else None, "project_derived": False, "shift_id": shift_id,
              **h, "day_type": day_type, "status": status}
        parsed.append((row, mp, errs))

    # project attribution from the assignment (ADR-017 visibility needs a project on the row)
    emp_ids = {mp["employment_id"] for _, mp, _ in parsed if mp["employment_id"]}
    assigns: dict[int, list[tuple[date, date | None, int]]] = defaultdict(list)
    for a in db.execute(select(Assignment).where(Assignment.tenant_id == ctx.tenant_id,
                                                 Assignment.employment_id.in_(emp_ids or {-1}),
                                                 Assignment.project_id.is_not(None))).scalars():
        assigns[a.employment_id].append((a.valid_from, a.valid_to, a.project_id))
    for _, mp, errs in parsed:
        if mp["project_id"] is not None or not mp["employment_id"] or not mp["work_date"] or \
                [e for e in errs if e["rule"] == "unknown_project"]:
            continue
        wd = date.fromisoformat(mp["work_date"])
        cands = {p for vf, vt, p in assigns.get(mp["employment_id"], ()) if vf <= wd and (vt is None or wd < vt)}
        if len(cands) == 1:
            mp["project_id"], mp["project_derived"] = cands.pop(), True
            errs.append({"rule": "project_derived_from_assignment", "severity": "warning",
                         "message": "no project code: project taken from the employee's assignment on that day"})
        else:
            errs.append({"rule": "project_code_missing", "severity": "warning",
                         "message": "no project code and no single assignment project: the row counts for the "
                                    "employer but is invisible in project views and to project-limited viewers"})

    # file-level: duplicates, stored rows (re-import), coverage
    dates = [date.fromisoformat(mp["work_date"]) for _, mp, _ in parsed if mp["work_date"]]
    existing: dict[tuple, Timesheet] = {}
    if emp_ids and dates:
        for t in db.execute(select(Timesheet).where(Timesheet.tenant_id == ctx.tenant_id,
                                                    Timesheet.employment_id.in_(emp_ids),
                                                    Timesheet.work_date >= min(dates),
                                                    Timesheet.work_date <= max(dates))).scalars():
            existing[(t.employment_id, t.work_date, t.project_id)] = t
    seen: set[tuple] = set()
    for _, mp, errs in parsed:
        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        mp["existing_id"] = None
        mp["replace"] = False
        le_id = mp["legal_entity_id"]
        if le_id is not None and batch.coverage and mp["work_date"]:
            wd = date.fromisoformat(mp["work_date"])
            if cov.row_outside(batch.coverage, le_id, wd, wd + timedelta(days=mp["period_days"] - 1),
                               mp["project_id"]):
                err(OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, "warning")
        if not mp["employment_id"] or not mp["work_date"]:
            continue
        key = (mp["employment_id"], mp["work_date"], mp["project_id"])
        if key in seen:
            err("duplicate_in_file", "duplicate row (same employee, day and project) in file")
            continue
        seen.add(key)
        ex = existing.get((mp["employment_id"], date.fromisoformat(mp["work_date"]), mp["project_id"]))
        if ex is None or [e for e in errs if e["severity"] == "error"]:
            continue
        mp["existing_id"] = ex.id
        changed = [f for f in _COMPARED if _stored(ex, f) != mp[f]]
        if not changed:
            err("already_imported", "identical timesheet row already stored -> will be skipped", "warning")
        elif opts["mode"] == "replace":
            mp["replace"] = True
            err("timesheet_corrected", "stored row differs -> corrected in place on commit (mode = replace; fields: "
                                       + ", ".join(changed) + ")", "warning")
        else:
            err("already_imported_changed", "a row for this employee, day and project is already stored with other "
                                            "values -> skipped; upload the corrected file with mode = replace",
                "warning")

    valid = errors = 0
    issues = []
    for row, mp, errs in parsed:
        row.mapped = mp
        row.errors = errs
        hard = [e for e in errs if e["severity"] == "error"]
        row.has_error = bool(hard)
        if hard:
            errors += 1
        else:
            valid += 1
        for e in errs:
            issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "timesheet",
                           "rule_code": e["rule"], "severity": e["severity"], "message": e["message"],
                           "row_number": row.row_number, "record_ref": mp.get("external_id")})
    ok = [mp for row, mp, errs in parsed if not row.has_error and mp["employment_id"] and mp["work_date"]]
    group, observed = _cross_row(db, ctx, batch, ok)
    if not status_mapped and not opts["approved_source"] and staged:
        group.append(("approval_unknown", "no approval status column and no approved_source declaration -> all rows "
                                          "stored as draft (not counted in KPIs); map the status column or upload "
                                          "with approved_source = true"))
    if batch.dropped_columns:
        group.append(("prohibited_columns_dropped", "name / identity / health column(s) dropped before storage (names "
                                                    "only, values never stored): " + ", ".join(batch.dropped_columns)))
    for rule, msg in group:
        issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "timesheet",
                       "rule_code": rule, "severity": "warning", "message": msg, "row_number": None,
                       "record_ref": None})
    if issues:
        db.bulk_insert_mappings(DataQualityIssue, issues)
    batch.options = {**opts, "observed_planned_on_leave_days": observed}
    batch.valid_rows, batch.error_rows, batch.status = valid, errors, "validated"
    db.commit()
    return summary(db, ctx, batch.id)


def _stored(t: Timesheet, f: str):
    v = getattr(t, f)
    if f in ("planned_hours", "worked_hours", "overtime_hours", "night_hours", "productive_hours"):
        return _num(v)
    if f == "period_days":
        return v or 1
    return v


def _cross_row(db: Session, ctx: AccessContext, batch: ImportBatch, rows: list[dict]) -> tuple[list, dict | None]:
    """WP-6 data 3.2.2 group-level checks over the valid rows of the file. Returns ([(rule, message)], observed
    planned-on-leave profile). Counts only - no employee, day or hours list (KVKK md.4)."""
    out: list[tuple[str, str]] = []
    daily = [r for r in rows if r["period_days"] == 1]
    if not rows:
        return out, None
    # overtime is defined weekly (md. 41): ISO weeks lying completely inside the file's date span
    dates = [date.fromisoformat(r["work_date"]) for r in daily]
    emp_ids = {r["employment_id"] for r in daily}
    entity_of = dict(db.execute(select(EmploymentRelationship.id, EmploymentRelationship.legal_entity_id).where(
        EmploymentRelationship.id.in_(emp_ids))).all()) if emp_ids else {}
    resolver, holidays = PatternResolver(db, ctx.tenant_id, emp_ids), Holidays(db)

    def pattern(emp: int, d: date):
        return resolver.resolve(emp, entity_of.get(emp), d)

    weeks: dict[tuple[int, date], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in daily:
        d = date.fromisoformat(r["work_date"])
        w = weeks[(r["employment_id"], d - timedelta(days=d.weekday()))]
        w[0] += r["worked_hours"] or 0
        w[1] += r["overtime_hours"] or 0
    if dates:
        lo, hi = min(dates), max(dates)
        bad = 0
        for (emp, mon), (worked, ot) in weeks.items():
            if mon >= lo and mon + timedelta(days=6) <= hi:
                p = pattern(emp, mon)
                if abs(ot - max(0.0, worked - (p.weekly_hours if p else WEEKLY_HOURS))) > 1:
                    bad += 1
        if bad:
            out.append(("overtime_weekly_inconsistent", f"{bad} employee-week(s): weekly overtime differs by more "
                        f"than 1 h from worked hours above the work pattern's weekly hours ({WEEKLY_HOURS} h where no "
                        "pattern is known; 4857 md. 41; equalisation L-15)"))
    if not dates:
        return out, {"profile": "unknown", "leave_workdays": 0, "with_planned_hours": 0}
    lo, hi = min(dates), max(dates)
    leaves = list(db.execute(select(LeaveRecord.employment_id, LeaveRecord.leave_type, LeaveRecord.start_date,
                                    LeaveRecord.end_date).where(
        LeaveRecord.tenant_id == ctx.tenant_id, LeaveRecord.employment_id.in_(emp_ids),
        LeaveRecord.start_date <= hi, LeaveRecord.end_date >= lo, LeaveRecord.leave_type != "public_holiday")))
    by_day: dict[tuple[int, date], list[dict]] = defaultdict(list)
    for r in daily:
        by_day[(r["employment_id"], date.fromisoformat(r["work_date"]))].append(r)
    paper = 0
    leave_days: set[tuple[int, date]] = set()
    paper_days: set[tuple[int, date]] = set()
    for emp, lt, sd, ed in leaves:
        d = max(sd, lo)
        while d <= min(ed, hi):
            rs = by_day.get((emp, d), [])
            if lt in CERTIFIED_LEAVE and any((r["worked_hours"] or 0) > 0 for r in rs):
                paper_days.add((emp, d))
            p = pattern(emp, d)
            workday = d.isoweekday() in (p.weekdays if p else DEFAULT_WEEKDAYS) and (
                p is None or holidays.fraction(p.country, d) < 1)
            if workday and not any(r["day_type"] in ("weekly_rest", "public_holiday") for r in rs):
                leave_days.add((emp, d))
            d += timedelta(days=1)
    paper = len(paper_days)
    if paper:
        out.append(("leave_and_work_same_day", f"{paper} employee-day(s) with an annual / certified-rest leave record "
                    "and worked hours > 0 (E.1 'paper leave' check; the correction list is for HR operations)"))
    with_planned = sum(1 for k in leave_days if any((r["planned_hours"] or 0) > 0 for r in by_day.get(k, [])))
    n = len(leave_days)
    if len(daily) < len(rows):
        profile = "unknown"  # WP-4 review condition 2: no daily granularity -> no detection
    elif not n:
        profile = None
    else:
        ratio = with_planned / n
        profile = "included" if ratio >= 0.9 else "excluded" if ratio <= 0.1 else "mixed"
    declared = (batch.coverage or {}).get("planned_on_leave_days")
    if declared in ("included", "excluded") and profile in ("included", "excluded", "mixed") and profile != declared:
        out.append(("planned_on_leave_mismatch", f"declared planned_on_leave_days '{declared}' but the file shows "
                    f"'{profile}' ({with_planned} of {n} leave working day(s) carry planned hours; WP-4 3.5)"))
    mism = _pattern_mismatch(daily, pattern, holidays, leaves, lo, hi)
    if mism:
        out.append(("pattern_timesheet_mismatch", f"{mism} employee(s): planned hours on days outside the work "
                    "pattern, or missing on pattern working days, in >= 50 % of complete weeks (WP-4 section 8 - check "
                    "the declared pattern)"))
    return out, {"profile": profile, "leave_workdays": n, "with_planned_hours": with_planned}


def _pattern_mismatch(daily: list[dict], pattern, holidays: Holidays, leaves, lo: date, hi: date) -> int:
    """WP-4 section 8 manipulation control (count of employees only): per complete ISO week inside the file span, a
    week is off-pattern when planned hours > 0 fall on a weekday outside the pattern (e.g. Saturday in a 5-day
    pattern) or a pattern working day has no planned hours although it is not a leave day, a public holiday or a
    rest/holiday row (e.g. Saturday never planned in a 6-day pattern). An employee with a known pattern and >= 50 % of
    such weeks is counted. Employees without a known pattern are not judged."""
    planned: dict[tuple[int, date], bool] = defaultdict(bool)
    excused: set[tuple[int, date]] = set()
    has_rows: set[tuple[int, date]] = set()  # (employee, Monday) weeks with at least one row (no judging before hire)
    for r in daily:
        k = (r["employment_id"], date.fromisoformat(r["work_date"]))
        has_rows.add((k[0], k[1] - timedelta(days=k[1].weekday())))
        planned[k] |= (r["planned_hours"] or 0) > 0
        if r["day_type"] in ("weekly_rest", "public_holiday", "leave"):
            excused.add(k)
    for emp, _lt, sd, ed in leaves:
        d = max(sd, lo)
        while d <= min(ed, hi):
            excused.add((emp, d))
            d += timedelta(days=1)
    first = lo + timedelta(days=(7 - lo.weekday()) % 7)
    weeks: dict[int, list[bool]] = defaultdict(list)
    for emp in {r["employment_id"] for r in daily}:
        mon = first
        while mon + timedelta(days=6) <= hi:
            p = pattern(emp, mon) if (emp, mon) in has_rows else None
            if p is not None:
                off = False
                for i in range(7):
                    d = mon + timedelta(days=i)
                    k = (emp, d)
                    if d.isoweekday() not in p.weekdays:
                        off |= planned[k]
                    elif not planned[k] and k not in excused and holidays.fraction(p.country, d) < 1:
                        off = True
                weeks[emp].append(off)
            mon += timedelta(days=7)
    return sum(1 for w in weeks.values() if w and sum(w) / len(w) >= PATTERN_MISMATCH_SHARE)


def commit_row(db: Session, ctx: AccessContext, row: ImportStagingRow) -> date | None:
    """Insert a new row, or (mode = replace, decided at validation) correct the stored one in place with
    record_version + 1 and an audit row. Identical rows and skipped changes return None. Never deletes."""
    from app.services.imports import ImportError_

    mp = row.mapped
    wd = date.fromisoformat(mp["work_date"])
    emp = db.get(EmploymentRelationship, mp["employment_id"])
    if emp is None or emp.tenant_id != ctx.tenant_id:
        raise ImportError_("employment disappeared between validation and commit")
    ex = db.execute(select(Timesheet).where(
        Timesheet.employment_id == emp.id, Timesheet.work_date == wd,
        Timesheet.project_id.is_not_distinct_from(mp["project_id"]))).scalar()
    values = {f: mp[f] for f in _COMPARED}
    if ex is None:
        db.add(Timesheet(tenant_id=ctx.tenant_id, employment_id=emp.id, project_id=mp["project_id"], work_date=wd,
                         source_system=mp["source_system"], source_record_id=mp["external_id"],
                         updated_by=ctx.user_id, **values))
        return wd
    changed = {f: (_stored(ex, f), mp[f]) for f in _COMPARED if _stored(ex, f) != mp[f]}
    if not changed or not mp.get("replace"):
        return None  # identical (re-import / resumed commit) or a change without mode = replace
    for f in changed:
        setattr(ex, f, mp[f])
    ex.record_version = (ex.record_version or 1) + 1
    ex.updated_by = ctx.user_id
    db.add(AuditLog(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=TIMESHEET_CORRECT_ACTION,
                    entity_type="timesheet", entity_id=str(ex.id),
                    details={"legal_entity_id": emp.legal_entity_id, "project_id": ex.project_id,
                             "work_date": wd.isoformat(), "import_batch_id": row.batch_id,
                             "record_version": ex.record_version,
                             "changed": {f: {"from": a, "to": b} for f, (a, b) in changed.items()}}))
    return wd
