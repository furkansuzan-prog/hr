"""Data import pipeline (ADR-012): upload -> preview (+mapping suggestion) -> validate -> commit.

- Idempotent: the same file (sha256) can be committed only once per tenant/entity type.
- Resumable: commit processes rows in chunks and marks each staged row committed; a failed commit can be retried
  and continues where it stopped without creating duplicates.
- Raw rows (import_staging_rows.raw) are kept separate from transformed data (mapped).
- Identity matching: (source_system, external_id) -> national-id HMAC; ambiguous matches go to manual review.
"""
import csv
import hashlib
import io
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

from openpyxl import load_workbook
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.analytics.engine import mark_stale_from
from app.core.config import get_settings
from app.models import (
    LEAVE_RECLASSIFY_ACTION,
    SICK_NOTE_FAMILY,
    Assignment,
    AuditLog,
    DataQualityIssue,
    EmploymentRelationship,
    IdentityReviewItem,
    ImportBatch,
    ImportStagingRow,
    LeaveRecord,
    LegalEntity,
    Occupation,
    Person,
    PersonIdentifier,
    Project,
    TerminationCounter,
)
from app.models.people import (
    CONTRACT_TYPES,
    PLANNED_END_CATEGORIES,
    PLANNED_END_CONTRACT_TYPES,
    TERMINATION_CATEGORIES,
)
from app.security.access import AccessContext
from app.security.auth import national_id_hash
from app.services import coverage as cov
from app.services import exit_codes as xc
from app.services import safety_import as sfi
from app.services import timesheet_import as tsi
from app.services.timekeeping import classify_leave

log = logging.getLogger("hrswi.imports")


class ImportError_(Exception):
    status_code = 400


class DuplicateFile(ImportError_):
    status_code = 409

    def __init__(self, batch_id: int):
        super().__init__(f"This file was already committed (batch {batch_id})")
        self.batch_id = batch_id


# Target schema for entity_type = "employment" (one row = person + one employment spell)
FIELDS = {
    "external_id": {"required": True, "aliases": ["sicil", "personel no", "employee id", "id", "табельный номер"]},
    "source_system": {"required": False, "aliases": ["kaynak", "source", "система"]},
    "first_name": {"required": True, "aliases": ["ad", "adı", "first name", "имя"]},
    "last_name": {"required": True, "aliases": ["soyad", "soyadı", "last name", "surname", "фамилия"]},
    "national_id": {"required": False, "aliases": ["tc", "tckn", "tc kimlik", "national id", "паспорт", "инн"]},
    "birth_year": {"required": False, "aliases": ["doğum yılı", "birth year", "год рождения"]},
    "gender": {"required": False, "aliases": ["cinsiyet", "gender", "пол"]},
    "legal_entity_code": {"required": True, "aliases": ["firma", "firma kodu", "şirket", "company", "employer", "компания"]},
    "employee_number": {"required": False, "aliases": ["sicil no", "employee number", "номер"]},
    "contract_type": {"required": False, "aliases": ["sözleşme", "sözleşme tipi", "contract", "договор"]},
    "start_date": {"required": True, "aliases": ["işe giriş", "giriş tarihi", "start", "hire date", "дата приема"]},
    "end_date": {"required": False, "aliases": ["çıkış", "çıkış tarihi", "end", "termination date", "дата увольнения"]},
    "termination_category": {"required": False, "aliases": ["çıkış nedeni", "ayrılış türü", "termination reason", "причина"]},
    "project_code": {"required": False, "aliases": ["proje", "project", "проект"]},
    "occupation_code": {"required": False, "aliases": ["meslek", "meslek kodu", "occupation", "профессия"]},
    # ADR-023 / WP-5 3.1: SGK exit code (recommended when end_date is set) and the contractual planned end
    "sgk_exit_code": {"required": False, "aliases": ["sgk çıkış kodu", "işten çıkış kodu", "çıkış kodu", "ayrılış kodu",
                                                     "sgk exit code", "exit code", "код увольнения"]},
    "planned_end_date": {"required": False, "aliases": ["sözleşme bitiş", "sözleşme bitiş tarihi", "planlanan bitiş",
                                                        "planned end", "planned end date", "contract end date",
                                                        "дата окончания договора"]},
}
# Target schema for entity_type = "leave" (one row = one leave/absence record of one employee) - B-07/B-27
LEAVE_FIELDS = {
    "external_id": {"required": True, "aliases": ["sicil", "sicil no", "personel no", "employee id", "employee number",
                                                   "табельный номер"]},
    "source_system": {"required": False, "aliases": ["kaynak", "source", "система"]},
    "legal_entity_code": {"required": True, "aliases": ["firma", "firma kodu", "şirket", "company", "employer",
                                                        "компания"]},
    "leave_type": {"required": True, "aliases": ["izin türü", "izin tipi", "izin", "tür", "leave type", "type",
                                                 "absence type", "вид отпуска", "тип"]},
    "sgk_report_code": {"required": False, "aliases": ["rapor türü", "sgk rapor kodu", "istirahat türü", "rapor kodu",
                                                       "report type"]},
    "start_date": {"required": True, "aliases": ["başlangıç", "başlangıç tarihi", "izin başlangıç", "start", "from",
                                                 "начало"]},
    "end_date": {"required": True, "aliases": ["bitiş", "bitiş tarihi", "izin bitiş", "end", "to", "окончание"]},
    "hours": {"required": False, "aliases": ["saat", "toplam saat", "hours", "часы"]},
    "days": {"required": False, "aliases": ["gün", "gün sayısı", "days", "дни"]},
    # ADR-019: the source's own leave / report number - identity key for re-imports (reclassification in place)
    "record_id": {"required": False, "aliases": ["rapor no", "rapor numarası", "izin no", "belge no", "kayıt no",
                                                 "record id", "report number", "leave id", "document number",
                                                 "номер документа", "номер больничного"]},
}
# ADR-021: HSE cases (one row = one affected person's outcome, no identity) - app.services.safety_import
# ADR-022: timesheets (one row = one employee x day x project) - app.services.timesheet_import
FIELD_SETS = {"employment": FIELDS, "leave": LEAVE_FIELDS, "safety_event": sfi.SAFETY_FIELDS,
              "timesheet": tsi.TIMESHEET_FIELDS}
LEAVE_HOURS_PER_DAY = 9  # 45 h / 5 days; work-pattern specific conversion is backlog B-28
# KVKK md.6 (data minimisation): diagnosis / medical detail is never needed for workforce analytics. Columns that
# look like it are dropped BEFORE anything is stored or echoed back.
_HEALTH_COLUMN_HINTS = ("tanı", "tani", "teşhis", "teshis", "diagnos", "icd", "hastalık adı", "hastalik adi",
                        "диагноз", "medical detail")

MAX_ROWS = 200_000
OUTSIDE_COVERAGE = "row_outside_declared_coverage"
OUTSIDE_COVERAGE_MSG = ("row's legal entity / dates are outside the coverage declared with the upload -> loaded, but it "
                        "creates no coverage")
