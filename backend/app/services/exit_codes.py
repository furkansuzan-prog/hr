"""SGK exit codes (işten çıkış kodu) -> platform termination categories (WP-5 3.1-3.2 + HR Director review; ADR-023).

The mapping is DATA (`termination_code_map`, per tenant), seeded unverified from the WP-5 starting list. Rules:
- A row is used for the category only when verified (verified_at set, by the payroll / SGK practitioner, L-12).
  Until then a code falls back to the source termination category or 'unknown' - the table is "not active".
- KVKK safe default (WP-5 L-8, review condition 2): a code is stored on the employment ONLY when it is in the table,
  not health-related and not withheld (retain_code). Health-implying codes (09, 24, 28), code 11 (death from a work
  accident, L-8 pending) and every code that is NOT in the table (the list beyond 34 / 42-50 is unverified and may
  hold maternity-related codes) are never stored: the raw value is replaced in the staging row on upload, before it
  is persisted or echoed in the preview; only the derived category (and, for code 11, a legal entity x month counter)
  survives.
"""
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TerminationCodeMap

CODE_SYSTEM = "SGK_ISTEN_CIKIS"
MASK_PREFIX = "masked:"
# KVKK safe defaults that hold even if a tenant's table says otherwise (WP-5 L-8): never stored
HEALTH_CODES = frozenset({"09", "24", "28"})
WITHHELD_CODES = frozenset({"11"})  # L-8 open: category 'death' + entity x month counter only
WORK_ACCIDENT_DEATH_COUNTER = "work_accident_death_exits"
TRANSFER_CODE = "16"
VOLUNTARY_JUST_CAUSE_CODES = ("23", "25")  # review condition 4 (24 is a health code: only in the voluntary total)

# WP-5_sgk_exit_code_map.csv (compiled from memory - "teyit edilmeli" on every row) with the HR Director review applied:
# 42-50 expanded, code 22 note = 15 % (condition 1, unknown + other), 13 -> other (L-9), 11 withheld (condition 3).
# (code, label, category, planned_end_eligible, health_related, note)
SEED_ROWS: tuple[tuple[str, str, str, bool, bool, str], ...] = (
    ("01", "Deneme süreli iş sözleşmesinin işverence feshi", "employer_initiated", False, False, "md.15"),
    ("02", "Deneme süreli iş sözleşmesinin işçi tarafından feshi", "voluntary", False, False, "md.15"),
    ("03", "Belirsiz süreli iş sözleşmesinin işçi tarafından feshi (istifa)", "voluntary", False, False, ""),
    ("04", "Belirsiz süreli iş sözleşmesinin işveren tarafından haklı sebep bildirilmeden feshi",
     "employer_initiated", False, False, ""),
    ("05", "Belirli süreli iş sözleşmesinin sona ermesi", "contract_end", True, False,
     "contract_type fixed_term/project/seasonal şart"),
    ("08", "Emeklilik (yaşlılık) veya toptan ödeme", "retirement", False, False, ""),
    ("09", "Malulen emeklilik", "retirement", False, True, "sağlık verisi -> kod saklanmaz (L-8)"),
    ("10", "Ölüm", "death", False, False, ""),
    ("11", "İş kazası sonucu ölüm", "death", False, False,
     "L-8 yanıtına kadar kod saklanmaz; tüzel kişilik x ay sayacı work_accident_death_exits"),
    ("12", "Askerlik", "other", False, False, ""),
    ("13", "Kadın işçinin evlenmesi", "other", False, False, "S-3 / L-9: KPI-020 dışında (other)"),
    ("14", "Emeklilik için yaş dışındaki şartların tamamlanması", "retirement", False, False, ""),
    ("15", "Toplu işçi çıkarma", "employer_initiated", False, False, "md.29"),
    ("16", "Sözleşme sona ermeden aynı işverenin diğer işyerine nakil", "other", False, False,
     "7 gün içinde yeni dönem beklenir; yoksa transfer_without_new_spell"),
    ("17", "İşyerinin kapanması", "project_end", True, False, "contract_type project/fixed_term şart (L-10)"),
    ("18", "İşin sona ermesi", "project_end", True, False, "contract_type project/fixed_term şart (L-10)"),
    ("19", "Mevsim bitimi", "contract_end", True, False, "contract_type seasonal beklenir"),
    ("20", "Kampanya bitimi", "contract_end", True, False, ""),
    ("21", "Statü değişikliği", "other", False, False, ""),
    ("22", "Diğer nedenler", "other", False, False, "düşük bilgi payı: (unknown + other) / tüm ayrılışlar > %15"),
    ("23", "İşçi tarafından zorunlu nedenle fesih", "voluntary", False, False, "md.24/III; voluntary_just_cause"),
    ("24", "İşçi tarafından sağlık nedeniyle fesih", "voluntary", False, True, "md.24/I; kod saklanmaz (L-8)"),
    ("25", "İşçi tarafından işverenin ahlak ve iyi niyet kurallarına aykırı davranışı nedeniyle fesih", "voluntary",
     False, False, "md.24/II (ücret ödenmemesi dahil); voluntary_just_cause"),
    ("26", "Disiplin kurulu kararı ile fesih", "employer_initiated", False, False, ""),
    ("27", "İşveren tarafından zorunlu nedenlerle ve tutukluluk nedeniyle fesih", "employer_initiated", False, False,
     "md.25/III-IV"),
    ("28", "İşveren tarafından sağlık nedeniyle fesih", "employer_initiated", False, True,
     "md.25/I; kod saklanmaz (L-8)"),
    ("29", "İşveren tarafından ahlak ve iyi niyet kurallarına aykırılık nedeniyle fesih", "employer_initiated", False,
     False, "yeni bildirgelerde 42-50 alt kodlarına bölünmüş olabilir"),
    ("30", "Vize süresinin bitimi (yabancı)", "other", False, False, ""),
    ("31", "Diğer kanunlar kapsamında kendi istek ve kusuru dışında sona erme", "other", False, False, ""),
    ("32", "4046 sayılı Kanun md.21 (özelleştirme)", "employer_initiated", False, False, ""),
    ("33", "Gazeteci tarafından sözleşmenin feshi", "voluntary", False, False, ""),
    ("34", "İşyerinin devri / işin veya işyerinin niteliğinin değişmesi nedeniyle fesih", "employer_initiated", False,
     False, ""),
    *((str(c), f"md.25/II alt bendi (işveren haklı nedenle fesih) - {c}", "employer_initiated", False, False,
       "alt bent ayrımı KPI için gerekli değil") for c in range(42, 51)),
)


