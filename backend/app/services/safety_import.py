"""HSE case import, entity_type = "safety_event" (wave-2 item 9, ADR-021).

Source: docs/hr/hse/WP-6_safety_import_fields.md + HR Director review (conditions 1-6, Q1-Q6). Review conditions
override the spec text where they differ:
- one row = one CASE (outcome of one affected person, no identity) - Q1; key (source_system, event_ref, case_no),
  stored as source_record_id "<event_ref>#<case_no>"; a re-import UPDATES the case, a missing case is never deleted;
- classification dictionary 3.4: short codes as whole tokens; Turkish and Russian word needles at word boundaries
  after Turkish-safe folding (condition 2); no default class; ambiguity, unknown labels, occupational illness (Q2),
  commuting (Q3) and out-of-scope events are errors that wait in the data quality queue;
- KVKK md.4/6 minimisation: identity, identifying attributes, health / injury detail and free text columns are dropped
  BEFORE anything is stored (only their names are kept); a mapping that points at a dropped column is an error that
  asks for the column to be renamed - never a silent drop of a needed field (condition 4);
- coverage: feed type "safety"; includes_subcontractor_cases / includes_near_miss travel in the declaration's
  attributes (condition 1); permission = whole-entity import.write AND hse.read on the employer (interim rule shared
  with POST /coverage until A11 defines an HSE import permission).
Validation messages carry the row number and event_ref only - never a label, name or description.
"""
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    SAFETY_RECLASSIFY_ACTION,
    SAFETY_SEVERITY,
    AuditLog,
    ContractorRelationship,
    DataQualityIssue,
    ImportBatch,
    ImportStagingRow,
    LegalEntity,
    Project,
    SafetyEvent,
)
from app.security.access import AccessContext

SAFETY_FIELDS = {
    "source_system": {"required": False, "aliases": ["kaynak", "source", "система"]},
    "event_ref": {"required": True, "aliases": ["olay no", "olay numarası", "kaza no", "kayıt no", "incident no",
                                                "incident id", "event id", "event ref", "номер происшествия"]},
    "case_no": {"required": False, "aliases": ["vaka no", "vaka sıra", "vaka sıra no", "case no", "case seq",
                                               "case number"]},
    "legal_entity_code": {"required": True, "aliases": ["firma", "firma kodu", "işveren", "alt yüklenici kodu",
                                                        "company", "employer", "компания"]},
    "project_code": {"required": False, "aliases": ["proje", "proje kodu", "şantiye", "project", "site", "проект",
                                                    "объект"]},
    "event_date": {"required": True, "aliases": ["olay tarihi", "kaza tarihi", "tarih", "event date", "incident date",
                                                 "дата происшествия"]},
    "classification": {"required": True, "aliases": ["sınıf", "olay sınıfı", "olay türü", "kaza türü", "sonuç",
                                                     "classification", "incident type", "severity", "категория"]},
    "lost_days": {"required": False, "aliases": ["kayıp gün", "kayıp iş günü", "iş günü kaybı", "istirahat günü",
                                                 "lost days", "days away", "lost workdays", "потерянные дни"]},
    "restricted_days": {"required": False, "aliases": ["kısıtlı iş günü", "hafif iş günü", "restricted days",
                                                       "days restricted"]},
    "worker_category": {"required": False, "aliases": ["çalışan türü", "personel tipi", "işçi kategorisi",
                                                       "worker type", "worker category", "категория работника"]},
    "reported_date": {"required": False, "aliases": ["iç bildirim tarihi", "kayıt tarihi", "sisteme giriş tarihi",
                                                     "reported date", "date reported", "дата регистрации"]},
    "case_status": {"required": False, "aliases": ["vaka durumu", "durum", "case status", "status"]},
    "sgk_notification": {"required": False, "aliases": ["sgk bildirimi", "sgk durumu", "sgk notification"]},
    "sgk_notification_date": {"required": False, "aliases": ["sgk bildirim tarihi", "sgk tarihi",
                                                             "sgk notification date"]},
    "high_consequence": {"required": False, "aliases": ["ağır sonuçlu", "high consequence"]},
    # WP-2 R2.5 / example L: a fatality may only be reclassified with a reason CODE (no free text is stored)
    "reclassification_reason": {"required": False, "aliases": ["gerekçe kodu", "yeniden sınıflandırma gerekçesi",
                                                               "reclassification reason", "reason code"]},
}