PLANNED_END_TOLERANCE_DAYS = 7  # WP-5 3.3 C4: an exit up to 7 days before the planned end still counts as planned


def _norm(h: str) -> str:
    """Header normalisation that is safe for Turkish (İ/ı) and other diacritics: 'İşe Giriş' == 'ise giris'."""
    h = (h or "").strip().replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    h = "".join(c for c in unicodedata.normalize("NFKD", h) if not unicodedata.combining(c))
    return re.sub(r"[\s_\-]+", " ", h)


def suggest_mapping(headers: list[str], fields: dict | None = None) -> dict[str, str | None]:
    """target field -> source header"""
    out: dict[str, str | None] = {}
    normed = {_norm(h): h for h in headers}
    for f, meta in (fields or FIELDS).items():
        cands = [_norm(f), _norm(f.replace("_", " "))] + [_norm(a) for a in meta["aliases"]]
        out[f] = next((normed[c] for c in cands if c in normed), None)
    return out


def parse_file(filename: str, content: bytes) -> tuple[list[str], list[dict]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception:
            raise ImportError_("File is not a valid .xlsx workbook") from None
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(it)]
        rows = []
        for r in it:
            if r is None or all(v is None or str(v).strip() == "" for v in r):
                continue
            rows.append({headers[i]: _cell(v) for i, v in enumerate(r) if i < len(headers)})
        return headers, rows
    if name.endswith((".csv", ".txt")):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1254")  # Turkish Excel default; cp1254 decodes any byte sequence
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        return [h.strip() for h in (reader.fieldnames or [])], [{k.strip(): (v or "").strip() for k, v in r.items() if k}
                                                               for r in reader]
    raise ImportError_("Unsupported file type (use .csv or .xlsx)")