@dataclass(frozen=True)
class Resolution:
    """What the import may use from one exit code. `code` is None when the raw value must not be kept."""
    code: str | None  # value stored in termination_reason_code (None = not stored)
    known: bool  # present in the tenant's table (valid on the date)
    category: str | None  # from a VERIFIED row only
    planned_end_eligible: bool | None  # verified rows only
    counter: str | None = None  # entity x month counter to increment on commit (verified rows only)

    @property
    def verified(self) -> bool:
        return self.category is not None


def normalize_code(value) -> str | None:
    """'3', '03', '3.0' (Excel) -> '03'. Blank -> None. Anything else -> ValueError (not a 1-2 digit code)."""
    s = str(value or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2})(?:[.,]0+)?", s)
    if not m:
        raise ValueError("exit code must be a 1-2 digit number")
    return m.group(1).zfill(2)


def is_masked(value) -> bool:
    return str(value or "").startswith(MASK_PREFIX)


def mask(res: Resolution) -> str:
    """Staging marker for a code that is not kept: carries only what validation needs (kind, verified category,
    counter) - never the code. kind 'unknown' = not in the table (so the importer can be told), 'withheld' otherwise
    (health / L-8 - indistinguishable from each other on purpose)."""
    kind = "withheld" if res.known else "unknown"
    return f"{MASK_PREFIX}{kind}:{res.category or ''}:{res.counter or ''}"


def unmask(value: str) -> tuple[str, Resolution]:
    kind, category, counter = [*value[len(MASK_PREFIX):].split(":"), "", "", ""][:3]
    return kind, Resolution(None, kind != "unknown", category or None, False if category else None, counter or None)


MINIMISED = MASK_PREFIX.rstrip(":")  # what a staging row keeps after commit


