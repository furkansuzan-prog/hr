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
import re
import unicodedata
from datetime import date, datetime

from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.engine import mark_stale_from
from app.core.config import get_settings
from app.models import (
    Assignment,
    DataQualityIssue,
    EmploymentRelationship,
    IdentityReviewItem,
    ImportBatch,
    ImportStagingRow,
    LegalEntity,
    Occupation,
    Person,
    PersonIdentifier,
    Project,
)
from app.models.people import TERMINATION_CATEGORIES
from app.security.access import AccessContext
from app.security.auth import national_id_hash


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
}
CONTRACT_TYPES = {"permanent", "fixed_term", "project", "seasonal"}


def _norm(h: str) -> str:
    """Header normalisation that is safe for Turkish (İ/ı) and other diacritics: 'İşe Giriş' == 'ise giris'."""
    h = (h or "").strip().replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    h = "".join(c for c in unicodedata.normalize("NFKD", h) if not unicodedata.combining(c))
    return re.sub(r"[\s_\-]+", " ", h)


def suggest_mapping(headers: list[str]) -> dict[str, str | None]:
    """target field -> source header"""
    out: dict[str, str | None] = {}
    normed = {_norm(h): h for h in headers}
    for f, meta in FIELDS.items():
        cands = [_norm(f), _norm(f.replace("_", " "))] + [_norm(a) for a in meta["aliases"]]
        out[f] = next((normed[c] for c in cands if c in normed), None)
    return out


