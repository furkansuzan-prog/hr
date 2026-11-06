"""Work patterns: definition, effective-dated assignment, public holidays and the `work_pattern` import (ADR-024).

Source: docs/hr/data/WP-4_work_pattern_leave_conversion.md 3.1 + HR Director review (conditions 1-4). Sources of a
real pattern: SAP HCM IT0007 (work schedule rule), SuccessFactors work schedule, Logo/Netsis "puantaj grubu".

`work_pattern` import - one row = a pattern definition and/or one assignment:
- definition: `pattern_code*`, `working_weekdays` (ISO 1 = Mon .. 7 = Sun: "1,2,3,4,5", "1-6", "Pzt-Cmt", "Mon-Sat"),
  `daily_hours`, optional `days_per_week` (= number of weekdays), `weekly_hours` (= days x daily hours +- 0.01),
  `country` (holiday calendar, default tenant country), `pattern_type` (only `weekly`, v1). Errors:
  `pattern_weekdays_mismatch`, `daily_hours_above_11` (md. 63), `weekly_hours_inconsistent`,
  `pattern_type_unsupported`, `pattern_definition_changed` (a referenced definition never changes - use a new code),
  `pattern_definition_conflict_in_file`, `unknown_pattern`; warning `weekly_hours_above_45` (TR, equalisation L-7);
- assignment: `level` (employment | project | legal_entity), `valid_from*` (with level), `valid_to` (exclusive),
  target by level: `external_id` + `legal_entity_code` (employment on valid_from), `project_code`,
  `legal_entity_code`. Errors: `pattern_assignment_overlap` (same target, overlapping interval - DB or file),
  `no_employment`, `ambiguous_employment`, `unknown_project`, `unknown_legal_entity`, `scope`; identical existing
  assignment -> `already_imported` (skipped).
Write scope: whole-entity import.write on the target's legal entity (project -> owner entity; ADR-017). Commit calls
mark_stale_from(valid_from) via imports._invalidate_from_committed (leave conversion and KPI-055/056 depend on it).
Patterns are not a data feed: no coverage declaration.
"""
import argparse
import csv
import re
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.analytics.engine import mark_stale_from
from app.models import (
    WORK_PATTERN_LEVELS,
    WORK_PATTERN_TYPES,
    DataQualityIssue,
    ImportBatch,
    ImportStagingRow,
    LegalEntity,
    Project,
    PublicHoliday,
    Tenant,
    WorkPattern,
    WorkPatternAssignment,
)
from app.security.access import AccessContext

MAX_DAILY_HOURS = 11  # 4857 md. 63 daily maximum
TR_WEEKLY_HOURS = 45  # above -> warning only (equalisation possible, L-7)

WORK_PATTERN_FIELDS = {
    "pattern_code": {"required": True, "aliases": ["düzen", "düzen kodu", "çalışma düzeni", "puantaj grubu",
                                                   "work schedule", "work schedule rule", "pattern", "pattern code",
                                                   "график", "режим работы"]},
    "days_per_week": {"required": False, "aliases": ["haftalık gün", "gün/hafta", "days per week", "дней в неделю"]},
    "working_weekdays": {"required": False, "aliases": ["çalışma günleri", "iş günleri", "working days",
                                                        "working weekdays", "рабочие дни"]},
    "daily_hours": {"required": False, "aliases": ["günlük saat", "daily hours", "часов в день"]},
    "weekly_hours": {"required": False, "aliases": ["haftalık saat", "weekly hours", "часов в неделю"]},
    "country": {"required": False, "aliases": ["ülke", "country", "страна"]},
    "pattern_type": {"required": False, "aliases": ["düzen tipi", "pattern type", "тип"]},
    "level": {"required": False, "aliases": ["düzey", "seviye", "level", "уровень"]},
    "external_id": {"required": False, "aliases": ["sicil", "sicil no", "personel no", "employee id",
                                                   "employee number", "табельный номер"]},
    "legal_entity_code": {"required": False, "aliases": ["firma", "firma kodu", "şirket", "company", "employer",
                                                         "компания"]},
    "project_code": {"required": False, "aliases": ["proje", "proje kodu", "project", "проект"]},
    "valid_from": {"required": False, "aliases": ["geçerlilik başlangıç", "başlangıç", "valid from", "from",
                                                  "действует с"]},
    "valid_to": {"required": False, "aliases": ["geçerlilik bitiş", "bitiş", "valid to", "to", "действует по"]},
    "source_system": {"required": False, "aliases": ["kaynak", "source", "система"]},
}
_DAY_NAMES = {
    "pzt": 1, "pazartesi": 1, "mon": 1, "monday": 1, "пн": 1,
    "sal": 2, "salı": 2, "sali": 2, "tue": 2, "tuesday": 2, "вт": 2,
    "çar": 3, "çarşamba": 3, "car": 3, "carsamba": 3, "wed": 3, "wednesday": 3, "ср": 3,
    "per": 4, "perşembe": 4, "persembe": 4, "thu": 4, "thursday": 4, "чт": 4,
    "cum": 5, "cuma": 5, "fri": 5, "friday": 5, "пт": 5,
    "cmt": 6, "cumartesi": 6, "sat": 6, "saturday": 6, "сб": 6,
    "paz": 7, "pazar": 7, "sun": 7, "sunday": 7, "вс": 7,
}
_LEVEL_ALIASES = {"employment": "employment", "kişi": "employment", "kisi": "employment", "personel": "employment",
                  "çalışan": "employment", "employee": "employment", "project": "project", "proje": "project",
                  "şantiye": "project", "legal_entity": "legal_entity", "işveren": "legal_entity",
                  "isveren": "legal_entity", "firma": "legal_entity", "employer": "legal_entity",
                  "company": "legal_entity"}