def _cell(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return "" if v is None else str(v).strip()


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date '{s}'")


def _coverage_decl(db: Session, ctx: AccessContext, entity_type: str, coverage: dict | None) -> dict | None:
    """B-20 / ADR-020: optional coverage declaration of the upload, validated before anything is stored."""
    if coverage is None:
        return None
    try:
        return cov.normalize(db, ctx, cov.IMPORT_FEEDS[entity_type], coverage, manual=False)
    except cov.CoverageError as e:
        err = ImportError_(f"Invalid coverage declaration: {e}")
        err.status_code = e.status_code
        raise err from None


def upload(db: Session, ctx: AccessContext, *, entity_type: str, filename: str, content: bytes,
           coverage: dict | None = None, options: dict | None = None) -> dict:
    if entity_type not in FIELD_SETS:
        raise ImportError_(f"Unsupported entity_type '{entity_type}' (use one of: {', '.join(FIELD_SETS)})")
    fields = FIELD_SETS[entity_type]
    if entity_type == "safety_event" and not ctx.has("hse.read"):
        err = ImportError_("HSE case imports require hse.read in addition to import.write")
        err.status_code = 403
        raise err
    decl = _coverage_decl(db, ctx, entity_type, coverage)
    opts = None
    if entity_type == "timesheet":
        try:
            opts = tsi.normalize_options(options)
        except ValueError as e:
            raise ImportError_(f"Invalid upload option: {e}") from None
    elif options and any(v is not None for v in options.values()):
        raise ImportError_("Upload options mode / approved_source are only for entity_type 'timesheet'")
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise ImportError_("File too large")
    sha = hashlib.sha256(content).hexdigest()
    done = db.execute(select(ImportBatch.id).where(ImportBatch.tenant_id == ctx.tenant_id,
                                                   ImportBatch.entity_type == entity_type,
                                                   ImportBatch.file_sha256 == sha,
                                                   ImportBatch.status == "committed")).scalar()
    if done:
        raise DuplicateFile(done)
    headers, rows = parse_file(filename, content)
    if len(rows) > MAX_ROWS:
        raise ImportError_(f"Too many rows ({len(rows)} > {MAX_ROWS}); split the file")
    if not rows and not (cov.IMPORT_FEEDS[entity_type] in cov.EVENT_FEEDS and decl
                         and decl["completeness"] == "complete"):
        # WP-6 3.5 step 2: an empty month is confirmable only for event feeds and only as an explicit 'complete'
        raise ImportError_("The file has no data rows. A header-only file is accepted only for event feeds (leave, "
                           "safety_event) with a coverage declaration 'complete' (confirms that there are no records)")
    dropped: list[str] = []
    if entity_type == "leave":
        dropped = [h for h in headers if any(k in _norm(h) for k in _HEALTH_COLUMN_HINTS)]
    elif entity_type == "safety_event":
        # KVKK md.4/6 (WP-6 HSE 3.9, V17): identity / health / free-text columns never reach storage
        dropped = sfi.prohibited_columns(headers)
        conflicts = sfi.field_alias_conflicts(dropped)
        if conflicts:
            raise ImportError_("Column(s) " + ", ".join(conflicts) + " match the prohibited-column list (identity, "
                               "health or free text) but are named like an import field; rename the column")
    elif entity_type == "timesheet":
        # KVKK md.4 (ADR-022): a timesheet needs the employee number only - name / national id / health dropped
        dropped = tsi.prohibited_columns(headers)
        conflicts = tsi.field_alias_conflicts(dropped)
        if conflicts:
            raise ImportError_("Column(s) " + ", ".join(conflicts) + " match the prohibited-column list (name, "
                               "identity or health) but are named like an import field; rename the column")
    if dropped:
        headers = [h for h in headers if h not in dropped]
        rows = [{k: v for k, v in r.items() if k not in dropped} for r in rows]
    suggested = suggest_mapping(headers, fields)
    # KVKK: identity numbers are pseudonymised BEFORE the raw row is persisted or echoed back in the preview
    _pseudonymise(rows, suggested.get("national_id"))
    if entity_type == "employment":  # ADR-023: health-implying / unmapped exit codes never reach storage either
        xc.ensure_map(db, ctx.tenant_id)
        _stage_exit_codes(xc.CodeMap(db, ctx.tenant_id), rows, suggested)
    batch = ImportBatch(tenant_id=ctx.tenant_id, entity_type=entity_type, file_name=filename[:255], file_sha256=sha,
                        status="uploaded", total_rows=len(rows), created_by=ctx.user_id,
                        mapping=suggested, coverage=decl, dropped_columns=dropped or None, options=opts)
    db.add(batch)
    db.flush()
    chunk = get_settings().import_chunk_size
    for i in range(0, len(rows), chunk):
        db.bulk_insert_mappings(ImportStagingRow, [
            {"batch_id": batch.id, "row_number": i + j + 2, "raw": r} for j, r in enumerate(rows[i:i + chunk])])
    db.commit()
    return {"batch_id": batch.id, "headers": headers, "total_rows": len(rows), "suggested_mapping": batch.mapping,
            "required_fields": [f for f, m in fields.items() if m["required"]], "preview": rows[:20],
            "entity_type": entity_type, "dropped_columns": dropped, "coverage": decl, "options": opts}


_HMAC_PREFIX = "hmac:"


def _pseudonymise(rows: list[dict], column: str | None) -> None:
    if not column:
        return
    for r in rows:
        v = str(r.get(column) or "").strip()
        if v and not v.startswith(_HMAC_PREFIX):
            r[column] = _HMAC_PREFIX + national_id_hash(v)


def _stage_exit_codes(cmap: "xc.CodeMap", rows: list[dict], mapping: dict) -> None:
    """Replace exit codes that may not be kept (health-implying, withheld, not in the table) by a marker in the raw row,
    in place - same moment as the national-id pseudonymisation. The code's validity date is the row's end date."""
    col = mapping.get("sgk_exit_code")
    if not col:
        return
    end_col = mapping.get("end_date")
    for r in rows:
        v = r.get(col)
        if v is None or str(v).strip() == "":
            continue
        try:
            on = _parse_date(str(r.get(end_col) or "")) if end_col else None
        except ValueError:
            on = None
        r[col] = cmap.stage_value(v, on)


def validate(db: Session, ctx: AccessContext, batch_id: int, mapping: dict[str, str | None] | None) -> dict:
    batch = _batch(db, ctx, batch_id)
    if batch.status == "committed":
        raise ImportError_("Batch already committed")
    mapping = mapping or batch.mapping or {}
    fields = FIELD_SETS[batch.entity_type]
    missing = [f for f, m in fields.items() if m["required"] and not mapping.get(f)]
    if missing:
        raise ImportError_(f"Mapping missing required fields: {', '.join(missing)}")
    gone = sorted({v for v in mapping.values() if v and v in (batch.dropped_columns or [])})
    if gone:  # WP-6 HSE review condition 4: never a silent drop of a mapped column
        raise ImportError_("Mapped column(s) " + ", ".join(gone) + " were dropped on upload as prohibited (identity, "
                           "health or free text) and were not stored; rename the column in the source file and upload "
                           "again")
    batch.mapping = mapping
    db.query(DataQualityIssue).filter(DataQualityIssue.import_batch_id == batch.id).delete()
    if batch.entity_type == "leave":
        return _validate_leave(db, ctx, batch, mapping)
    if batch.entity_type == "safety_event":
        return sfi.validate(db, ctx, batch, mapping)
    if batch.entity_type == "timesheet":
        return tsi.validate(db, ctx, batch, mapping)

    import_scope = _import_scope(ctx)
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    projects ={p.code: p.id for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    occs = {o.code: o.id for o in db.execute(select(Occupation).where(Occupation.tenant_id == ctx.tenant_id)).scalars()}
    seen: set[tuple] = set()
    valid = errors = 0
    issues = []
    nid_col = mapping.get("national_id")
    xc.ensure_map(db, ctx.tenant_id)
    cmap = xc.CodeMap(db, ctx.tenant_id)
    staged = list(db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                             .order_by(ImportStagingRow.row_number)).scalars())
    new_spells = _file_spells(staged, mapping)  # code 16 check: spells of the same file
    for row in staged:
        if nid_col and str(row.raw.get(nid_col) or "").strip() and not str(row.raw[nid_col]).startswith(_HMAC_PREFIX):
            # user mapped a different column than suggested: pseudonymise it in the stored raw row too
            raw = dict(row.raw)
            _pseudonymise([raw], nid_col)
            row.raw = raw
        if mapping.get("sgk_exit_code"):
            # user mapped a different column than suggested (or the table changed): no raw code stays behind
            raw = dict(row.raw)
            _stage_exit_codes(cmap, [raw], mapping)
            if raw != row.raw:
                row.raw = raw
        m = {f: (row.raw.get(src, "") if src else "") for f, src in mapping.items()}
        errs: list[dict] = []

        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        for f, meta in FIELDS.items():
            if meta["required"] and not str(m.get(f, "")).strip():
                err("required", f"'{f}' is required")
        le = ents.get(m.get("legal_entity_code", ""))
        if m.get("legal_entity_code") and le is None:
            err("unknown_legal_entity", f"legal entity '{m['legal_entity_code']}' not found")
        elif le is not None and import_scope is not None and le.id not in import_scope:
            err("scope", f"no import permission for legal entity '{le.code}'")
        try:
            sd = _parse_date(m.get("start_date", ""))
            ed = _parse_date(m.get("end_date", ""))
            if sd and ed and ed < sd:
                err("date_order", "end_date before start_date")
            if sd and sd > date.today():
                err("future_start", "start_date is in the future", "warning")
            m["start_date"], m["end_date"] = (sd.isoformat() if sd else None), (ed.isoformat() if ed else None)
        except ValueError as e:
            err("date_format", str(e))
        if le is not None and batch.coverage and not any(e["rule"] == "date_format" for e in errs) and cov.row_outside(
                batch.coverage, le.id, date.fromisoformat(m["start_date"]) if m.get("start_date") else None,
                date.fromisoformat(m["end_date"]) if m.get("end_date") else None):
            err(OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, "warning")
        ct = (m.get("contract_type") or "").strip().lower()
        if not ct:  # WP-5 M7 / ADR-023: missing is never silently 'permanent'
            ct = "unknown"
            err("contract_type_missing", "contract type is blank -> stored as 'unknown' (not assumed permanent)",
                "warning")
        elif ct not in CONTRACT_TYPES:
            err("contract_type", f"unknown contract type '{ct}'")
        m["contract_type"] = ct
        _classify_exit(db, ctx, cmap, m, err, new_spells)
        if m.get("project_code") and m["project_code"] not in projects:
            err("unknown_project", f"project '{m['project_code']}' not found")
        if m.get("occupation_code") and m["occupation_code"] not in occs:
            err("unknown_occupation", f"occupation '{m['occupation_code']}' not found")
        if m.get("birth_year"):
            try:
                y = int(float(m["birth_year"]))
                if not 1930 <= y <= date.today().year - 14:
                    err("birth_year", "implausible birth year")
                m["birth_year"] = y
            except ValueError:
                err("birth_year", "birth_year must be a number")
        key = (m.get("source_system") or "import", m.get("external_id"), m.get("legal_entity_code"), m.get("start_date"))
        if key in seen:
            err("duplicate_in_file", "duplicate row (same person, employer and start date) in file")
        seen.add(key)
        # national id is pseudonymised immediately; raw value never leaves this function
        if m.get("national_id"):
            v = str(m.pop("national_id"))
            m["national_id_hash"] = v[len(_HMAC_PREFIX):] if v.startswith(_HMAC_PREFIX) else national_id_hash(v)
        else:
            m.pop("national_id", None)
        row.mapped = m
        row.errors = errs
        hard = [e for e in errs if e["severity"] == "error"]
        row.has_error = bool(hard)
        if hard:
            errors += 1
        else:
            valid += 1
        for e in errs:
            issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": batch.entity_type,
                           "rule_code": e["rule"], "severity": e["severity"], "message": e["message"],
                           "row_number": row.row_number, "record_ref": m.get("external_id")})
    if issues:
        db.bulk_insert_mappings(DataQualityIssue, issues)
    batch.valid_rows, batch.error_rows, batch.status = valid, errors, "validated"
    db.commit()
    return summary(db, ctx, batch_id)