# ---------------------------------------------------------------- prohibited columns (WP-6 HSE 3.9, V17)
# Folded (imports._norm) header substrings. Short tokens match as whole words only, otherwise "SGK Notification"
# ('not') or "Organizasyon" ('organ') would be dropped (test_prohibited_column_short_tokens_whole_word).
_PROHIBITED_SUBSTRINGS = (
    # identity
    "ad soyad", "adi soyadi", "kazazede", "yaralanan", "yarali adi", "tckn", "kimlik", "sicil", "personel no",
    "employee id", "employee number", "passport", "pasaport", "telefon", "phone", "фамилия", "паспорт", "табельный",
    # attributes that can identify a person
    "dogum", "cinsiyet", "gender", "uyruk", "nationality", "meslek", "occupation", "unvan", "job title", "возраст",
    "должность",
    # health / diagnosis / injury detail
    "tani", "teshis", "diagnos", "yaralanma turu", "yaralanma sekli", "vucut bolgesi", "body part",
    "nature of injury", "injury type", "hastane", "tedavi", "treatment detail", "диагноз", "травма характер",
    "часть тела", "hastalik adi", "medical detail",
    # free text / fault
    "aciklama", "olayin olus", "olay ozeti", "description", "narrative", "summary", "notes", "comment", "kok neden",
    "root cause", "kusur", "sorumlu", "fault", "blame", "tanik", "witness", "описание", "причина", "виновн",
)
_PROHIBITED_WORDS = ("tc", "not", "yas", "isim", "name", "organ", "gorev", "icd", "имя", "пол")


def _fold(s: str) -> str:
    from app.services.imports import _norm

    return _norm(s)


def _tokens(s: str) -> list[str]:
    return re.findall(r"\w+", _fold(s))


def prohibited_columns(headers: list[str]) -> list[str]:
    out = []
    for h in headers:
        f = _fold(h)
        toks = set(re.findall(r"\w+", f))
        if any(k in f for k in _PROHIBITED_SUBSTRINGS) or any(w in toks for w in _PROHIBITED_WORDS):
            out.append(h)
    return out


def field_alias_conflicts(dropped: list[str]) -> list[str]:
    """Dropped headers that are exactly a field name / alias: the user clearly meant to import that field, so the
    upload is refused with a rename request instead of silently losing a required column (review condition 4)."""
    names = {_fold(a) for f, meta in SAFETY_FIELDS.items() for a in [f, f.replace("_", " "), *meta["aliases"]]}
    return [h for h in dropped if _fold(h) in names]


# ---------------------------------------------------------------- classification (WP-6 HSE 3.4)
# A needle is a phrase; a trailing '*' makes its LAST word a stem (prefix). Turkish needles are whole words (review
# condition 2: 'ölüm' must not match 'olumsuz', 'ölümlü' -> 'olumlu'); English and Russian needles are stems.
_CLASS_NEEDLES: dict[str, list[str]] = {
    "fatality": ["ölümlü", "ölüm", "vefat", "ölümle sonuçlanan", "fatal*", "death*", "смертельн*", "летальн*",
                 "гибел*"],
    "lost_time_injury": ["kayıp günlü", "kayıp iş günlü", "iş günü kayıplı", "istirahatli kaza", "lost time*",
                         "lost workday*", "days away*", "с потерей трудоспособност*", "временной нетрудоспособност*"],
    "restricted_work": ["kısıtlı iş", "hafif iş", "görev değişikliği", "restricted work*", "job transfer*",
                        "light duty*", "ограниченн*", "легкий труд*"],
    "medical_treatment": ["tıbbi müdahale", "tıbbi tedavi", "medical treatment*", "медицинское лечени*",
                          "медицинская помощ*"],
    "first_aid": ["ilk yardım", "ilkyardım", "first aid*", "первая помощ*", "первая медицинск*"],
    "near_miss": ["ramak kala", "ramakkala", "near miss*", "nearmiss*", "close call*", "почти происш*",
                  "предпосылк*"],
}
_CLASS_CODES = {
    "fat": "fatality", "lti": "lost_time_injury", "ltc": "lost_time_injury", "lwc": "lost_time_injury",
    "dafw": "lost_time_injury", "rwc": "restricted_work", "rwi": "restricted_work", "mtc": "medical_treatment",
    "mti": "medical_treatment", "fac": "first_aid", "fa": "first_aid", "nm": "near_miss",
}
# 3.4.b generic "work accident" labels: no severity -> only a lost-days value can make them an LTI
_GENERIC_NEEDLES = ["iş kazası", "kaza", "yaralanma", "incident*", "injur*", "accident*", "несчастный случа*",
                    "травм*"]
