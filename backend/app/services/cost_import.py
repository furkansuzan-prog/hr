"""Payroll cost import, entity_type = "cost" (wave-2 item 19, ADR-027).

Source: docs/hr/data/WP-6_import_field_dictionaries.md 3.1 + 3.7 and the HR Director review (condition 5: the masked
preview is a mandatory acceptance criterion for users without cost.read; raw cost staging is minimised). Review
conditions override the spec text where they differ.

Permissions: import.write AND cost.write on the WHOLE employer (ADR-017 / ADR-027). Amounts are personal salary data
(KVKK md.4 / md.12, L-13):
- the upload preview shows amounts only to a caller whose cost.read covers every employer they can import into;
  everyone else gets a preview in which every column that is not a mapped non-amount field is masked;
- validation messages, data-quality issues, errors.csv, batch summaries and the audit row of a correction never
  contain an amount - for anybody (those outputs are visible with import.write / audit.read alone);
- after a successful commit the staging rows keep only the mapped NON-amount columns (raw) and no amount (mapped).

Rules:
- one row = one employee x month x cost item (x project); natural key (employment, period_month, cost_type, project).
  is_correction rows are separate rows (negative amounts allowed only there); an identical correction row (same key,
  amount and currency) already stored is skipped as already_imported (idempotent re-import);
- employment resolution on the last day of the month (spec); an employee who left during the month is resolved on any
  day of the month (final payroll of a leaver - documented deviation, ADR-027);
- no default cost item: unmappable -> `unknown_cost_type`; currency "TL" / "YTL" -> TRY + warning `currency_alias`;
  unknown code -> `currency_code`; no fx_rates rate within the 45 days before month end into the reporting currency of
  KPI-019 -> warning `fx_missing` (the cost KPIs return insufficient_data - same rule as labor_cost_total);
- re-import: same key + same amount / currency / cost centre -> `already_imported` (skipped); other values ->
  `already_imported_changed` (skipped) unless uploaded with mode = replace -> corrected in place (record_version + 1,
  audit_logs `cost.correct` naming the changed FIELDS only, warning `cost_corrected`). Closed months are rejected
  (`period_closed`, D-2). Rows are never deleted;
- group check `leave_pay_without_termination` (E.3 #9, L-17): count of unused_leave_pay rows whose employment has no
  end date in that or the previous month (no person list);
- feeds coverage `cost`; commit calls mark_stale_from via imports._invalidate_from_committed (key `period_month`).
"""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.calculators import _fx
from app.analytics.domain import month_end
from app.models import (
    COST_CORRECT_ACTION,
    AuditLog,
    CostCenter,
    DataQualityIssue,
    EmploymentRelationship,
    FxRate,
    ImportBatch,
    ImportStagingRow,
    KpiDefinition,
    KpiFormulaVersion,
    LegalEntity,
    PayrollCost,
    PeriodClose,
    Project,
)
from app.security.access import AccessContext
from app.services import timesheet_import as tsi
from app.services import training_import as tri

COST_FIELDS = {
    "external_id": {"required": True, "aliases": ["sicil", "sicil no", "personel no", "employee id", "employee number",
                                                   "табельный номер"]},
    "source_system": {"required": False, "aliases": ["kaynak", "source", "bordro", "система"]},
    "legal_entity_code": {"required": True, "aliases": ["firma", "firma kodu", "şirket", "company", "employer",
                                                        "компания"]},
    "period_month": {"required": True, "aliases": ["dönem", "bordro dönemi", "ay", "period", "payroll month",
                                                   "период"]},
    "cost_type": {"required": True, "aliases": ["kalem", "ödeme türü", "maliyet kalemi", "cost type",
                                                "вид начисления"]},
    "amount": {"required": True, "aliases": ["tutar", "amount", "сумма"]},
    "currency": {"required": True, "aliases": ["para birimi", "döviz", "currency", "валюта"]},
    "project_code": {"required": False, "aliases": ["proje", "proje kodu", "şantiye", "project", "проект"]},
    "cost_center_code": {"required": False, "aliases": ["masraf yeri", "cost center", "мвз"]},
    "is_correction": {"required": False, "aliases": ["düzeltme", "correction", "корректировка"]},
}
# fields whose source column may be echoed back in the preview to a caller without cost.read (no amount)
SAFE_FIELDS = tuple(f for f in COST_FIELDS if f != "amount")
MODES = ("skip", "replace")
MASK = "***"
MINIMISED = "minimised"
MAX_AMOUNT = Decimal("9999999999999999.99")  # Numeric(18, 2)