def _file_spells(staged: list[ImportStagingRow], mapping: dict) -> dict[tuple[str, str], list[date]]:
    """(source_system, external_id) -> start dates of the spells in this file (code 16 needs the follow-up spell)."""
    out: dict[tuple[str, str], list[date]] = {}
    src_col, id_col, sd_col = mapping.get("source_system"), mapping.get("external_id"), mapping.get("start_date")
    for r in staged:
        try:
            sd = _parse_date(str(r.raw.get(sd_col) or "")) if sd_col else None
        except ValueError:
            sd = None
        if sd and id_col:
            src = (str(r.raw.get(src_col) or "").strip() if src_col else "") or "import"
            out.setdefault((src, str(r.raw.get(id_col) or "").strip()), []).append(sd)
    return out


def _has_follow_up_spell(db: Session, ctx: AccessContext, m: dict, ed: date, file_spells: dict) -> bool:
    """A new spell of the same person starting within TRANSFER_GAP_DAYS after `ed` - in the file or in the database
    (any legal entity of the tenant: the same window as the KPI transfer rule)."""
    from app.analytics.calculators import TRANSFER_GAP_DAYS

    lo, hi = ed, ed + timedelta(days=TRANSFER_GAP_DAYS)
    key = ((m.get("source_system") or "").strip() or "import", (m.get("external_id") or "").strip())
    if any(lo < d <= hi for d in file_spells.get(key, ())):
        return True
    person = select(PersonIdentifier.person_id).where(PersonIdentifier.tenant_id == ctx.tenant_id,
                                                      PersonIdentifier.source_system == key[0],
                                                      PersonIdentifier.external_id == key[1])
    return db.execute(select(EmploymentRelationship.id).where(
        EmploymentRelationship.tenant_id == ctx.tenant_id, EmploymentRelationship.person_id.in_(person),
        EmploymentRelationship.start_date > lo, EmploymentRelationship.start_date <= hi).limit(1)).first() is not None


def _classify_exit(db: Session, ctx: AccessContext, cmap: "xc.CodeMap", m: dict, err, file_spells: dict) -> None:
    """WP-5 3.1 / 3.3 + HR Director review (ADR-023): category order (verified SGK code > source category > unknown),
    which code may be stored, planned end date and planned_end_verified. Messages never echo a withheld code."""
    def as_date(k):
        try:
            return date.fromisoformat(m[k]) if m.get(k) else None
        except (ValueError, TypeError):
            return None

    sd, ed = as_date("start_date"), as_date("end_date")
    ped = None
    if m.get("planned_end_date"):
        try:
            ped = _parse_date(m["planned_end_date"])
        except ValueError as e:
            err("date_format", f"planned_end_date: {e}")
        if ped and sd and ped < sd:
            err("planned_end_before_start", "planned_end_date before start_date")
            ped = None
        if ped and m.get("contract_type") == "permanent":
            err("planned_end_on_permanent", "planned_end_date given for a permanent contract", "warning")
    m["planned_end_date"] = ped.isoformat() if ped else None

    raw = str(m.pop("sgk_exit_code", "") or "").strip()
    res: xc.Resolution | None = None
    if xc.is_masked(raw):
        res = xc.unmask(raw)[1]
    elif raw:
        try:
            code = xc.normalize_code(raw)
            res = cmap.resolve(code, ed) if code else None
        except ValueError:
            err("exit_code_format", "SGK exit code must be a 1-2 digit number -> ignored")
    if res is not None and not ed:
        err("exit_code_without_end_date", "SGK exit code without end_date -> ignored", "warning")
        res = None
    if res is not None and not res.known:
        err("unknown_exit_code", "SGK exit code is not in the exit code table -> not stored; category from the source "
            "or 'unknown'", "warning")
    elif res is not None and not res.verified:
        err("exit_code_mapping_unverified", "SGK exit code mapping is not verified yet (L-12) -> category from the "
            "source or 'unknown'", "warning")

    src_cat = (m.get("termination_category") or "").strip().lower()
    if src_cat and src_cat not in TERMINATION_CATEGORIES:
        err("termination_category", f"unknown termination category '{src_cat}'")
        src_cat = ""
    cat = source = None
    if ed:
        if res is not None and res.verified:
            cat = res.category
            if src_cat and src_cat != cat:
                err("category_code_conflict", f"source category '{src_cat}' differs from the SGK exit code's category "
                    f"'{cat}' -> the code wins (statutory notification)", "warning")
        elif src_cat:
            cat = src_cat
        else:
            cat = "unknown"
            err("termination_reason_missing", "end_date without reason -> stored as 'unknown' (not assumed)", "warning")
        source = "sgk" if res is not None else "hris" if src_cat else None
    elif src_cat:
        cat = src_cat  # as before: a category is stored only together with an end date
    m["termination_category"] = cat
    m["termination_reason_code"] = res.code if res is not None else None
    m["termination_reason_source"] = source
    m["_exit_counter"] = res.counter if res is not None else None
    if res is not None and res.code == xc.TRANSFER_CODE and not _has_follow_up_spell(db, ctx, m, ed, file_spells):
        err("transfer_without_new_spell", "exit code 16 (transfer) but no new spell of the person within 7 days",
            "warning")

    # WP-5 3.3: C1 category, C2 contract type, C3 code eligibility, C4 planned end date (-7 days)
    pev = None
    if ed:
        if cat not in PLANNED_END_CATEGORIES:
            pev = False
        else:
            ct = m.get("contract_type")
            fails = []
            if ct == "permanent":
                fails.append(("contract_end_on_permanent", "contract/project end on a permanent contract -> not a "
                              "planned end (counted as an early leaver)"))
            if res is not None and res.verified and res.planned_end_eligible is False:
                fails.append(("exit_code_not_planned_end", "the SGK exit code is not a planned-end code"))
            if ped and ed < ped - timedelta(days=PLANNED_END_TOLERANCE_DAYS):
                fails.append(("planned_end_mismatch", "employment ended more than 7 days before the planned end date "
                              "-> not a planned end"))
            for rule, msg in fails:
                err(rule, msg, "warning")
            if fails:
                pev = False
            elif ct in PLANNED_END_CONTRACT_TYPES and (res is None or res.verified):
                pev = True
            else:  # contract type unknown, or a code that cannot be confirmed yet -> never excluded
                err("planned_end_unverifiable", "planned end cannot be verified (contract type unknown or exit code "
                    "mapping not verified) -> counted as an early leaver", "warning")
    m["planned_end_verified"] = pev