class PatternError(ValueError):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule


def parse_weekdays(v) -> list[int]:
    """'1,2,3,4,5' | '1-6' | 'Pzt-Cmt' | 'Mon, Tue' | [1, 2] -> sorted unique ISO weekdays."""
    if isinstance(v, list | tuple):
        days = {int(x) for x in v}
    else:
        days = set()
        for part in re.split(r"[,;/\s]+", str(v).strip().lower()):
            if not part:
                continue
            ends = part.split("-")
            if len(ends) > 2:
                raise ValueError(v)
            vals = [int(x) if x.isdigit() else _DAY_NAMES[x] for x in ends]
            days |= set(range(vals[0], vals[-1] + 1)) if len(vals) == 2 else {vals[0]}
    if not days or not all(1 <= d <= 7 for d in days):
        raise ValueError(v)
    return sorted(days)


def check_definition(weekdays, daily_hours: float, *, days_per_week: int | None = None,
                     weekly_hours: float | None = None, country: str = "TR",
                     pattern_type: str = "weekly") -> tuple[dict, list[tuple[str, str]]]:
    """WP-4 3.1(a) validation. Returns (normalised definition, warnings); raises PatternError."""
    if pattern_type not in WORK_PATTERN_TYPES:
        raise PatternError("pattern_type_unsupported", "only weekly patterns are supported (rotations count as "
                                                       "unknown pattern - WP-4 S-4)")
    try:
        days = parse_weekdays(weekdays)
    except (ValueError, KeyError):
        raise PatternError("pattern_weekdays_mismatch", "working_weekdays must list ISO weekdays 1-7 (e.g. '1-5', "
                                                        "'Pzt-Cmt')") from None
    if days_per_week is not None and days_per_week != len(days):
        raise PatternError("pattern_weekdays_mismatch", f"days_per_week {days_per_week} does not match "
                                                        f"{len(days)} working weekday(s)")
    dh = round(float(daily_hours), 2)
    if not 0 < dh:
        raise PatternError("hours_format", "daily_hours must be positive")
    if dh > MAX_DAILY_HOURS:
        raise PatternError("daily_hours_above_11", "daily_hours above 11 h (4857 md. 63 daily maximum)")
    wh = round(len(days) * dh, 2)
    if weekly_hours is not None and abs(float(weekly_hours) - wh) > 0.01:
        raise PatternError("weekly_hours_inconsistent", f"weekly_hours {weekly_hours} != days x daily hours ({wh})")
    warnings = []
    if (country or "TR").upper() == "TR" and wh > TR_WEEKLY_HOURS:
        warnings.append(("weekly_hours_above_45", f"weekly hours {wh} above 45 (4857 md. 63; equalisation may "
                                                  "apply - L-7)"))
    return ({"days_per_week": len(days), "working_weekdays": days, "daily_hours": dh, "weekly_hours": wh,
             "country": (country or "TR").upper(), "pattern_type": pattern_type}, warnings)