_COST_TYPES = {
    "gross wage": "gross_wage", "brut ucret": "gross_wage", "brut maas": "gross_wage", "brut": "gross_wage",
    "начисленная зарплата": "gross_wage",
    "employer ss": "employer_ss", "sgk isveren payi": "employer_ss", "isveren primi": "employer_ss",
    "sgk isveren primi": "employer_ss", "employer social security": "employer_ss", "страховые взносы": "employer_ss",
    "overtime": "overtime", "fazla mesai": "overtime", "fazla calisma": "overtime", "сверхурочные": "overtime",
    "meal": "meal", "yemek": "meal", "питание": "meal",
    "transport": "transport", "yol": "transport", "servis": "transport", "транспорт": "transport",
    "accommodation": "accommodation", "konaklama": "accommodation", "kamp": "accommodation",
    "проживание": "accommodation",
    "benefit": "benefit", "yan hak": "benefit", "yan haklar": "benefit", "льготы": "benefit",
    "unused leave pay": "unused_leave_pay", "kullanilmayan izin ucreti": "unused_leave_pay",
    "izin ucreti": "unused_leave_pay", "компенсация отпуска": "unused_leave_pay",
    "severance": "severance", "kidem tazminati": "severance", "ihbar tazminati": "severance", "kidem": "severance",
    "ihbar": "severance", "выходное пособие": "severance",
}
_CURRENCY_ALIASES = {"TL": "TRY", "YTL": "TRY"}
# ISO 4217 codes accepted without an fx_rates row (any code present in fx_rates is accepted as well)
_ISO_CODES = {"TRY", "USD", "EUR", "GBP", "CHF", "JPY", "CNY", "RUB", "KZT", "AZN", "GEL", "UZS", "TMT", "KGS", "UAH",
              "BYN", "SAR", "AED", "QAR", "KWD", "BHD", "OMR", "IQD", "LYD", "DZD", "EGP", "MAD", "TND", "ILS", "SEK",
              "NOK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN", "RSD", "CAD", "AUD", "INR", "PKR", "TJS", "AFN"}


def normalize_options(raw: dict | None) -> dict:
    raw = raw or {}
    if raw.get("approved_source") is not None or raw.get("all_completed") is not None:
        raise ValueError("approved_source / all_completed are not options of entity_type 'cost'")
    mode = raw.get("mode") or "skip"
    if mode not in MODES:
        raise ValueError("mode must be 'skip' or 'replace'")
    return {"mode": mode}


def prohibited_columns(headers: list[str]) -> list[str]:
    return tri.prohibited_columns(headers, COST_FIELDS)


def write_scope(ctx: AccessContext) -> set[int] | None:
    """Employers the caller may import costs into: whole-entity import.write AND whole-entity cost.write."""
    from app.services.imports import _import_scope

    imp = _import_scope(ctx)
    cw = ctx.entity_filter("cost.write")
    cw = None if cw is None else {e for e in cw if ctx.covers_entity("cost.write", e)}
    if imp is None:
        return cw
    return imp if cw is None else imp & cw


def amounts_visible(ctx: AccessContext) -> bool:
    """Amounts may be echoed (upload preview) only when cost.read covers every employer the caller can import into."""
    if not ctx.has("cost.read") or not ctx.has("cost.write"):
        return False
    cr = ctx.entity_filter("cost.read")
    if cr is None:
        return True
    scope = write_scope(ctx)
    return scope is not None and all(ctx.covers_entity("cost.read", e) for e in scope)


def mask_preview(rows: list[dict], mapping: dict, visible: bool) -> list[dict]:
    if visible:
        return rows
    safe = {mapping.get(f) for f in SAFE_FIELDS if mapping.get(f)}
    return [{k: (v if k in safe else MASK) for k, v in r.items()} for r in rows]