def commit(db: Session, ctx: AccessContext, batch_id: int, *, fail_after_rows: int | None = None) -> dict:
    """fail_after_rows is a test hook simulating a crash mid-import (scenario 14)."""
    batch = _batch(db, ctx, batch_id)
    if batch.status == "committed":
        return summary(db, ctx, batch_id)
    if batch.status not in ("validated", "failed"):
        raise ImportError_("Batch must be validated before commit")
    batch.attempt_count += 1
    db.commit()
    ents = {le.code: le.id for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    projects = {p.code: p.id for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    occs = {o.code: o.id for o in db.execute(select(Occupation).where(Occupation.tenant_id == ctx.tenant_id)).scalars()}
    chunk = get_settings().import_chunk_size
    processed = 0
    earliest: date | None = None
    try:
        while True:
            rows = list(db.execute(select(ImportStagingRow).where(
                ImportStagingRow.batch_id == batch.id, ImportStagingRow.committed.is_(False),
                ImportStagingRow.has_error.is_(False)).order_by(ImportStagingRow.row_number).limit(chunk)).scalars())
            if not rows:
                break
            for r in rows:
                if fail_after_rows is not None and processed >= fail_after_rows:
                    raise RuntimeError("simulated failure during commit")
                d = (_commit_leave_row(db, ctx, r) if batch.entity_type == "leave"
                     else sfi.commit_row(db, ctx, r) if batch.entity_type == "safety_event"
                     else tsi.commit_row(db, ctx, r) if batch.entity_type == "timesheet"
                     else _commit_row(db, ctx, batch, r, ents, projects, occs))
                if d == "review":  # sent to manual identity review: not committed, not retried automatically
                    r.has_error = True
                    r.errors = [*(r.errors or []), {"rule": "identity_review", "severity": "error",
                                                    "message": "sent to manual identity review"}]
                    processed += 1
                    continue
                if d and (earliest is None or d < earliest):
                    earliest = d
                r.committed = True
                processed += 1
            db.commit()  # chunk boundary = durable progress
        total_ok = db.execute(select(func.count()).select_from(ImportStagingRow).where(
            ImportStagingRow.batch_id == batch.id, ImportStagingRow.committed.is_(True))).scalar()
        batch.committed_rows = total_ok
        batch.status = "committed"
        batch.error_message = None
        _invalidate_from_committed(db, ctx, batch.id)
        cov.record_import(db, ctx, batch)  # B-20: coverage rows in the same transaction as the status change
        if batch.entity_type in ("safety_event", "timesheet"):
            sfi.minimise_raw(db, batch)
        elif batch.entity_type == "employment":
            _minimise_exit_codes(db, batch)
        db.commit()
    except Exception as exc:
        db.rollback()
        b = db.get(ImportBatch, batch_id)
        b.status = "failed"
        b.error_message = str(exc)[:1000]  # internal detail stays server-side (batch record + logs)
        # chunks committed before the failure already changed data: invalidate now, not only on final success
        _invalidate_from_committed(db, ctx, batch_id)
        db.commit()
        log.warning("import commit failed", extra={"tenant_id": ctx.tenant_id})
        raise ImportError_("Commit failed; committed chunks are kept and the batch can be retried") from exc
    return summary(db, ctx, batch_id)


def _working_days(sd: date, ed: date) -> int:
    return sum(1 for i in range((ed - sd).days + 1) if (sd + timedelta(days=i)).weekday() < 5)


def _resolve_employment(db: Session, ctx: AccessContext, source: str, external_id: str, entity_id: int,
                        on: date) -> tuple[list[EmploymentRelationship], str]:
    """Employment of the person at `on` with the given employer: via the identity map (source system + external id),
    falling back to the employer's employee number (sicil no)."""
    by_identity = select(EmploymentRelationship).join(
        PersonIdentifier, PersonIdentifier.person_id == EmploymentRelationship.person_id).where(
        PersonIdentifier.tenant_id == ctx.tenant_id, PersonIdentifier.source_system == source,
        PersonIdentifier.external_id == external_id)
    by_number = select(EmploymentRelationship).where(EmploymentRelationship.tenant_id == ctx.tenant_id,
                                                     EmploymentRelationship.employee_number == external_id)
    for how, q in (("identity", by_identity), ("employee_number", by_number)):
        found = list(db.execute(q.where(
            EmploymentRelationship.legal_entity_id == entity_id, EmploymentRelationship.start_date <= on,
            or_(EmploymentRelationship.end_date.is_(None), EmploymentRelationship.end_date >= on))).scalars())
        if found:
            return found, how
    return [], "none"


def _import_scope(ctx: AccessContext) -> set[int] | None:
    """Entities the caller may import into: whole-entity grants only (ADR-017 - an entity visible only for some
    projects is not writable). None = tenant-wide."""
    s = ctx.entity_filter("import.write")
    return None if s is None else {e for e in s if ctx.covers_entity("import.write", e)}


def _validate_leave(db: Session, ctx: AccessContext, batch: ImportBatch, mapping: dict) -> dict:
    """Leave rows: resolve the employment, map the source label through normalize_leave_type (protected leave first,
    decision D-6), derive hours, flag unmapped labels (B-27), detect duplicates in file and in the database."""
    import_scope = _import_scope(ctx)
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    seen: set[tuple] = set()
    fam_spans: dict[tuple, list[tuple[date, date]]] = {}
    valid = errors = 0
    issues = []
    for row in db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                          .order_by(ImportStagingRow.row_number)).scalars():
        m = {f: (str(row.raw.get(src, "") or "").strip() if src else "") for f, src in mapping.items()}
        errs: list[dict] = []

        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        for f, meta in LEAVE_FIELDS.items():
            if meta["required"] and not m.get(f):
                err("required", f"'{f}' is required")
        le = ents.get(m.get("legal_entity_code", ""))
        if m.get("legal_entity_code") and le is None:
            err("unknown_legal_entity", f"legal entity '{m['legal_entity_code']}' not found")
        elif le is not None and import_scope is not None and le.id not in import_scope:
            err("scope", f"no import permission for legal entity '{le.code}'")
        sd = ed = None
        try:
            sd, ed = _parse_date(m.get("start_date", "")), _parse_date(m.get("end_date", ""))
            if sd and ed and ed < sd:
                err("date_order", "end_date before start_date")
                sd = ed = None
        except ValueError as e:
            err("date_format", str(e))
        if le is not None and cov.row_outside(batch.coverage, le.id, sd, ed):
            err(OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, "warning")
        label = m.get("leave_type", "")
        lt, label_warnings = classify_leave(label, m.get("sgk_report_code") or None)
        for w in label_warnings:  # KVKK md.6 (WP-1 3.6): the health-related label is not echoed into the queue
            err(w, _LABEL_WARNING_MESSAGES[w], "warning")
        if lt == "other" and _norm(label) != "other":
            err("unmapped_leave_label", f"leave label '{label}' not recognised -> counted as 'other' (review mapping)",
                "warning")
        hours = None
        try:
            if m.get("hours"):
                hours = float(m["hours"].replace(",", "."))
            elif m.get("days"):
                hours = float(m["days"].replace(",", ".")) * LEAVE_HOURS_PER_DAY
            elif sd and ed:
                hours = _working_days(sd, ed) * LEAVE_HOURS_PER_DAY
                err("hours_derived", f"hours derived from Mon-Fri working days x {LEAVE_HOURS_PER_DAY}", "warning")
            if hours is not None and not 0 < hours <= 24 * ((ed - sd).days + 1 if sd and ed else 1):
                err("hours_range", "hours must be positive and not exceed 24 h per calendar day")
        except ValueError:
            err("hours_format", "hours/days must be numbers")
        emp, how = None, None
        if le is not None and sd and not [e for e in errs if e["rule"] == "scope"]:
            found, how = _resolve_employment(db, ctx, m.get("source_system") or "import", m.get("external_id", ""),
                                             le.id, sd)
            if not found:
                err("no_employment", f"no employment of '{m.get('external_id')}' with '{le.code}' on {sd.isoformat()}")
            elif len(found) > 1:
                err("ambiguous_employment", "several employments match; resolve the identity first")
            else:
                emp = found[0]
                if ed and emp.end_date and ed > emp.end_date:
                    err("past_employment_end", "leave ends after the employment end date -> clipped", "warning")
                    ed = emp.end_date
        src, rid = m.get("source_system") or "import", m.get("record_id") or None
        reclass: LeaveRecord | None = None
        if emp is not None and sd and ed:
            reclass = _match_existing_leave(db, emp.id, src, rid, lt, sd, ed, err)
        # in-file checks: one certified-rest record per employee and dates, whatever its type (WP-1 3.4)
        person = (src, m.get("external_id"), m.get("legal_entity_code"))
        key = (*person, "sick_note" if lt in SICK_NOTE_FAMILY else lt, sd)
        if key in seen or (rid and (*person, "record_id", rid) in seen):
            err("duplicate_in_file", "duplicate row (same employee, employer, leave type and start date)")
        elif lt in SICK_NOTE_FAMILY and sd and ed and any(a <= ed and b >= sd for a, b in fam_spans.get(person, ())):
            err(SICK_NOTE_OVERLAP, "certified rest (sick / work injury) overlaps another row of this employee in the "
                                   "file -> not committed (manual review)")
        seen.add(key)
        if rid:
            seen.add((*person, "record_id", rid))
        if lt in SICK_NOTE_FAMILY and sd and ed:
            fam_spans.setdefault(person, []).append((sd, ed))
        row.mapped = {"external_id": m.get("external_id"), "legal_entity_code": m.get("legal_entity_code"),
                      "employment_id": emp.id if emp else None, "leave_type": lt,
                      "start_date": sd.isoformat() if sd else None, "end_date": ed.isoformat() if ed else None,
                      "hours": hours, "source_system": src, "matched_by": how, "record_id": rid,
                      "reclassify_id": reclass.id if reclass is not None else None,
                      "reclassify_from": reclass.leave_type if reclass is not None else None}
        row.errors = errs
        hard = [e for e in errs if e["severity"] == "error"]
        row.has_error = bool(hard)
        if hard:
            errors += 1
        else:
            valid += 1
        for e in errs:
            issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "leave",
                           "rule_code": e["rule"], "severity": e["severity"], "message": e["message"],
                           "row_number": row.row_number, "record_ref": m.get("external_id")})
    if issues:
        db.bulk_insert_mappings(DataQualityIssue, issues)
    batch.valid_rows, batch.error_rows, batch.status = valid, errors, "validated"
    db.commit()
    return summary(db, ctx, batch.id)