def _same_definition(p: WorkPattern, d: dict) -> bool:
    return (sorted(int(x) for x in p.working_weekdays) == d["working_weekdays"]
            and abs(float(p.daily_hours) - d["daily_hours"]) < 0.005 and p.country == d["country"]
            and p.pattern_type == d["pattern_type"])


def define_pattern(db: Session, tenant_id: int, code: str, weekdays, daily_hours: float, *, country: str = "TR",
                   source_system: str = "manual", user_id: int | None = None) -> WorkPattern:
    """Create a pattern (or return the identical existing one). A different definition under an existing code is
    rejected: leave rows already converted with it would silently change (WP-4 3.1; new code instead)."""
    d, _ = check_definition(weekdays, daily_hours, country=country)
    p = db.execute(select(WorkPattern).where(WorkPattern.tenant_id == tenant_id, WorkPattern.code == code)).scalar()
    if p is not None:
        if not _same_definition(p, d):
            raise PatternError("pattern_definition_changed", f"pattern '{code}' exists with another definition; "
                                                             "use a new code")
        return p
    p = WorkPattern(tenant_id=tenant_id, code=code, source_system=source_system, updated_by=user_id, **d)
    db.add(p)
    db.flush()
    return p


def _target_col(level: str):
    return {"employment": WorkPatternAssignment.employment_id, "project": WorkPatternAssignment.project_id,
            "legal_entity": WorkPatternAssignment.legal_entity_id}[level]


def overlapping(db: Session, tenant_id: int, level: str, target_id: int, vf: date, vt: date | None):
    q = select(WorkPatternAssignment).where(
        WorkPatternAssignment.tenant_id == tenant_id, WorkPatternAssignment.level == level,
        _target_col(level) == target_id, or_(WorkPatternAssignment.valid_to.is_(None),
                                             WorkPatternAssignment.valid_to > vf))
    if vt is not None:
        q = q.where(WorkPatternAssignment.valid_from < vt)
    return list(db.execute(q).scalars())


def assign_pattern(db: Session, tenant_id: int, pattern: WorkPattern, level: str, target_id: int, valid_from: date,
                   valid_to: date | None = None, *, source_system: str = "manual", user_id: int | None = None,
                   invalidate: bool = True) -> WorkPatternAssignment:
    if level not in WORK_PATTERN_LEVELS:
        raise PatternError("level", f"level must be one of {', '.join(WORK_PATTERN_LEVELS)}")
    if valid_to is not None and valid_to <= valid_from:
        raise PatternError("date_order", "valid_to must be after valid_from (half-open interval)")
    if overlapping(db, tenant_id, level, target_id, valid_from, valid_to):
        raise PatternError("pattern_assignment_overlap", "another pattern assignment of this target overlaps")
    a = WorkPatternAssignment(tenant_id=tenant_id, work_pattern_id=pattern.id, level=level,
                              valid_from=valid_from, valid_to=valid_to, source_system=source_system,
                              updated_by=user_id, **{f"{level}_id": target_id})
    db.add(a)
    db.flush()
    if invalidate:
        mark_stale_from(db, tenant_id, valid_from)
    return a


def load_public_holidays(db: Session, country: str, rows, *, source: str) -> int:
    """Insert / update (date -> fraction, name) rows of a country calendar. rows: iterable of (date, fraction, name)."""
    n = 0
    country = country.upper()
    rows = list(rows)
    for d, frac, name in rows:
        frac = float(frac)
        if not 0 < frac <= 1:
            raise PatternError("fraction", f"holiday fraction must be in (0, 1]: {d}")
        h = db.execute(select(PublicHoliday).where(PublicHoliday.country == country,
                                                   PublicHoliday.holiday_date == d)).scalar()
        if h is None:
            db.add(PublicHoliday(country=country, holiday_date=d, fraction=frac, name=name[:100], source=source[:120]))
        else:
            h.fraction, h.name, h.source = frac, name[:100], source[:120]
        n += 1
    db.flush()
    db.info.pop("holiday_memo", None)
    # every tenant's leave conversion and leave KPIs may change
    if rows:
        earliest = min(r[0] for r in rows)
        for tid in db.execute(select(Tenant.id)).scalars().all():
            mark_stale_from(db, tid, earliest)
    return n


# ---------------------------------------------------------------- import (entity_type = work_pattern)