# 3.4.c labels that need a human / policy decision (checked first - they win over a class word in the same label)
_POLICY_NEEDLES: dict[str, list[str]] = {
    "illness_not_supported_v1": ["meslek hastalığı", "occupational disease*", "occupational illness*",
                                 "профзаболев*", "профессиональное заболев*"],
    "commuting_policy_required": ["trafik kazası", "servis kazası", "yol kazası", "commut*"],
    "out_of_scope_event": ["maddi hasar", "çevre olayı", "yangın", "property damage*", "environmental*"],
}
_NEGATIONS = {"non", "not", "no", "без"}
_CATEGORY_NEEDLES: list[tuple[str, list[str]]] = [  # first match wins (most specific first)
    ("third_party", ["ziyaretçi*", "üçüncü şahıs*", "üçüncü kişi*", "halk", "visitor*", "public", "third party*",
                     "посетител*"]),
    ("temporary_agency_worker", ["geçici iş ilişkisi*", "ödünç işçi*", "agency*", "temporary*", "временн*"]),
    ("subcontractor_worker", ["taşeron*", "alt yüklenici*", "alt işveren*", "subcontractor*", "contractor*",
                              "подрядчик*", "субподряд*"]),
    ("own_employee", ["kendi personeli*", "asıl işveren*", "kadrolu", "direct", "own", "employee*", "штатн*"]),
]
_compiled: dict[str, tuple[list[str], bool]] = {}


def _needle(n: str) -> tuple[list[str], bool]:
    if n not in _compiled:
        _compiled[n] = (_tokens(n.rstrip("*")), n.endswith("*"))
    return _compiled[n]


def _has(toks: list[str], needle: str) -> bool:
    words, stem = _needle(needle)
    k = len(words)
    for i in range(len(toks) - k + 1):
        if i > 0 and toks[i - 1] in _NEGATIONS:
            continue
        if toks[i:i + k - 1] != words[:-1]:
            continue
        last = toks[i + k - 1]
        if last == words[-1] or (stem and last.startswith(words[-1])):
            return True
    return False


def classify(label: str, lost_days: int | None) -> tuple[str | None, str | None, list[str]]:
    """(event_type | None, error rule code | None, warning rule codes). No default class (3.4 rule 4)."""
    toks = _tokens(label)
    for code, needles in _POLICY_NEEDLES.items():
        if any(_has(toks, n) for n in needles):
            return None, code, []
    found = {t for t, needles in _CLASS_NEEDLES.items() if any(_has(toks, n) for n in needles)}
    found |= {_CLASS_CODES[t] for i, t in enumerate(toks) if t in _CLASS_CODES
              and not (i > 0 and toks[i - 1] in _NEGATIONS)}
    if "medical_treatment" in found and "первая" in toks:  # RU 'первая медицинская помощь' = first aid (3.4 note)
        found.discard("medical_treatment")
    if len(found) > 1:
        return None, "classification_ambiguous", []
    if found:
        return found.pop(), None, []
    if any(_has(toks, n) for n in _GENERIC_NEEDLES):
        if lost_days is not None and lost_days >= 1:
            return "lost_time_injury", None, ["classification_derived_from_lost_days"]
        return None, "severity_required", []
    return None, "unmapped_classification", []


def worker_category(value: str) -> str | None:
    toks = _tokens(value)
    if not toks:
        return None
    for cat, needles in _CATEGORY_NEEDLES:
        if any(_has(toks, n) for n in needles):
            return cat
    return None