def parse_amount(v: str) -> Decimal:
    """'10000,00', '1.234,50', '1,234.50', '1 234,50', '-500' -> Decimal with at most 2 decimals. Thousand separators
    are recognised for amounts only (WP-6 3.1)."""
    s = (v or "").strip().replace(" ", "").replace(" ", "")
    if not re.fullmatch(r"[-+]?[\d.,]+", s):
        raise ValueError
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(",", ".")
    elif "," in s:
        if s.count(",") > 1:
            raise ValueError
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        raise ValueError
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError from None
    if not d.is_finite() or d.as_tuple().exponent < -2 or abs(d) > MAX_AMOUNT:
        raise ValueError
    return d.quantize(Decimal("0.01"))


def parse_month(v: str) -> date:
    s = (v or "").strip()
    for pat, order in ((r"(\d{4})[-/.](\d{1,2})", "ym"), (r"(\d{1,2})[-/.](\d{4})", "my")):
        mt = re.fullmatch(pat, s)
        if mt:
            a, b = int(mt.group(1)), int(mt.group(2))
            y, mo = (a, b) if order == "ym" else (b, a)
            if 1 <= mo <= 12:
                return date(y, mo, 1)
            raise ValueError(f"invalid period '{s}'")
    from app.services.imports import _parse_date

    d = _parse_date(s)
    if d is None:
        raise ValueError("empty period")
    return date(d.year, d.month, 1)


def reporting_currency(db: Session) -> str:
    """Reporting currency of the current KPI-019 formula version (labor_cost_total converts into it); TRY default."""
    params = db.execute(select(KpiFormulaVersion.parameters).join(
        KpiDefinition, KpiDefinition.id == KpiFormulaVersion.kpi_id).where(
        KpiDefinition.code == "KPI-019", KpiFormulaVersion.is_current.is_(True))).scalar()
    return (params or {}).get("reporting_currency") or "TRY"


def _resolve(resolve: "tsi._Resolver", src: str, external_id: str, entity_id: int, month: date) -> list:
    found = resolve(src, external_id, entity_id, month_end(month))
    if found:
        return found
    me = month_end(month)
    out = []
    for spells in resolve.cache.get((src, external_id, entity_id), ()):
        out = [e for e in spells if e.start_date <= me and (e.end_date is None or e.end_date >= month)]
        if out:
            break
    return out