def _num(v: str) -> float:
    return float(str(v).replace(",", "."))


def validate(db: Session, ctx: AccessContext, batch: ImportBatch, mapping: dict) -> dict:
    from app.services.imports import _import_scope, _parse_date, _resolve_employment, summary

    import_scope = _import_scope(ctx)
    tenant_country = db.execute(select(Tenant.country).where(Tenant.id == ctx.tenant_id)).scalar() or "TR"
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    projects = {p.code: p for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    existing = {p.code: p for p in db.execute(select(WorkPattern).where(WorkPattern.tenant_id == ctx.tenant_id))
                .scalars()}
    file_defs: dict[str, dict] = {}
    file_spans: dict[tuple[str, int], list[tuple[date, date | None, str]]] = {}
    valid = errors = 0
    issues = []
    for row in db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                          .order_by(ImportStagingRow.row_number)).scalars():
        m = {f: (str(row.raw.get(src, "") or "").strip() if src else "") for f, src in mapping.items()}
        errs: list[dict] = []

        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        code = m.get("pattern_code", "")
        if not code:
            err("required", "'pattern_code' is required")
        definition = None
        if any(m.get(f) for f in ("working_weekdays", "daily_hours", "days_per_week", "weekly_hours")):
            if not m.get("working_weekdays") or not m.get("daily_hours"):
                err("required", "a pattern definition needs working_weekdays and daily_hours")
            else:
                try:
                    definition, warns = check_definition(
                        m["working_weekdays"], _num(m["daily_hours"]),
                        days_per_week=int(_num(m["days_per_week"])) if m.get("days_per_week") else None,
                        weekly_hours=_num(m["weekly_hours"]) if m.get("weekly_hours") else None,
                        country=m.get("country") or tenant_country,
                        pattern_type=(m.get("pattern_type") or "weekly").lower())
                    for rule, msg in warns:
                        err(rule, msg, "warning")
                except PatternError as e:
                    err(e.rule, str(e))
                except ValueError:
                    err("hours_format", "days_per_week / daily_hours / weekly_hours must be numbers")
        if code and definition is not None:
            if code in existing and not _same_definition(existing[code], definition):
                err("pattern_definition_changed", f"pattern '{code}' exists with another definition; a referenced "
                                                  "definition never changes - use a new code")
            elif code in file_defs and file_defs[code] != definition:
                err("pattern_definition_conflict_in_file", f"pattern '{code}' is defined differently in this file")
            else:
                file_defs.setdefault(code, definition)
        elif code and code not in existing and code not in file_defs:
            err("unknown_pattern", f"pattern '{code}' is neither defined in this file nor known")
        level = _LEVEL_ALIASES.get((m.get("level") or "").lower()) if m.get("level") else None
        if m.get("level") and level is None:
            err("level", "level must be employment | project | legal_entity")
        target = vf = vt = None
        entity_for_scope = None
        if level:
            try:
                vf, vt = _parse_date(m.get("valid_from", "")), _parse_date(m.get("valid_to", ""))
                if vf is None:
                    err("required", "'valid_from' is required for an assignment")
                elif vt is not None and vt <= vf:
                    err("date_order", "valid_to must be after valid_from (exclusive end)")
                    vf = None
            except ValueError as e:
                err("date_format", str(e))
            le = ents.get(m.get("legal_entity_code", ""))
            if level in ("employment", "legal_entity"):
                if not m.get("legal_entity_code"):
                    err("required", "'legal_entity_code' is required for this level")
                elif le is None:
                    err("unknown_legal_entity", f"legal entity '{m['legal_entity_code']}' not found")
                entity_for_scope = le.id if le else None
            if level == "legal_entity" and le is not None:
                target = le.id
            elif level == "project":
                p = projects.get(m.get("project_code", ""))
                if p is None:
                    err("unknown_project", f"project '{m.get('project_code')}' not found")
                else:
                    target, entity_for_scope = p.id, p.owner_entity_id
            if entity_for_scope is not None and import_scope is not None and entity_for_scope not in import_scope:
                err("scope", "no whole-entity import permission for the assignment's legal entity")
                target = None
            elif level == "employment" and le is not None and vf is not None:
                found, _ = _resolve_employment(db, ctx, m.get("source_system") or "import",
                                               m.get("external_id", ""), le.id, vf)
                if not found:
                    err("no_employment", f"no employment of '{m.get('external_id')}' with '{le.code}' on "
                                         f"{vf.isoformat()}")
                elif len(found) > 1:
                    err("ambiguous_employment", "several employments match; resolve the identity first")
                else:
                    target = found[0].id
            if target is not None and vf is not None:
                same = False
                for a in overlapping(db, ctx.tenant_id, level, target, vf, vt):
                    pcode = next((c for c, p in existing.items() if p.id == a.work_pattern_id), None)
                    if (pcode, a.valid_from, a.valid_to) == (code, vf, vt):
                        same = True
                    else:
                        err("pattern_assignment_overlap", "another pattern assignment of this target overlaps "
                                                          "(close it first: valid_to)")
                        break
                if same and not [e for e in errs if e["rule"] == "pattern_assignment_overlap"]:
                    err("already_imported", "identical assignment already exists -> will be skipped", "warning")
                for a_vf, a_vt, _a_code in file_spans.get((level, target), ()):
                    if a_vf < (vt or date.max) and vf < (a_vt or date.max):
                        err("pattern_assignment_overlap", "overlaps another assignment of the same target in this "
                                                          "file")
                        break
                file_spans.setdefault((level, target), []).append((vf, vt, code))
        row.mapped = {"pattern_code": code, "definition": definition, "level": level, "target_id": target,
                      "valid_from": vf.isoformat() if vf else None, "valid_to": vt.isoformat() if vt else None,
                      "source_system": m.get("source_system") or "import",
                      "skip_assignment": any(e["rule"] == "already_imported" for e in errs)}
        row.errors = errs
        hard = [e for e in errs if e["severity"] == "error"]
        row.has_error = bool(hard)
        if hard:
            errors += 1
        else:
            valid += 1
        for e in errs:
            issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "work_pattern",
                           "rule_code": e["rule"], "severity": e["severity"], "message": e["message"],
                           "row_number": row.row_number, "record_ref": code or None})
    if issues:
        db.bulk_insert_mappings(DataQualityIssue, issues)
    batch.valid_rows, batch.error_rows, batch.status = valid, errors, "validated"
    db.commit()
    return summary(db, ctx, batch.id)