def parse_file(filename: str, content: bytes) -> tuple[list[str], list[dict]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
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
        text = content.decode("utf-8-sig")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
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


def upload(db: Session, ctx: AccessContext, *, entity_type: str, filename: str, content: bytes) -> dict:
    if entity_type != "employment":
        raise ImportError_("Only entity_type='employment' is supported in this release")
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
    suggested = suggest_mapping(headers)
    # KVKK: identity numbers are pseudonymised BEFORE the raw row is persisted or echoed back in the preview
    _pseudonymise(rows, suggested.get("national_id"))
    batch = ImportBatch(tenant_id=ctx.tenant_id, entity_type=entity_type, file_name=filename[:255], file_sha256=sha,
                        status="uploaded", total_rows=len(rows), created_by=ctx.user_id,
                        mapping=suggested)
    db.add(batch)
    db.flush()
    chunk = get_settings().import_chunk_size
    for i in range(0, len(rows), chunk):
        db.bulk_insert_mappings(ImportStagingRow, [
            {"batch_id": batch.id, "row_number": i + j + 2, "raw": r} for j, r in enumerate(rows[i:i + chunk])])
    db.commit()
    return {"batch_id": batch.id, "headers": headers, "total_rows": len(rows), "suggested_mapping": batch.mapping,
            "required_fields": [f for f, m in FIELDS.items() if m["required"]], "preview": rows[:20]}


_HMAC_PREFIX = "hmac:"


def _pseudonymise(rows: list[dict], column: str | None) -> None:
    if not column:
        return
    for r in rows:
        v = str(r.get(column) or "").strip()
        if v and not v.startswith(_HMAC_PREFIX):
            r[column] = _HMAC_PREFIX + national_id_hash(v)


def validate(db: Session, ctx: AccessContext, batch_id: int, mapping: dict[str, str | None] | None) -> dict:
    batch = _batch(db, ctx, batch_id)
    if batch.status == "committed":
        raise ImportError_("Batch already committed")
    mapping = mapping or batch.mapping or {}
    missing = [f for f, m in FIELDS.items() if m["required"] and not mapping.get(f)]
    if missing:
        raise ImportError_(f"Mapping missing required fields: {', '.join(missing)}")
    batch.mapping = mapping
    db.query(DataQualityIssue).filter(DataQualityIssue.import_batch_id == batch.id).delete()

    import_scope = ctx.entity_filter("import.write")
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    projects = {p.code: p.id for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    occs = {o.code: o.id for o in db.execute(select(Occupation).where(Occupation.tenant_id == ctx.tenant_id)).scalars()}
    seen: set[tuple] = set()
    valid = errors = 0
    issues = []
    nid_col = mapping.get("national_id")
    for row in db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                          .order_by(ImportStagingRow.row_number)).scalars():
        if nid_col and str(row.raw.get(nid_col) or "").strip() and not str(row.raw[nid_col]).startswith(_HMAC_PREFIX):
            # user mapped a different column than suggested: pseudonymise it in the stored raw row too
            raw = dict(row.raw)
            _pseudonymise([raw], nid_col)
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
        cat = (m.get("termination_category") or "").strip().lower()
        if cat and cat not in TERMINATION_CATEGORIES:
            err("termination_category", f"unknown termination category '{cat}'")
        if m.get("end_date") and not cat:
            m["termination_category"] = "unknown"
            err("termination_reason_missing", "end_date without reason -> stored as 'unknown' (not assumed)", "warning")
        ct = (m.get("contract_type") or "permanent").strip().lower()
        if ct not in CONTRACT_TYPES:
            err("contract_type", f"unknown contract type '{ct}'")
        m["contract_type"] = ct
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
                d = _commit_row(db, ctx, batch, r, ents, projects, occs)
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
        if earliest:
            mark_stale_from(db, ctx.tenant_id, earliest)
        db.commit()
    except Exception as exc:
        db.rollback()
        b = db.get(ImportBatch, batch_id)
        b.status = "failed"
        b.error_message = str(exc)[:1000]
        db.commit()
        raise ImportError_(f"Commit failed and can be retried: {exc}") from exc
    return summary(db, ctx, batch_id)


def _commit_row(db, ctx, batch, row, ents, projects, occs) -> date | None:
    m = row.mapped
    src = m.get("source_system") or "import"
    ident = db.execute(select(PersonIdentifier).where(PersonIdentifier.tenant_id == ctx.tenant_id,
                                                      PersonIdentifier.source_system == src,
                                                      PersonIdentifier.external_id == m["external_id"])).scalar()
    person = db.get(Person, ident.person_id) if ident else None
    if person is None and m.get("national_id_hash"):
        cands = list(db.execute(select(Person).where(Person.tenant_id == ctx.tenant_id,
                                                     Person.national_id_hash == m["national_id_hash"])).scalars())
        if len(cands) > 1:
            _review(db, ctx, batch, row, None, "multiple persons share this national id hash")
            return None
        if len(cands) == 1:
            c = cands[0]
            if c.last_name.strip().lower() != m["last_name"].strip().lower():
                _review(db, ctx, batch, row, c.id, "national id matches but last name differs")
                return None
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
    if emp is None:
        emp = EmploymentRelationship(tenant_id=ctx.tenant_id, person_id=person.id, legal_entity_id=le_id,
                                     employee_number=m.get("employee_number") or None, contract_type=m["contract_type"],
                                     start_date=sd, end_date=ed, termination_category=m.get("termination_category")
                                     if ed else None, source_system=src, source_record_id=m["external_id"],
                                     updated_by=ctx.user_id)
        db.add(emp)
        db.flush()
        if m.get("project_code") or m.get("occupation_code"):
            db.add(Assignment(tenant_id=ctx.tenant_id, employment_id=emp.id, project_id=projects.get(m.get("project_code")),
                              occupation_id=occs.get(m.get("occupation_code")), valid_from=sd,
                              valid_to=(ed.fromordinal(ed.toordinal() + 1) if ed else None)))
        return sd
    # existing spell: back-dated termination is an update of end date (versioned), never a delete
    if ed and emp.end_date != ed:
        emp.end_date = ed
        emp.termination_category = m.get("termination_category") or "unknown"
        emp.record_version += 1
        emp.updated_by = ctx.user_id
        return ed
    return None


def _review(db, ctx, batch, row, cand, reason):
    db.add(IdentityReviewItem(tenant_id=ctx.tenant_id, import_batch_id=batch.id, candidate_person_id=cand,
                              reason=reason, payload={k: v for k, v in row.mapped.items() if k != "national_id_hash"}))


def _batch(db: Session, ctx: AccessContext, batch_id: int) -> ImportBatch:
    b = db.get(ImportBatch, batch_id)
    if b is None or b.tenant_id != ctx.tenant_id:
        raise ImportError_("Batch not found")
    return b


def summary(db: Session, ctx: AccessContext, batch_id: int) -> dict:
    b = _batch(db, ctx, batch_id)
    reviews = db.execute(select(func.count()).select_from(IdentityReviewItem).where(
        IdentityReviewItem.import_batch_id == b.id)).scalar()
    return {"batch_id": b.id, "status": b.status, "file_name": b.file_name, "total_rows": b.total_rows,
            "valid_rows": b.valid_rows, "error_rows": b.error_rows, "committed_rows": b.committed_rows,
            "identity_reviews": reviews, "attempt_count": b.attempt_count, "error_message": b.error_message,
            "mapping": b.mapping}


def error_report_csv(db: Session, ctx: AccessContext, batch_id: int) -> str:
    b = _batch(db, ctx, batch_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row_number", "external_id", "severity", "rule", "message"])
    for i in db.execute(select(DataQualityIssue).where(DataQualityIssue.import_batch_id == b.id)
                        .order_by(DataQualityIssue.row_number)).scalars():
        w.writerow([i.row_number, i.record_ref, i.severity, i.rule_code, i.message])
    return buf.getvalue()