def validate(db: Session, ctx: AccessContext, batch: ImportBatch, mapping: dict) -> dict:
    from app.services import coverage as cov
    from app.services.imports import OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, summary

    opts = normalize_options(batch.options)
    scope = write_scope(ctx)
    this_month = date.today().replace(day=1)
    rep = reporting_currency(db)
    ents = {le.code: le for le in db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant_id)).scalars()}
    projects = {p.code: p.id for p in db.execute(select(Project).where(Project.tenant_id == ctx.tenant_id)).scalars()}
    centers: dict[str, list[CostCenter]] = {}
    for c in db.execute(select(CostCenter).where(CostCenter.tenant_id == ctx.tenant_id)).scalars():
        centers.setdefault(c.code, []).append(c)
    closed = set(db.execute(select(PeriodClose.period_month).where(
        PeriodClose.tenant_id == ctx.tenant_id, PeriodClose.status == "closed")).scalars())
    known_ccy = _ISO_CODES | set(db.execute(select(FxRate.from_ccy).distinct()).scalars()) \
        | set(db.execute(select(FxRate.to_ccy).distinct()).scalars())
    fx_cache: dict[tuple[str, date], bool] = {}
    resolve = tsi._Resolver(db, ctx.tenant_id)
    staged = list(db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
                             .order_by(ImportStagingRow.row_number)).scalars())
    parsed: list[tuple[ImportStagingRow, dict, list[dict]]] = []
    for row in staged:
        m = {f: (str(row.raw.get(src, "") or "").strip() if src else "") for f, src in mapping.items()}
        errs: list[dict] = []

        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        for f, meta in COST_FIELDS.items():
            if meta["required"] and not m.get(f):
                err("required", f"'{f}' is required")
        src = m.get("source_system") or "import"
        if len(src) > 50:
            err("field_too_long", "source_system longer than 50 characters")
        le = ents.get(m.get("legal_entity_code", ""))
        if m.get("legal_entity_code") and le is None:
            err("unknown_legal_entity", f"legal entity '{m['legal_entity_code']}' not found")
        elif le is not None and scope is not None and le.id not in scope:
            err("scope", f"no cost import permission (import.write + cost.write) for legal entity '{le.code}'")
        month = None
        if m.get("period_month"):
            try:
                month = parse_month(m["period_month"])
            except ValueError:
                err("date_format", "period_month must be YYYY-MM or a date")
        if month is not None and month > this_month:
            err("future_date", "period_month is after the current month")
            month = None
        if month is not None and month in closed:
            err("period_closed", "period_month is a closed period (D-2: only the main contractor can reopen it)")
        ctype = None
        if m.get("cost_type"):
            ctype = _COST_TYPES.get(tri._fold(m["cost_type"]))
            if ctype is None:
                err("unknown_cost_type", f"cost item '{m['cost_type']}' not recognised; no default item is assigned")
        is_corr = False
        if m.get("is_correction"):
            b = tri.parse_bool(m["is_correction"])
            if b is None:
                err("bool_format", "is_correction must be yes / no")
            else:
                is_corr = b
        amount = None
        if m.get("amount"):
            try:
                amount = parse_amount(m["amount"])
            except ValueError:
                err("amount_format", "amount is not a number with at most 2 decimals (value not shown)")
            if amount is not None and amount < 0 and not is_corr:
                err("negative_amount", "negative amount without the correction flag (value not shown)")
                amount = None
        ccy = None
        if m.get("currency"):
            c = m["currency"].strip().upper()
            if c in _CURRENCY_ALIASES:
                err("currency_alias", f"currency '{c}' converted to {_CURRENCY_ALIASES[c]}", "warning")
                c = _CURRENCY_ALIASES[c]
            if len(c) != 3 or not c.isalpha() or c not in known_ccy:
                err("currency_code", f"currency '{m['currency'][:10]}' is not a known ISO 4217 code")
            else:
                ccy = c
        if ccy and month is not None and ccy != rep:
            k = (ccy, month)
            if k not in fx_cache:
                fx_cache[k] = _fx(db, ccy, rep, month_end(month)) is not None
            if not fx_cache[k]:
                err("fx_missing", f"no {ccy}->{rep} rate within 45 days before month end -> loaded; cost KPIs of this "
                                  "month are insufficient_data until the rate exists", "warning")
        prj = None
        if m.get("project_code"):
            prj = projects.get(m["project_code"])
            if prj is None:
                err("unknown_project", f"project '{m['project_code']}' not found")
        cc = None
        if m.get("cost_center_code"):
            cands = [c for c in centers.get(m["cost_center_code"], []) if le is None or c.legal_entity_id == le.id]
            if len(cands) == 1:
                cc = cands[0].id
            else:
                err("unknown_cost_center", f"cost centre '{m['cost_center_code']}' not found for the employer -> "
                                           "stored without cost centre", "warning")
        emp = None
        if le is not None and month is not None and m.get("external_id") and \
                not [e for e in errs if e["rule"] == "scope"]:
            found = _resolve(resolve, src, m["external_id"], le.id, month)
            if not found:
                err("no_employment", f"no employment of '{m['external_id']}' with '{le.code}' in "
                                     f"{month.isoformat()[:7]}")
            elif len(found) > 1:
                err("ambiguous_employment", "several employments match; resolve the identity first")
            else:
                emp = found[0]
        mp = {"external_id": m.get("external_id"), "legal_entity_code": m.get("legal_entity_code"),
              "legal_entity_id": le.id if le else None, "employment_id": emp.id if emp else None,
              "source_system": src, "period_month": month.isoformat() if month else None, "cost_type": ctype,
              "amount": str(amount) if amount is not None else None, "currency": ccy, "project_id": prj,
              "cost_center_id": cc, "is_correction": is_corr}
        parsed.append((row, mp, errs))

    emp_ids = {mp["employment_id"] for _, mp, _ in parsed if mp["employment_id"]}
    months = [mp["period_month"] for _, mp, _ in parsed if mp["period_month"]]
    existing: dict[tuple, PayrollCost] = {}
    corrections: set[tuple] = set()
    if emp_ids and months:
        for c in db.execute(select(PayrollCost).where(
                PayrollCost.tenant_id == ctx.tenant_id, PayrollCost.employment_id.in_(emp_ids),
                PayrollCost.period_month >= date.fromisoformat(min(months)),
                PayrollCost.period_month <= date.fromisoformat(max(months))).order_by(PayrollCost.id)).scalars():
            k = (c.employment_id, c.period_month.isoformat(), c.cost_type, c.project_id)
            if c.is_correction:
                corrections.add((*k, str(Decimal(c.amount).quantize(Decimal("0.01"))), c.currency))
            else:
                existing.setdefault(k, c)
    seen: set[tuple] = set()
    for _, mp, errs in parsed:
        def err(rule, msg, sev="error", errs=errs):
            errs.append({"rule": rule, "message": msg, "severity": sev})

        mp["existing_id"], mp["replace"], mp["skip"] = None, False, False
        if mp["legal_entity_id"] is not None and batch.coverage and mp["period_month"]:
            mo = date.fromisoformat(mp["period_month"])
            if cov.row_outside(batch.coverage, mp["legal_entity_id"], mo, month_end(mo), mp["project_id"]):
                err(OUTSIDE_COVERAGE, OUTSIDE_COVERAGE_MSG, "warning")
        if not mp["employment_id"] or not mp["period_month"] or not mp["cost_type"]:
            continue
        key = (mp["employment_id"], mp["period_month"], mp["cost_type"], mp["project_id"])
        if mp["is_correction"]:
            key = (*key, mp["amount"], mp["currency"])
        if key in seen:
            err("duplicate_in_file", "duplicate row (same employee, month, cost item and project) in file")
            continue
        seen.add(key)
        if [e for e in errs if e["severity"] == "error"]:
            continue
        if mp["is_correction"]:
            if key in corrections:
                mp["skip"] = True
                err("already_imported", "identical correction row already stored -> will be skipped", "warning")
            continue
        ex = existing.get(key)
        if ex is None:
            continue
        mp["existing_id"] = ex.id
        changed = _changed(ex, mp)
        if not changed:
            mp["skip"] = True
            err("already_imported", "identical cost row already stored -> will be skipped", "warning")
        elif opts["mode"] == "replace":
            mp["replace"] = True
            err("cost_corrected", "stored row differs -> corrected in place on commit (mode = replace; fields: "
                                  + ", ".join(changed) + ")", "warning")
        else:
            mp["skip"] = True
            err("already_imported_changed", "a row for this employee, month, cost item and project is already stored "
                                            "with other values -> skipped; upload the corrected file with mode = "
                                            "replace or send a correction row", "warning")

    valid = errors = 0
    issues = []
    for row, mp, errs in parsed:
        row.mapped, row.errors = mp, errs
        row.has_error = any(e["severity"] == "error" for e in errs)
        errors += row.has_error
        valid += not row.has_error
        for e in errs:
            issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "cost",
                           "rule_code": e["rule"], "severity": e["severity"], "message": e["message"],
                           "row_number": row.row_number, "record_ref": mp.get("external_id")})
    group = _leave_pay_check(db, [mp for row, mp, _ in parsed if not row.has_error])
    if batch.dropped_columns:
        group.append(("prohibited_columns_dropped", "name / identity / health column(s) dropped before storage (names "
                                                    "only, values never stored): " + ", ".join(batch.dropped_columns)))
    for rule, msg in group:
        issues.append({"tenant_id": ctx.tenant_id, "import_batch_id": batch.id, "entity_type": "cost",
                       "rule_code": rule, "severity": "warning", "message": msg, "row_number": None,
                       "record_ref": None})
    if issues:
        db.bulk_insert_mappings(DataQualityIssue, issues)
    batch.options = {**opts, "reporting_currency": rep}
    batch.valid_rows, batch.error_rows, batch.status = valid, errors, "validated"
    db.commit()
    return summary(db, ctx, batch.id)