def commit_row(db: Session, ctx: AccessContext, row: ImportStagingRow) -> date | None:
    m = row.mapped
    p = db.execute(select(WorkPattern).where(WorkPattern.tenant_id == ctx.tenant_id,
                                             WorkPattern.code == m["pattern_code"])).scalar()
    if p is None:
        d = m["definition"]
        if d is None:
            from app.services.imports import ImportError_
            raise ImportError_("pattern disappeared between validation and commit")
        p = WorkPattern(tenant_id=ctx.tenant_id, code=m["pattern_code"], source_system=m["source_system"],
                        updated_by=ctx.user_id, **d)
        db.add(p)
        db.flush()
    if not m.get("level") or m.get("skip_assignment") or m.get("target_id") is None:
        return None
    vf = date.fromisoformat(m["valid_from"])
    vt = date.fromisoformat(m["valid_to"]) if m.get("valid_to") else None
    for a in overlapping(db, ctx.tenant_id, m["level"], m["target_id"], vf, vt):
        if (a.work_pattern_id, a.valid_from, a.valid_to) == (p.id, vf, vt):
            return None  # resumed commit
    assign_pattern(db, ctx.tenant_id, p, m["level"], m["target_id"], vf, vt, source_system=m["source_system"],
                   user_id=ctx.user_id, invalidate=False)
    return vf


def main() -> None:  # pragma: no cover - operator CLI
    """python -m app.services.work_patterns holidays TR calendar.csv --source "Resmi Gazete ..."
    CSV columns: date (YYYY-MM-DD), fraction (1 or 0.5), name."""
    from app.core.db import SessionLocal

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("holidays")
    h.add_argument("country")
    h.add_argument("csv_path")
    h.add_argument("--source", required=True)
    a = ap.parse_args()
    with open(a.csv_path, newline="", encoding="utf-8") as f:
        rows = [(date.fromisoformat(r["date"]), float(r.get("fraction") or 1), r.get("name") or "")
                for r in csv.DictReader(f)]
    with SessionLocal() as db:
        print(load_public_holidays(db, a.country, rows, source=a.source), "holiday row(s) loaded")
        db.commit()


if __name__ == "__main__":  # pragma: no cover
    main()