def _commit_leave_row(db: Session, ctx: AccessContext, row: ImportStagingRow) -> date | None:
    m = row.mapped
    sd, ed = date.fromisoformat(m["start_date"]), date.fromisoformat(m["end_date"])
    emp = db.get(EmploymentRelationship, m["employment_id"])
    if emp is None or emp.tenant_id != ctx.tenant_id:
        raise ImportError_("employment disappeared between validation and commit")
    if m.get("reclassify_id"):
        return _reclassify_leave(db, ctx, row, emp, sd)
    if db.execute(select(LeaveRecord.id).where(
            LeaveRecord.employment_id == emp.id, LeaveRecord.leave_type == m["leave_type"],
            LeaveRecord.start_date == sd, LeaveRecord.end_date == ed)).first():
        return None  # idempotent: identical record already present (re-import / resumed commit)
    db.add(LeaveRecord(tenant_id=ctx.tenant_id, employment_id=emp.id, leave_type=m["leave_type"], start_date=sd,
                       end_date=ed, hours=m["hours"], source_system=m["source_system"],
                       source_record_id=m.get("record_id") or m["external_id"], updated_by=ctx.user_id))
    return sd


# ---------------------------------------------------------------- leave reclassification (WP-1 3.4, ADR-019)
SICK_NOTE_OVERLAP = "sick_note_overlap"
_LABEL_WARNING_MESSAGES = {
    "leave_label_code_conflict": "leave label and SGK report type code disagree -> the report code is used "
                                 "(health-related leave label; HSE review)",
    "leave_label_ambiguous": "leave label names both protected leave and a work injury -> kept as protected leave "
                             "(decision D-6; HR review)",
    "unmapped_sgk_report_code": "SGK report type code not recognised -> mapped from the label (review the code list)",
}


def _direction(old: str, new: str) -> str:
    """Rule codes shared with the HSE import (WP-1 review condition 5)."""
    if "parental" in (old, new):
        return "leave_reclassified_protected"
    if (old, new) == ("sick", "work_injury"):
        return "reclassified_up"
    if (old, new) == ("work_injury", "sick"):
        return "reclassified_down"
    return "leave_reclassified"