_CLASS_ERROR_MESSAGES = {
    "unmapped_classification": "classification label not recognised (there is no default class) -> not committed; "
                               "fix the label or the mapping in the HSE system",
    "classification_ambiguous": "classification label matches more than one class -> not committed (the platform "
                                "does not guess)",
    "severity_required": "generic 'work accident' label without lost days: medical treatment or first aid cannot be "
                         "told apart -> not committed (give the severity class)",
    "illness_not_supported_v1": "occupational illness cases are not imported in v1 (HR Director Q2) -> kept in the "
                                "queue for the HSE specialist / workplace physician",
    "commuting_policy_required": "commuting / traffic accident: inclusion in the rates needs a policy decision (Q3, "
                                 "WP-3 OHS-10) -> kept in the queue",
    "out_of_scope_event": "not an injury or near miss (property damage, environmental, fire without injury) -> kept "
                          "in the queue for the HSE specialist",
}
_YES = {"evet", "e", "yes", "y", "true", "1", "да", "var"}
_NO = {"hayir", "h", "no", "n", "false", "0", "нет", "yok"}
_CASE_STATUS = {"acik": "open", "open": "open", "devam ediyor": "open", "открыт": "open", "открыто": "open",
                "kapali": "closed", "kapandi": "closed", "closed": "closed", "закрыт": "closed", "закрыто": "closed"}
_SGK = {"bildirildi": "notified", "notified": "notified", "yapildi": "notified", "beklemede": "pending",
        "bekliyor": "pending", "pending": "pending", "gerekmiyor": "not_required", "not required": "not_required",
        "not_required": "not_required", "gerekli degil": "not_required"}
_REASON_CODE = re.compile(r"[A-Za-z0-9_.\-]{2,40}")
MIN_EVENT_DATE = date(2000, 1, 1)
INJURY_TYPES = {"fatality", "lost_time_injury", "restricted_work", "medical_treatment", "first_aid"}


def _int(v: str) -> int:
    f = float(v.replace(",", "."))
    if f != int(f):
        raise ValueError(v)
    return int(f)


def direction(old: str, new: str) -> str:
    """Rule codes shared with the leave import (WP-1 review condition 5)."""
    return "reclassified_up" if SAFETY_SEVERITY[new] > SAFETY_SEVERITY[old] else "reclassified_down"


def _entity_on_project(db: Session, tenant_id: int, entity_id: int, project: Project) -> bool:
    if project.owner_entity_id == entity_id:
        return True
    return db.execute(select(ContractorRelationship.id).where(
        ContractorRelationship.tenant_id == tenant_id, ContractorRelationship.child_entity_id == entity_id,
        (ContractorRelationship.project_id == project.id) | ContractorRelationship.project_id.is_(None))
        .limit(1)).first() is not None


def safety_scope(ctx: AccessContext) -> set[int] | None:
    """Employers the caller may import HSE cases for: whole-entity import.write AND hse.read (None = tenant-wide)."""
    from app.services.imports import _import_scope

    imp = _import_scope(ctx)
    hse = ctx.entity_filter("hse.read") if ctx.has("hse.read") else set()
    if imp is None and hse is None:
        return None
    base = imp if imp is not None else hse
    return {e for e in base if ctx.covers_entity("hse.read", e) and (imp is None or e in imp)}


