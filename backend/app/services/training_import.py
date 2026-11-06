"""Training import, entity_type = "training" (wave-2 item 18, ADR-027).

Source: docs/hr/data/WP-6_import_field_dictionaries.md 3.1 + 3.3 and the HR Director review (conditions 1-6; condition 4:
no "statutory minimum not met" note until the workplace hazard class exists - only the breakdown is shown). Review
conditions override the spec text where they differ.

- one row = one employee's completed training; natural key (employment, course_code, completed_on);
- employment resolution as the leave / timesheet import (identity map -> employer's employee number, on completed_on);
- no default category / status: an unmappable category is an error (`unknown_training_category`); an unmapped status
  column needs the batch option all_completed = true (validation refuses otherwise). Only status `completed` is
  stored; attended / failed / cancelled (and a blank status) rows are loaded as skipped with a warning;
- `legal_basis` is informative (L-14): unknown value -> warning, NULL; an ISG_* basis on a non-HSE category -> warning
  `legal_basis_category_mismatch` (WP-6 section 8 "non-HSE coded as HSE"). is_statutory_hse is derived
  (category = hse AND legal_basis ISG_*) and never stored;
- re-import: same key + same values -> `already_imported` (skipped); other values -> `already_imported_changed`
  (skipped) unless the batch was uploaded with mode = replace -> corrected in place (record_version + 1, audit_logs
  `training.correct`, warning `training_corrected`). Closed months are rejected (`period_closed`, D-2). Rows missing
  from a later file are never deleted;
- KVKK md.4: name / national-id / health columns are dropped on upload (names of the columns only) - except headers
  that are exactly an alias of a training field (e.g. "eğitim adı" = course name, not a person's name);
- feeds coverage `training` (EVENT feed: a header-only file with completeness 'complete' confirms an empty month);
  commit calls mark_stale_from via imports._invalidate_from_committed (key `completed_on`).
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    TRAINING_CORRECT_ACTION,
    TRAINING_LEGAL_BASES,
    AuditLog,
    DataQualityIssue,
    EmploymentRelationship,
    ImportBatch,
    ImportStagingRow,
    LegalEntity,
    PeriodClose,
    TrainingRecord,
)
from app.security.access import AccessContext
from app.services import timesheet_import as tsi

TRAINING_FIELDS = {
    "external_id": {"required": True, "aliases": ["sicil", "sicil no", "personel no", "employee id", "employee number",
                                                   "табельный номер"]},
    "source_system": {"required": False, "aliases": ["kaynak", "source", "lms", "система"]},
    "legal_entity_code": {"required": True, "aliases": ["firma", "firma kodu", "şirket", "company", "employer",
                                                        "компания"]},
    "course_code": {"required": True, "aliases": ["eğitim kodu", "ders kodu", "course code", "код курса"]},
    "course_name": {"required": True, "aliases": ["eğitim adı", "ders adı", "course name", "название курса"]},
    "category": {"required": True, "aliases": ["eğitim türü", "kategori", "category", "категория"]},
    "legal_basis": {"required": False, "aliases": ["yasal dayanak", "legal basis", "правовое основание"]},
    "hours": {"required": True, "aliases": ["süre", "saat", "hours", "часы"]},
    "completed_on": {"required": True, "aliases": ["tamamlanma tarihi", "eğitim tarihi", "completion date",
                                                   "дата окончания"]},
    "start_date": {"required": False, "aliases": ["başlangıç", "başlangıç tarihi", "start", "start date", "начало"]},
    "status": {"required": False, "aliases": ["durum", "status", "статус"]},
    "delivery": {"required": False, "aliases": ["yöntem", "eğitim yöntemi", "delivery", "формат"]},
    "mandatory": {"required": False, "aliases": ["zorunlu", "mandatory", "обязательный"]},
}
MODES = ("skip", "replace")
MAX_HOURS = 9999.99  # Numeric(6, 2)

# value synonyms (folded with imports._norm: lower case, no diacritics, '_' / '-' -> space)
_CATEGORIES = {
    "hse": "hse", "isg": "hse", "is guvenligi": "hse", "is sagligi ve guvenligi": "hse", "ohs": "hse",
    "охрана труда": "hse",
    "technical": "technical", "teknik": "technical", "mesleki": "technical", "техническое": "technical",
    "compliance": "compliance", "uyum": "compliance", "yasal": "compliance", "соответствие": "compliance",
    "soft": "soft", "soft skills": "soft", "kisisel gelisim": "soft", "личностное развитие": "soft",
}
_STATUSES = {
    "completed": "completed", "tamamlandi": "completed", "tamamlanan": "completed", "basarili": "completed",
    "passed": "completed", "завершено": "completed", "завершен": "completed",
    "attended": "attended", "katildi": "attended", "katilim": "attended", "посетил": "attended",
    "failed": "failed", "basarisiz": "failed", "kaldi": "failed", "не сдал": "failed",
    "cancelled": "cancelled", "canceled": "cancelled", "iptal": "cancelled", "отменено": "cancelled",
}
_DELIVERY = {
    "classroom": "classroom", "sinif": "classroom", "sinif ici": "classroom", "yuz yuze": "classroom",
    "очно": "classroom",
    "online": "online", "uzaktan": "online", "e ogrenme": "online", "elearning": "online", "e learning": "online",
    "дистанционно": "online",
    "on the job": "on_the_job", "isbasi": "on_the_job", "is basi": "on_the_job", "на рабочем месте": "on_the_job",
}
_TRUE = {"true", "1", "evet", "e", "yes", "y", "x", "zorunlu", "да"}
_FALSE = {"false", "0", "hayir", "h", "no", "n", "нет"}
_COMPARED = ("course_name", "category", "hours", "legal_basis", "start_date", "delivery", "mandatory")


def _fold(s: str) -> str:
    return tsi._fold(s)


def field_names(fields: dict) -> set[str]:
    return {_fold(a) for f, meta in fields.items() for a in [f, f.replace("_", " "), *meta["aliases"]]}


def prohibited_columns(headers: list[str], fields: dict | None = None) -> list[str]:
    """Name / identity / health columns (timesheet list, ADR-022) - a header that is exactly a field alias is kept."""
    names = field_names(fields or TRAINING_FIELDS)
    return [h for h in tsi.prohibited_columns(headers) if _fold(h) not in names]


def normalize_options(raw: dict | None) -> dict:
    """Upload options of a training batch: mode (skip | replace, default skip) and all_completed (bool)."""
    raw = raw or {}
    if raw.get("approved_source") is not None:
        raise ValueError("approved_source is only for entity_type 'timesheet'")
    mode = raw.get("mode") or "skip"
    if mode not in MODES:
        raise ValueError("mode must be 'skip' or 'replace'")
    done = raw.get("all_completed")
    if done is not None and not isinstance(done, bool):
        raise ValueError("all_completed must be true or false")
    return {"mode": mode, "all_completed": bool(done)}


def parse_bool(v: str) -> bool | None:
    f = _fold(v)
    return True if f in _TRUE else False if f in _FALSE else None


def is_statutory_hse(category: str | None, legal_basis: str | None) -> bool:
    return category == "hse" and bool(legal_basis) and legal_basis.startswith("ISG_")


def validate(db: Session, ctx: AccessContext, batch: ImportBatch, mapping: dict) -> dict:
    from app.services import coverage as cov
    from app.services.imports import (
        OUTSIDE_COVERAGE,
        OUTSIDE_COVERAGE_MSG,
        ImportError_,
        _import_scope,
        _parse_date,
        summary,
    )

    opts = normalize_options(batch.options)
    status_mapped = bool(mapping.get("status"))
    if not status_mapped and not opts["all_completed"] and batch.total_rows:
        raise ImportError_("No status column is mapped: map it, or upload the file with all_completed = true "
                           "(WP-6 3.3.1 - no default completion status is assigned)")
    scope = _import_scope(ctx)
    today = date.today()
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    closed = set(db.execute(select(PeriodClose.period_month).where(
        PeriodClose.tenant_id == ctx.tenant_id, PeriodClose.status == "closed")).scalars())
    resolve = tsi._Resolver(db, ctx.tenant_id)
    staged = list(db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                             .order_by(ImportStagingRow.row_number)).scalars())
    parsed: list[tuple[ImportStagingRow, dict, list[dict]]] = []
    for row in staged:
        m = {f: (str(row.raw.get(src, "") or "").strip() if src else "") for f, src in mapping.items()}
        errs: list[dict] = []

        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        for f, meta in TRAINING_FIELDS.items():
            if meta["required"] and not m.get(f):
                err("required", f"'{f}' is required")
        src = m.get("source_system") or "import"
        if len(src) > 50:
            err("field_too_long", "source_system longer than 50 characters")
        if len(m.get("course_code", "")) > 40:
            err("field_too_long", "course_code longer than 40 characters")
        if len(m.get("course_name", "")) > 200:
            err("field_too_long", "course_name longer than 200 characters")
        le = ents.get(m.get("legal_entity_code", ""))
        if m.get("legal_entity_code") and le is None:
            err("unknown_legal_entity", f"legal entity '{m['legal_entity_code']}' not found")
        elif le is not None and scope is not None and le.id not in scope:
            err("scope", f"no import permission for legal entity '{le.code}'")
        category = None
        if m.get("category"):
            category = _CATEGORIES.get(_fold(m["category"]))
            if category is None:
                err("unknown_training_category", f"category '{m['category']}' not recognised (hse, technical, "
                                                 "compliance, soft); no default category is assigned")
        legal_basis = None
        if m.get("legal_basis"):
            lb = m["legal_basis"].strip().upper().replace(" ", "_").replace("-", "_")
            if lb in TRAINING_LEGAL_BASES:
                legal_basis = lb
            else:
                err("unknown_legal_basis", f"legal_basis '{m['legal_basis']}' not recognised -> stored empty "
                                           "(informative field, L-14)", "warning")
        if legal_basis and legal_basis.startswith("ISG_") and category and category != "hse":
            err("legal_basis_category_mismatch", "an ISG_* legal basis on a non-HSE category: not counted as "
                                                 "statutory HSE training", "warning")
        cd = sd = None
        try:
            cd = _parse_date(m.get("completed_on", ""))
        except ValueError as e:
            err("date_format", str(e))
        if cd is not None and cd > today:
            err("future_date", "completed_on is after the import day")
            cd = None
        if cd is not None and date(cd.year, cd.month, 1) in closed:
            err("period_closed", "completed_on falls in a closed period (D-2: only the main contractor can reopen it)")
        try:
            sd = _parse_date(m.get("start_date", ""))
        except ValueError as e:
            err("date_format", str(e))
        if sd is not None and cd is not None and sd > cd:
            err("date_order", "start_date after completed_on")
            sd = None
        hours = None
        if m.get("hours"):
            try:
                hours = tsi._hours(m["hours"])
            except ValueError:
                err("hours_format", "'hours' must be a number")
            if hours is not None:
                span = (cd - sd).days + 1 if sd and cd else None
                if hours <= 0 or hours > MAX_HOURS:
                    err("hours_range", "'hours' must be greater than 0")
                    hours = None
                elif span is not None and hours > 24 * span:
                    err("hours_range", f"'hours' above 24 h x {span} day(s) between start_date and completed_on")
                    hours = None
                elif span is None and hours > 24:
                    err("hours_exceed_single_day", "more than 24 h without a start_date -> loaded; give the start "
                                                   "date of multi-day trainings", "warning")
        delivery = None
        if m.get("delivery"):
            delivery = _DELIVERY.get(_fold(m["delivery"]))
            if delivery is None:
                err("unknown_delivery", "delivery not recognised (classroom, online, on_the_job) -> stored empty",
                    "warning")
        mandatory = False
        if m.get("mandatory"):
            b = parse_bool(m["mandatory"])
            if b is None:
                err("bool_format", "mandatory must be yes / no")
            else:
                mandatory = b
        store = True
        if status_mapped:
            if not m.get("status"):
                store = False
                err("completion_unknown", "empty status -> not stored (only completed trainings count; no default "
                                          "status is assigned)", "warning")
            else:
                st = _STATUSES.get(_fold(m["status"]))
                if st is None:
                    err("status_unknown", "status not recognised (completed, attended, failed, cancelled)")
                elif st != "completed":
                    store = False
                    err("training_not_completed", f"status '{st}' -> not stored (only completed trainings count)",
                        "warning")
        emp = None
        if le is not None and cd is not None and m.get("external_id") and not [e for e in errs if e["rule"] == "scope"]:
            found = resolve(src, m["external_id"], le.id, cd)
            if not found:
                err("no_employment", f"no employment of '{m['external_id']}' with '{le.code}' on {cd.isoformat()}")
            elif len(found) > 1:
                err("ambiguous_employment", "several employments match; resolve the identity first")
            else:
                emp = found[0]
        mp = {"external_id": m.get("external_id"), "legal_entity_code": m.get("legal_entity_code"),
              "legal_entity_id": le.id if le else None, "employment_id": emp.id if emp else None,
              "source_system": src, "course_code": m.get("course_code"), "course_name": m.get("course_name"),
              "category": category, "legal_basis": legal_basis, "hours": hours,
              "completed_on": cd.isoformat() if cd else None, "training_start": sd.isoformat() if sd else None,
              "delivery": delivery, "mandatory": mandatory, "store": store,
              "is_statutory_hse": is_statutory_hse(category, legal_basis)}
        parsed.append((row, mp, errs))

    emp_ids = {mp["employment_id"] for _, mp, _ in parsed if mp["employment_id"]}
    existing: dict[tuple, TrainingRecord] = {}
    if emp_ids:
        for t in db.execute(select(TrainingRecord).where(TrainingRecord.tenant_id == ctx.tenant_id,
                                                         TrainingRecord.employment_id.in_(emp_ids))
                            .order_by(TrainingRecord.id)).scalars():
            existing.setdefault((t.employment_id, t.course_code, t.completed_on.isoformat()), t)
    seen: set[tuple] = set()
    for _, mp, errs in parsed:
        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        mp["existing_id"], mp["replace"] = None, False
        if mp["legal_entity_id"] is not None and batch.coverage and mp["completed_on"]:
            cd = date.fromisoformat(mp["completed_on"])
            if cov.row_outside(batch.coverage, mp["legal_entity_id"], cd, cd):
                err(OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, "warning")
        if not mp["employment_id"] or not mp["completed_on"] or not mp["course_code"] or not mp["store"]:
            continue
        key = (mp["employment_id"], mp["course_code"], mp["completed_on"])
        if key in seen:
            err("duplicate_in_file", "duplicate row (same employee, course code and completion date) in file")
            continue
        seen.add(key)
        ex = existing.get(key)
        if ex is None or [e for e in errs if e["severity"] == "error"]:
            continue
        mp["existing_id"] = ex.id
        changed = [f for f in _COMPARED if _stored(ex, f) != _new(mp, f)]
        if not changed:
            err("already_imported", "identical training record already stored -> will be skipped", "warning")
        elif opts["mode"] == "replace":
            mp["replace"] = True
            err("training_corrected", "stored record differs -> corrected in place on commit (mode = replace; "
                                      "fields: " + ", ".join(changed) + ")", "warning")
        else:
            err("already_imported_changed", "a record for this employee, course and completion date is already "
                                            "stored with other values -> skipped; upload the corrected file with "
                                            "mode = replace", "warning")

    valid = errors = 0
    issues = []
    for row, mp, errs in parsed:
        row.mapped, row.errors = mp, errs
        row.has_error = any(e["severity"] == "error" for e in errs)
        errors += row.has_error
        valid += not row.has_error
        for e in errs:
            issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "training",
                           "rule_code": e["rule"], "severity": e["severity"], "message": e["message"],
                           "row_number": row.row_number, "record_ref": mp.get("external_id")})
    if batch.dropped_columns:
        issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "training",
                       "rule_code": "prohibited_columns_dropped", "severity": "warning", "row_number": None,
                       "record_ref": None, "message": "name / identity / health column(s) dropped before storage "
                                                      "(names only, values never stored): "
                                                      + ", ".join(batch.dropped_columns)})
    if issues:
        db.bulk_insert_mappings(DataQualityIssue, issues)
    batch.options = opts
    batch.valid_rows, batch.error_rows, batch.status = valid, errors, "validated"
    db.commit()
    return summary(db, ctx, batch.id)


def _stored(t: TrainingRecord, f: str):
    v = getattr(t, f)
    if f == "hours":
        return tsi._num(v)
    if f == "start_date":
        return v.isoformat() if v else None
    if f == "mandatory":
        return bool(v)
    return v


def _new(mp: dict, f: str):
    return mp["training_start"] if f == "start_date" else mp[f]


def commit_row(db: Session, ctx: AccessContext, row: ImportStagingRow) -> date | None:
    """Insert a completed training, or (mode = replace, decided at validation) correct the stored record in place
    with record_version + 1 and an audit row. Skipped / identical rows return None. Never deletes."""
    from app.services.imports import ImportError_

    mp = row.mapped
    if not mp.get("store"):
        return None
    cd = date.fromisoformat(mp["completed_on"])
    emp = db.get(EmploymentRelationship, mp["employment_id"])
    if emp is None or emp.tenant_id != ctx.tenant_id:
        raise ImportError_("employment disappeared between validation and commit")
    ex = db.execute(select(TrainingRecord).where(
        TrainingRecord.employment_id == emp.id, TrainingRecord.course_code == mp["course_code"],
        TrainingRecord.completed_on == cd).order_by(TrainingRecord.id).limit(1)).scalar()
    values = {"course_name": mp["course_name"], "category": mp["category"], "hours": mp["hours"],
              "legal_basis": mp["legal_basis"], "delivery": mp["delivery"], "mandatory": mp["mandatory"],
              "start_date": date.fromisoformat(mp["training_start"]) if mp["training_start"] else None}
    if ex is None:
        db.add(TrainingRecord(tenant_id=ctx.tenant_id, employment_id=emp.id, course_code=mp["course_code"],
                              completed_on=cd, source_system=mp["source_system"], source_record_id=mp["external_id"],
                              updated_by=ctx.user_id, **values))
        return cd
    changed = {f: (_stored(ex, f), _new(mp, f)) for f in _COMPARED if _stored(ex, f) != _new(mp, f)}
    if not changed or not mp.get("replace"):
        return None  # identical (re-import / resumed commit) or a change without mode = replace
    for f in changed:
        setattr(ex, f, values[f])
    ex.record_version = (ex.record_version or 1) + 1
    ex.updated_by = ctx.user_id
    db.add(AuditLog(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=TRAINING_CORRECT_ACTION,
                    entity_type="training_record", entity_id=str(ex.id),
                    details={"legal_entity_id": emp.legal_entity_id, "completed_on": cd.isoformat(),
                             "course_code": ex.course_code, "import_batch_id": row.batch_id,
                             "record_version": ex.record_version,
                             "changed": {f: {"from": a, "to": b} for f, (a, b) in changed.items()}}))
    return cd