def _match_existing_leave(db: Session, employment_id: int, src: str, rid: str | None, lt: str, sd: date, ed: date,
                          err) -> LeaveRecord | None:
    """Identity of an incoming leave row against stored records (WP-1 3.4); returns the record to reclassify in place.
    Key: the source's record number when the feed has one; otherwise (employment, start, end) within the certified
    rest family {sick, work_injury}. A changed type on the same key is a reclassification, never a second record.
    Overlaps inside the family are errors; other overlaps stay warnings (unchanged behaviour)."""
    existing = None
    if rid:
        existing = db.execute(select(LeaveRecord).where(
            LeaveRecord.employment_id == employment_id, LeaveRecord.source_system == src,
            LeaveRecord.source_record_id == rid).order_by(LeaveRecord.id).limit(1)).scalar()
        if existing is not None and (existing.start_date, existing.end_date) != (sd, ed):
            err("record_id_dates_changed", "a leave record with this record number exists with other dates -> not "
                                           "committed (manual review)")
            return None
    if existing is None:
        same = list(db.execute(select(LeaveRecord).where(
            LeaveRecord.employment_id == employment_id, LeaveRecord.start_date == sd,
            LeaveRecord.end_date == ed).order_by(LeaveRecord.id)).scalars())
        if any(r.leave_type == lt for r in same):
            err("already_imported", "identical leave record already exists -> will be skipped", "warning")
            return None
        if lt in SICK_NOTE_FAMILY:
            existing = next((r for r in same if r.leave_type in SICK_NOTE_FAMILY), None)
    if existing is not None:
        if existing.leave_type == lt:
            err("already_imported", "identical leave record already exists -> will be skipped", "warning")
            return None
        err(_direction(existing.leave_type, lt), "same leave record arrives with another type -> reclassified in "
                                                 "place on commit (no second record)", "warning")
        return existing
    overlaps = list(db.execute(select(LeaveRecord.leave_type).where(
        LeaveRecord.employment_id == employment_id, LeaveRecord.start_date <= ed,
        LeaveRecord.end_date >= sd)).scalars())
    if lt in SICK_NOTE_FAMILY and any(t in SICK_NOTE_FAMILY for t in overlaps):
        err(SICK_NOTE_OVERLAP, "certified rest (sick / work injury) overlaps an existing one with other dates -> "
                               "not committed (manual review; counting both would double the days)")
    elif overlaps:
        err("overlapping_leave", "overlaps an existing leave record of this employee", "warning")
    return None


def _reclassify_leave(db: Session, ctx: AccessContext, row: ImportStagingRow, emp: EmploymentRelationship,
                      sd: date) -> date | None:
    """In-place type change + record_version + audit row in the same transaction (WP-1 3.4 step 2)."""
    m = row.mapped
    rec = db.get(LeaveRecord, m["reclassify_id"])
    if rec is None or rec.tenant_id != ctx.tenant_id or rec.employment_id != emp.id:
        raise ImportError_("leave record disappeared between validation and commit")
    if rec.leave_type == m["leave_type"]:
        return None  # resumed commit: already applied
    old = rec.leave_type
    rec.leave_type = m["leave_type"]
    rec.record_version = (rec.record_version or 1) + 1
    rec.updated_by = ctx.user_id
    if m.get("record_id"):
        rec.source_record_id = m["record_id"]
    db.add(AuditLog(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=LEAVE_RECLASSIFY_ACTION,
                    entity_type="leave_record", entity_id=str(rec.id),
                    details={"from": old, "to": rec.leave_type, "direction": _direction(old, rec.leave_type),
                             "import_batch_id": row.batch_id, "legal_entity_id": emp.legal_entity_id,
                             "start_date": rec.start_date.isoformat(), "end_date": rec.end_date.isoformat(),
                             "record_version": rec.record_version}))
    return sd


def leave_reclassification_counts(db: Session, ctx: AccessContext, ps: date, pe: date) -> list[dict]:
    """Group-level direction counters per quarter of the reclassification (WP-1 3.4 step 6): reclassified_up
    (sick -> work_injury) and reclassified_down (work_injury -> sick). Counts only - no person, record or date list.
    A legal entity is listed only when its quarter total is >= HRSWI_MIN_GROUP_SIZE; smaller ones appear only in the
    'other_entities' remainder and in the total. Visible scope: whole legal entities readable with timekeeping.read
    (audit rows carry no project, so project-limited grants contribute nothing - ADR-017)."""
    allowed = ctx.entity_filter("timekeeping.read")
    min_n = get_settings().min_group_size
    q = select(AuditLog.at, AuditLog.details).where(
        AuditLog.tenant_id == ctx.tenant_id, AuditLog.action == LEAVE_RECLASSIFY_ACTION,
        func.date(AuditLog.at) >= ps, func.date(AuditLog.at) <= pe)
    buckets: dict[str, dict[int, dict[str, int]]] = {}
    for at, d in db.execute(q):
        d = d or {}
        direction, eid = d.get("direction"), d.get("legal_entity_id")
        if direction not in ("reclassified_up", "reclassified_down") or eid is None:
            continue
        if allowed is not None and not ctx.covers_entity("timekeeping.read", eid):
            continue
        quarter = f"{at.year}-Q{(at.month - 1) // 3 + 1}"
        c = buckets.setdefault(quarter, {}).setdefault(eid, {"reclassified_up": 0, "reclassified_down": 0})
        c[direction] += 1
    out = []
    for quarter in sorted(buckets):
        per = buckets[quarter]
        total = {k: sum(c[k] for c in per.values()) for k in ("reclassified_up", "reclassified_down")}
        shown = [{"legal_entity_id": e, **c} for e, c in sorted(per.items()) if sum(c.values()) >= min_n]
        hidden = [c for c in per.values() if sum(c.values()) < min_n]
        out.append({"quarter": quarter, "total": total, "by_legal_entity": shown,
                    "other_entities": {k: sum(c[k] for c in hidden) for k in total} if hidden else None})
    return out


def _invalidate_from_committed(db: Session, ctx: AccessContext, batch_id: int) -> None:
    """Earliest affected date across ALL committed rows of the batch (any attempt) -> stale marks + cache bump."""
    dates = []
    for (m,) in db.execute(select(ImportStagingRow.mapped).where(ImportStagingRow.batch_id == batch_id,
                                                                 ImportStagingRow.committed.is_(True))):
        for k in ("start_date", "end_date", "event_date", "work_date"):
            if m and m.get(k):
                dates.append(date.fromisoformat(m[k]))
    if dates:
        mark_stale_from(db, ctx.tenant_id, min(dates))