def validate(db: Session, ctx: AccessContext, batch: ImportBatch, mapping: dict) -> dict:
    from app.services import coverage as cov
    from app.services.imports import OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, _parse_date, summary

    scope = safety_scope(ctx)
    today = date.today()
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    projects = {p.code: p for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    staged = list(db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                             .order_by(ImportStagingRow.row_number)).scalars())
    parsed: list[tuple[ImportStagingRow, dict, list[dict], bool]] = []
    for row in staged:
        m = {f: (str(row.raw.get(src, "") or "").strip() if src else "") for f, src in mapping.items()}
        errs: list[dict] = []

        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        for f, meta in SAFETY_FIELDS.items():
            if meta["required"] and not m.get(f):
                err("required", f"'{f}' is required")
        src = m.get("source_system") or "import"
        ref = m.get("event_ref", "")
        if len(src) > 50 or len(ref) > 100:
            err("field_too_long", "source_system (max 50) / event_ref (max 100) too long")
        case_blank = not m.get("case_no")
        case_no = 1
        if not case_blank:
            try:
                case_no = _int(m["case_no"])
                if case_no < 1:
                    raise ValueError
            except ValueError:
                err("case_no_format", "case_no must be a whole number >= 1")
        le = ents.get(m.get("legal_entity_code", ""))
        if m.get("legal_entity_code") and le is None:
            err("unknown_legal_entity", f"legal entity '{m['legal_entity_code']}' not found")
        elif le is not None and scope is not None and le.id not in scope:
            err("scope", f"no HSE import permission (import.write + hse.read on the whole entity) for '{le.code}'")
        prj = None
        if m.get("project_code"):
            prj = projects.get(m["project_code"])
            if prj is None:
                err("unknown_project", f"project '{m['project_code']}' not found")
            elif le is not None and not _entity_on_project(db, ctx.tenant_id, le.id, prj):
                err("entity_not_on_project", "legal entity is neither the project owner nor a contractor on it",
                    "warning")
        else:
            err("project_code_missing", "no project code: the case counts for the employer but no project view",
                "warning")
        dates: dict[str, date | None] = {}
        for f in ("event_date", "reported_date", "sgk_notification_date"):
            try:
                dates[f] = _parse_date(m.get(f, ""))
            except ValueError:
                dates[f] = None
                err("date_format", f"'{f}' is not a valid date")
        ed = dates["event_date"]
        if ed is not None:
            if ed < MIN_EVENT_DATE:
                err("date_format", "event_date before 2000-01-01")
                ed = None
            elif ed > today:
                err("event_date_in_future", "event_date is after the import day")
                ed = None
        rd, sd = dates["reported_date"], dates["sgk_notification_date"]
        if rd is not None and ((ed is not None and rd < ed) or rd > today):
            err("reported_before_event", "reported_date must be on/after event_date and not after the import day")
        if sd is not None and ed is not None and sd < ed:
            err("sgk_date_before_event", "sgk_notification_date before event_date")
        nums: dict[str, int | None] = {}
        for f in ("lost_days", "restricted_days"):
            nums[f] = None
            if m.get(f):
                try:
                    nums[f] = _int(m[f])
                    if not 0 <= nums[f] <= 3650:
                        raise ValueError
                except ValueError:
                    nums[f] = None
                    err("lost_days_range", f"'{f}' must be a whole number of calendar days 0-3650")
        lost = nums["lost_days"]
        status = _CASE_STATUS.get(_fold(m.get("case_status", ""))) if m.get("case_status") else None
        if m.get("case_status") and status is None:
            err("case_status_unknown", "case_status not recognised (open / closed) -> stored empty", "warning")
        et = None
        if m.get("classification"):
            et, rule, warns = classify(m["classification"], lost)
            if rule:
                err(rule, _CLASS_ERROR_MESSAGES[rule])
            for w in warns:
                err(w, "generic work-accident label with lost days >= 1 -> lost_time_injury (derived)", "warning")
        if et == "lost_time_injury" and (lost is None or lost < 1):
            if lost is None and status == "open":
                err("lost_days_pending", "LTI case still open without lost days -> update with a later import",
                    "warning")
            elif not [e for e in errs if e["rule"] == "lost_days_range"]:
                err("lti_without_lost_days", "lost-time injury needs lost_days >= 1 (or case_status open)")
        if et in ("restricted_work", "medical_treatment", "first_aid", "near_miss") and lost:
            err("lost_days_class_conflict", "lost days > 0 with a class that has no days away -> possible "
                                            "under-classification (S6); not committed")
        if et == "fatality" and lost:
            err("fatality_lost_days_ignored", "lost days are not recorded for a fatality -> ignored", "warning")
            lost = None
        if et == "restricted_work" and not nums["restricted_days"]:
            err("rwc_without_restricted_days", "restricted work case without restricted days", "warning")
        cat = None
        if et in INJURY_TYPES:
            cat = worker_category(m.get("worker_category", ""))
            if cat is None:
                err("unknown_worker_category", "worker_category missing or not recognised (own_employee, "
                                               "subcontractor_worker, temporary_agency_worker, third_party)")
        sgk = None
        if m.get("sgk_notification"):
            sgk = _SGK.get(_fold(m["sgk_notification"]))
            if sgk is None:
                err("sgk_notification_unknown", "sgk_notification not recognised -> stored empty", "warning")
        hc = None
        if m.get("high_consequence"):
            v = _fold(m["high_consequence"])
            hc = True if v in _YES else False if v in _NO else None
            if hc is None:
                err("high_consequence_unknown", "high_consequence not recognised (yes / no) -> stored empty",
                    "warning")
        reason = m.get("reclassification_reason") or None
        if reason and not _REASON_CODE.fullmatch(reason):
            err("reason_code_format", "reclassification_reason must be a code (2-40 letters, digits, _ . -); free "
                                      "text is not stored")
            reason = None
        if le is not None and batch.coverage and ed is not None and cov.row_outside(
                batch.coverage, le.id, ed, ed, prj.id if prj else None):
            err(OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, "warning")
        mapped = {"source_system": src, "event_ref": ref, "case_no": case_no,
                  "source_record_id": f"{ref}#{case_no}", "legal_entity_code": m.get("legal_entity_code"),
                  "legal_entity_id": le.id if le else None, "project_id": prj.id if prj else None,
                  "event_date": ed.isoformat() if ed else None, "event_type": et, "lost_days": lost,
                  "restricted_days": nums["restricted_days"], "worker_category": cat,
                  "reported_date": rd.isoformat() if rd else None, "case_status": status,
                  "sgk_notification": sgk, "sgk_notification_date": sd.isoformat() if sd else None,
                  "high_consequence": hc, "reclassification_reason": reason}
        parsed.append((row, mapped, errs, case_blank))

    # file-level rules: V13 duplicates, V14 inconsistent event fields, conditional case_no
    by_event: dict[tuple, list[tuple[dict, bool]]] = {}
    for _, mp, _, blank in parsed:
        by_event.setdefault((mp["source_system"], mp["event_ref"]), []).append((mp, blank))
    seen: set[tuple] = set()
    keys = {mp["source_record_id"] for _, mp, _, _ in parsed if mp["event_ref"]}
    existing = {(r.source_system, r.source_record_id): r for r in db.execute(select(SafetyEvent).where(
        SafetyEvent.tenant_id == ctx.tenant_id, SafetyEvent.source_record_id.in_(keys or {""}))).scalars()}
    valid = errors = 0
    issues = []
    for row, mp, errs, blank in parsed:
        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        group = by_event.get((mp["source_system"], mp["event_ref"]), [])
        if mp["event_ref"] and len(group) > 1:
            if blank:
                err("case_no_required", "this event has several rows in the file: case_no is required")
            if len({(g["event_date"], g["project_id"]) for g, _ in group}) > 1:
                err("event_fields_inconsistent", "rows of the same event differ in event_date / project")
        key = (mp["source_system"], mp["event_ref"], mp["case_no"])
        if mp["event_ref"] and key in seen:
            err("duplicate_in_file", "duplicate case (same source system, event_ref and case_no) in file")
        seen.add(key)
        mp["existing_id"] = mp["reclassify_from"] = None
        ex = existing.get((mp["source_system"], mp["source_record_id"]))
        if ex is not None and not [e for e in errs if e["severity"] == "error"]:
            if ex.legal_entity_id != mp["legal_entity_id"]:
                err("legal_entity_changed", "this case exists for another legal entity -> not committed (manual "
                                            "review)")
            elif mp["event_date"] and ex.event_date.isoformat() != mp["event_date"]:
                err("event_date_changed", "this case exists with another event_date -> not committed (manual review)")
            else:
                mp["existing_id"] = ex.id
                if ex.event_type != mp["event_type"]:
                    if ex.event_type == "fatality" and not mp["reclassification_reason"]:
                        err("fatality_reclassification_reason_required", "a fatality can only be reclassified with "
                            "a reclassification_reason code (WP-2 R2.5) -> not committed")
                    else:
                        mp["reclassify_from"] = ex.event_type
                        err(direction(ex.event_type, mp["event_type"]), "same case arrives with another class -> "
                            "updated in place on commit (direction recorded, no second record)", "warning")
                elif _changed(ex, mp):
                    err("case_updated", "same case with changed details -> updated in place on commit", "warning")
                else:
                    err("already_imported", "identical case already stored -> will be skipped", "warning")
        row.mapped = mp
        row.errors = errs
        hard = [e for e in errs if e["severity"] == "error"]
        row.has_error = bool(hard)
        if hard:
            errors += 1
        else:
            valid += 1
        for e in errs:
            issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "safety_event",
                           "rule_code": e["rule"], "severity": e["severity"], "message": e["message"],
                           "row_number": row.row_number, "record_ref": (mp["event_ref"] or None) and
                           f"{mp['event_ref']}#{mp['case_no']}"[:120]})
    if batch.dropped_columns:
        issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "safety_event",
                       "rule_code": "prohibited_columns_dropped", "severity": "warning", "row_number": None,
                       "record_ref": None, "message": "prohibited column(s) dropped before storage (names only, "
                                                      "values never stored): " + ", ".join(batch.dropped_columns)})
    if issues:
        db.bulk_insert_mappings(DataQualityIssue, issues)
    batch.valid_rows, batch.error_rows, batch.status = valid, errors, "validated"
    db.commit()
    return summary(db, ctx, batch.id)