def _changed(ex: PayrollCost, mp: dict) -> list[str]:
    out = []
    if Decimal(ex.amount).quantize(Decimal("0.01")) != Decimal(mp["amount"]):
        out.append("amount")
    if ex.currency != mp["currency"]:
        out.append("currency")
    if ex.cost_center_id != mp["cost_center_id"]:
        out.append("cost_center")
    return out


def _leave_pay_check(db: Session, rows: list[dict]) -> list[tuple[str, str]]:
    """E.3 #9 / L-17: unused_leave_pay without an employment end in that or the previous month (count only)."""
    lp = [r for r in rows if r["cost_type"] == "unused_leave_pay" and r["employment_id"] and r["period_month"]]
    if not lp:
        return []
    ends = dict(db.execute(select(EmploymentRelationship.id, EmploymentRelationship.end_date).where(
        EmploymentRelationship.id.in_({r["employment_id"] for r in lp}))).all())
    bad = set()
    for r in lp:
        mo = date.fromisoformat(r["period_month"])
        prev = (mo - timedelta(days=1)).replace(day=1)
        ed = ends.get(r["employment_id"])
        if ed is None or not prev <= ed <= month_end(mo):
            bad.add((r["employment_id"], mo))
    if not bad:
        return []
    return [("leave_pay_without_termination", f"{len(bad)} employee-month(s) with unused leave pay but no employment "
             "end in that or the previous month (E.3; 4857 md. 59 - the person list is for HR operations)")]