def _commit_row(db, ctx, batch, row, ents, projects, occs) -> date | None:
    m = row.mapped
    src = m.get("source_system") or "import"
    ident = db.execute(select(PersonIdentifier).where(PersonIdentifier.tenant_id == ctx.tenant_id,
                                                      PersonIdentifier.source_system == src,
                                                      PersonIdentifier.external_id == m["external_id"])).scalar()
    person = db.get(Person, ident.person_id) if ident else None
    if person is not None and m.get("national_id_hash") and person.national_id_hash and \
            person.national_id_hash != m["national_id_hash"]:
        _review(db, ctx, batch, row, person.id, "external id matches a person with a different national id")
        return "review"
    if person is None and m.get("national_id_hash"):
        cands = list(db.execute(select(Person).where(Person.tenant_id == ctx.tenant_id,
                                                     Person.national_id_hash == m["national_id_hash"])).scalars())
        if len(cands) > 1:
            _review(db, ctx, batch, row, None, "multiple persons share this national id hash")
            return "review"
        if len(cands) == 1:
            c = cands[0]
            if c.last_name.strip().lower() != m["last_name"].strip().lower():
                _review(db, ctx, batch, row, c.id, "national id matches but last name differs")
                return "review"
            person = c
    if person is None:
        person = Person(tenant_id=ctx.tenant_id, person_code=f"{src}-{m['external_id']}"[:40],
                        first_name=m["first_name"], last_name=m["last_name"],
                        national_id_hash=m.get("national_id_hash"), birth_year=m.get("birth_year") or None,
                        gender=(m.get("gender") or None), source_system=src, source_record_id=m["external_id"],
                        updated_by=ctx.user_id)
        db.add(person)
        db.flush()
    if ident is None:
        db.add(PersonIdentifier(tenant_id=ctx.tenant_id, person_id=person.id, source_system=src,
                                external_id=m["external_id"]))
    le_id = ents[m["legal_entity_code"]]
    sd = date.fromisoformat(m["start_date"])
    ed = date.fromisoformat(m["end_date"]) if m.get("end_date") else None
    emp = db.execute(select(EmploymentRelationship).where(
        EmploymentRelationship.person_id == person.id, EmploymentRelationship.legal_entity_id == le_id,
        EmploymentRelationship.start_date == sd)).scalar()
    exit_fields = _exit_fields(m, ed)
    ped = date.fromisoformat(m["planned_end_date"]) if m.get("planned_end_date") else None
    if emp is None:
        emp = EmploymentRelationship(tenant_id=ctx.tenant_id, person_id=person.id, legal_entity_id=le_id,
                                     employee_number=m.get("employee_number") or None,
                                     contract_type=m.get("contract_type") or "unknown", start_date=sd, end_date=ed,
                                     planned_end_date=ped, source_system=src, source_record_id=m["external_id"],
                                     updated_by=ctx.user_id, **exit_fields)
        db.add(emp)
        db.flush()
        if m.get("project_code") or m.get("occupation_code"):
            db.add(Assignment(tenant_id=ctx.tenant_id, employment_id=emp.id, project_id=projects.get(m.get("project_code")),
                              occupation_id=occs.get(m.get("occupation_code")), valid_from=sd,
                              valid_to=(ed.fromordinal(ed.toordinal() + 1) if ed else None)))
        if ed:
            _count_exit(db, ctx, le_id, ed, m.get("_exit_counter"))
        return sd
    # existing spell: back-dated termination is an update of end date (versioned), never a delete. ADR-023: a re-import
    # that only adds / corrects the exit classification (e.g. the SGK code arriving later) updates it in place too.
    changed = {k: v for k, v in exit_fields.items() if getattr(emp, k) != v}
    if ped and emp.planned_end_date != ped:
        changed["planned_end_date"] = ped
    if ed and (emp.end_date != ed or changed):
        new_end = emp.end_date != ed
        emp.end_date = ed
        for k, v in changed.items():
            setattr(emp, k, v)
        emp.record_version += 1
        emp.updated_by = ctx.user_id
        if new_end:  # counted once per exit date (an unchanged re-import never counts again)
            _count_exit(db, ctx, le_id, ed, m.get("_exit_counter"))
        return ed
    return None


def _exit_fields(m: dict, ed: date | None) -> dict:
    if not ed:
        return {"termination_category": None, "termination_reason_code": None, "termination_reason_source": None,
                "planned_end_verified": None}
    return {"termination_category": m.get("termination_category") or "unknown",
            "termination_reason_code": m.get("termination_reason_code"),
            "termination_reason_source": m.get("termination_reason_source"),
            "planned_end_verified": m.get("planned_end_verified")}


def _count_exit(db: Session, ctx: AccessContext, legal_entity_id: int, ed: date, counter: str | None) -> None:
    """WP-5 review condition 3: legal entity x month count only (no person link) for exits whose code is withheld."""
    if not counter:
        return
    month = ed.replace(day=1)
    row = db.execute(select(TerminationCounter).where(
        TerminationCounter.tenant_id == ctx.tenant_id, TerminationCounter.legal_entity_id == legal_entity_id,
        TerminationCounter.period_month == month, TerminationCounter.counter == counter).with_for_update()).scalar()
    if row is None:
        db.add(TerminationCounter(tenant_id=ctx.tenant_id, legal_entity_id=legal_entity_id, period_month=month,
                                  counter=counter, count=1))
        db.flush()
    else:
        row.count += 1


def _minimise_exit_codes(db: Session, batch: ImportBatch) -> None:
    """After commit no staging row keeps what a withheld code implied (category / counter marker): raw value ->
    'masked', mapped counter key dropped. Kept codes (non-sensitive, in the table) stay as imported."""
    col = (batch.mapping or {}).get("sgk_exit_code")
    for r in db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)).scalars():
        raw, mapped = r.raw or {}, r.mapped or {}
        if col and xc.is_masked(raw.get(col)) and raw.get(col) != xc.MINIMISED:
            r.raw = {**raw, col: xc.MINIMISED}
        if "_exit_counter" in mapped:
            r.mapped = {k: v for k, v in mapped.items() if k != "_exit_counter"}


def _review(db, ctx, batch, row, cand, reason):
    db.add(IdentityReviewItem(tenant_id=ctx.tenant_id, import_batch_id=batch.id, candidate_person_id=cand,
                              reason=reason, payload={k: v for k, v in row.mapped.items() if k != "national_id_hash"}))


def _batch(db: Session, ctx: AccessContext, batch_id: int) -> ImportBatch:
    b = db.get(ImportBatch, batch_id)
    # entity-scoped importers may only touch batches they created (audit finding #9)
    if b is None or b.tenant_id != ctx.tenant_id or (
            ctx.entity_filter("import.write") is not None and b.created_by != ctx.user_id) or (
            b.entity_type == "safety_event" and not ctx.has("hse.read")):  # ADR-021: HSE batches need hse.read
        raise ImportError_("Batch not found")
    return b


def summary(db: Session, ctx: AccessContext, batch_id: int) -> dict:
    b = _batch(db, ctx, batch_id)
    reviews = db.execute(select(func.count()).select_from(IdentityReviewItem).where(
        IdentityReviewItem.import_batch_id == b.id)).scalar()
    return {"batch_id": b.id, "entity_type": b.entity_type, "status": b.status, "file_name": b.file_name,
            "total_rows": b.total_rows,
            "valid_rows": b.valid_rows, "error_rows": b.error_rows, "committed_rows": b.committed_rows,
            "identity_reviews": reviews, "attempt_count": b.attempt_count, "error_message": b.error_message,
            "mapping": b.mapping, "coverage": b.coverage, "dropped_columns": b.dropped_columns or [],
            "options": b.options}


def error_report_csv(db: Session, ctx: AccessContext, batch_id: int) -> str:
    b = _batch(db, ctx, batch_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row_number", "external_id", "severity", "rule", "message"])
    for i in db.execute(select(DataQualityIssue).where(DataQualityIssue.import_batch_id == b.id)
                        .order_by(DataQualityIssue.row_number)).scalars():
        w.writerow([i.row_number, i.record_ref, i.severity, i.rule_code, i.message])
    return buf.getvalue()