_UPDATABLE = ("project_id", "event_type", "lost_days", "restricted_days", "worker_category", "reported_date",
              "case_status", "sgk_notification", "sgk_notification_date", "high_consequence")
_DATE_FIELDS = {"reported_date", "sgk_notification_date"}


def _value(mp: dict, f: str):
    v = mp.get(f)
    return date.fromisoformat(v) if f in _DATE_FIELDS and v else v


def _changed(ev: SafetyEvent, mp: dict) -> bool:
    return any(getattr(ev, f) != _value(mp, f) for f in _UPDATABLE)


def commit_row(db: Session, ctx: AccessContext, row: ImportStagingRow) -> date | None:
    """Insert a new case or update the stored one in place (record_version + 1). A class change writes an audit row
    (action safety.reclassify, direction) in the same transaction; a fatality reclassified to another class keeps the
    marker reclassified_from_fatality (WP-2 example L). Never deletes (V16)."""
    from app.services.imports import ImportError_

    mp = row.mapped
    ed = date.fromisoformat(mp["event_date"])
    ev = db.execute(select(SafetyEvent).where(
        SafetyEvent.tenant_id == ctx.tenant_id, SafetyEvent.source_system == mp["source_system"],
        SafetyEvent.source_record_id == mp["source_record_id"])).scalar()
    if ev is None:
        db.add(SafetyEvent(tenant_id=ctx.tenant_id, legal_entity_id=mp["legal_entity_id"], event_date=ed,
                           event_ref=mp["event_ref"], case_no=mp["case_no"], source_system=mp["source_system"],
                           source_record_id=mp["source_record_id"], updated_by=ctx.user_id,
                           **{f: _value(mp, f) for f in _UPDATABLE}))
        return ed
    if ev.legal_entity_id != mp["legal_entity_id"] or ev.event_date != ed:
        raise ImportError_("safety case changed between validation and commit; validate again")
    if not _changed(ev, mp):
        return None  # identical (re-import / resumed commit)
    old = ev.event_type
    if old != mp["event_type"] and old == "fatality" and not mp.get("reclassification_reason"):
        raise ImportError_("safety case changed between validation and commit; validate again")
    for f in _UPDATABLE:
        setattr(ev, f, _value(mp, f))
    ev.record_version = (ev.record_version or 1) + 1
    ev.updated_by = ctx.user_id
    if old != ev.event_type:
        if old == "fatality":
            ev.reclassified_from_fatality = True
        db.add(AuditLog(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=SAFETY_RECLASSIFY_ACTION,
                        entity_type="safety_event", entity_id=str(ev.id),
                        details={"from": old, "to": ev.event_type, "direction": direction(old, ev.event_type),
                                 "legal_entity_id": ev.legal_entity_id, "project_id": ev.project_id,
                                 "event_date": ed.isoformat(), "import_batch_id": row.batch_id,
                                 "record_version": ev.record_version,
                                 "reason_code": mp.get("reclassification_reason")}))
    return ed


def minimise_raw(db: Session, batch: ImportBatch) -> None:
    """After a successful commit the raw staging rows keep only the mapped source columns (KVKK md.4): an unmapped
    column with an innocent-looking header is not kept for the staging retention period."""
    keep = {v for v in (batch.mapping or {}).values() if v}
    for row in db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)).scalars():
        if row.raw and set(row.raw) - keep:
            row.raw = {k: v for k, v in row.raw.items() if k in keep}