class CodeMap:
    """The tenant's mapping table, loaded once per import step."""

    def __init__(self, db: Session, tenant_id: int):
        self.rows: dict[str, list[TerminationCodeMap]] = {}
        for r in db.execute(select(TerminationCodeMap).where(TerminationCodeMap.tenant_id == tenant_id,
                                                             TerminationCodeMap.code_system == CODE_SYSTEM)).scalars():
            self.rows.setdefault(r.code, []).append(r)

    def row(self, code: str, on: date | None) -> TerminationCodeMap | None:
        on = on or date.today()
        for r in self.rows.get(code, ()):
            if (r.valid_from is None or r.valid_from <= on) and (r.valid_to is None or on <= r.valid_to):
                return r
        return None

    def resolve(self, code: str, on: date | None) -> Resolution:
        r = self.row(code, on)
        if r is None:
            return Resolution(None, False, None, None)
        verified = r.verified_at is not None
        keep = r.retain_code and not r.health_related and code not in HEALTH_CODES | WITHHELD_CODES
        return Resolution(code if keep else None, True, r.category if verified else None,
                          r.planned_end_eligible if verified else None, r.counter if verified else None)

    def stage_value(self, raw, on: date | None):
        """Value persisted in the staging row for a raw cell: the normalised code when it may be kept, a marker when
        not, the raw text when it is not a code at all (validation reports exit_code_format)."""
        if is_masked(raw):
            return raw
        try:
            code = normalize_code(raw)
        except ValueError:
            return raw
        if code is None:
            return ""
        res = self.resolve(code, on)
        return code if res.code else mask(res)


def storable_code(db: Session, tenant_id: int, value: str | None, on: date | None) -> str | None:
    """Manual / API paths: the value that may be stored in termination_reason_code. Free-text reason codes that are not
    SGK-shaped (e.g. 'internal_transfer') pass through; an SGK-shaped code passes only if the table allows keeping it."""
    if value is None or not str(value).strip():
        return None
    try:
        code = normalize_code(value)
    except ValueError:
        return str(value).strip()
    ensure_map(db, tenant_id)
    return CodeMap(db, tenant_id).resolve(code, on).code


def ensure_map(db: Session, tenant_id: int) -> int:
    """Seed the WP-5 starting list (all rows unverified) for a tenant that has no mapping yet. Returns rows added."""
    if db.execute(select(TerminationCodeMap.id).where(TerminationCodeMap.tenant_id == tenant_id).limit(1)).first():
        return 0
    for code, label, cat, planned, health, note in SEED_ROWS:
        db.add(TerminationCodeMap(tenant_id=tenant_id, code_system=CODE_SYSTEM, code=code, label_tr=label,
                                  category=cat, planned_end_eligible=planned, health_related=health,
                                  retain_code=code not in WITHHELD_CODES,
                                  counter=WORK_ACCIDENT_DEATH_COUNTER if code == "11" else None, note=note or None))
    db.flush()
    return len(SEED_ROWS)


def verify(db: Session, tenant_id: int, codes: list[str], verified_by: str) -> int:
    """Mark mapping rows as verified against the current e-Bildirge list (L-12). Activation is a data step by the
    payroll / SGK practitioner; health_related / retain_code cannot be relaxed here (KVKK safe defaults)."""
    if not verified_by.strip():
        raise ValueError("verified_by is required")
    ensure_map(db, tenant_id)
    wanted = {normalize_code(c) for c in codes}
    n = 0
    for r in db.execute(select(TerminationCodeMap).where(TerminationCodeMap.tenant_id == tenant_id,
                                                         TerminationCodeMap.code.in_(wanted))).scalars():
        r.verified_by, r.verified_at = verified_by.strip()[:120], datetime.now(UTC)
        n += 1
    db.commit()
    return n


if __name__ == "__main__":  # python -m app.services.exit_codes verify <tenant_code> <verified_by> 01 02 03 ...
    import sys

    from app.core.db import SessionLocal
    from app.models import Tenant

    if len(sys.argv) < 5 or sys.argv[1] != "verify":
        sys.exit("usage: python -m app.services.exit_codes verify <tenant_code> <verified_by> <code> [<code> ...]")
    with SessionLocal() as _db:
        _t = _db.execute(select(Tenant).where(Tenant.code == sys.argv[2])).scalar_one()
        print(f"verified {verify(_db, _t.id, sys.argv[4:], sys.argv[3])} mapping row(s)")