def commit_row(db: Session, ctx: AccessContext, row: ImportStagingRow) -> date | None:
    """Insert, or (mode = replace, decided at validation) correct the stored key row in place with record_version + 1
    and an audit row naming the changed fields (no amounts). Skipped / identical rows return None. Never deletes."""
    from app.services.imports import ImportError_

    mp = row.mapped
    if mp.get("skip") or mp.get("amount") is None:
        return None
    mo = date.fromisoformat(mp["period_month"])
    emp = db.get(EmploymentRelationship, mp["employment_id"])
    if emp is None or emp.tenant_id != ctx.tenant_id:
        raise ImportError_("employment disappeared between validation and commit")
    base = select(PayrollCost).where(PayrollCost.employment_id == emp.id, PayrollCost.period_month == mo,
                                     PayrollCost.cost_type == mp["cost_type"],
                                     PayrollCost.project_id.is_not_distinct_from(mp["project_id"]))
    amount = Decimal(mp["amount"])
    if mp["is_correction"]:
        dup = db.execute(base.where(PayrollCost.is_correction.is_(True), PayrollCost.amount == amount,
                                    PayrollCost.currency == mp["currency"]).limit(1)).scalar()
        if dup is not None:
            return None  # resumed commit
    else:
        ex = db.execute(base.where(PayrollCost.is_correction.is_(False)).order_by(PayrollCost.id).limit(1)).scalar()
        if ex is not None:
            changed = _changed(ex, mp)
            if not changed or not mp.get("replace"):
                return None
            ex.amount, ex.currency, ex.cost_center_id = amount, mp["currency"], mp["cost_center_id"]
            ex.record_version = (ex.record_version or 1) + 1
            ex.updated_by = ctx.user_id
            db.add(AuditLog(tenant_id=ctx.tenant_id, user_id=ctx.user_id, action=COST_CORRECT_ACTION,
                            entity_type="payroll_cost", entity_id=str(ex.id),
                            details={"legal_entity_id": emp.legal_entity_id, "period_month": mo.isoformat(),
                                     "cost_type": ex.cost_type, "import_batch_id": row.batch_id,
                                     "record_version": ex.record_version, "changed_fields": changed}))
            return mo
    db.add(PayrollCost(tenant_id=ctx.tenant_id, employment_id=emp.id, project_id=mp["project_id"],
                       cost_center_id=mp["cost_center_id"], period_month=mo, cost_type=mp["cost_type"],
                       amount=amount, currency=mp["currency"], is_correction=mp["is_correction"],
                       source_system=mp["source_system"], source_record_id=mp["external_id"],
                       updated_by=ctx.user_id))
    return mo


def minimise_staging(db: Session, batch: ImportBatch) -> None:
    """After a successful commit (L-13 safe default, review condition 5): raw keeps only the mapped non-amount columns,
    mapped keeps no amount. Error rows included."""
    keep = {(batch.mapping or {}).get(f) for f in SAFE_FIELDS} - {None, ""}
    for r in db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)).scalars():
        raw, mapped = r.raw or {}, r.mapped or {}
        if set(raw) - keep:
            r.raw = {k: v for k, v in raw.items() if k in keep}
        if mapped.get("amount") is not None:
            r.mapped = {**mapped, "amount": MINIMISED}
