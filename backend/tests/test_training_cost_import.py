"""Training import `training` and payroll cost import `cost` (wave-2 items 18 / 19, ADR-027).

Hand-calculated expectations: docs/hr/data/WP-6_import_field_dictionaries.md 3.1, 3.3, 3.7, section 4 (K1, K2, K8)
and the HR Director review (condition 4: breakdown only, no statutory-minimum note; condition 5: masked cost preview).

Tenant TCI (sample files). ANA-01 owns PRJ-A / PRJ-B; ALT-07 is a second employer. June 2026 is closed.
  Training sample 3.3.3: row 2 P-0001 ANA-01 ISG-TEMEL-16 "İSG" ISG_TEMEL 16 h 07.10 -> valid, category hse,
      is_statutory_hse = true; row 3 P-0002 ALT-07 OP-VINC-01 "mesleki" OPERATOR_BELGE 8 h -> valid, technical, not
      statutory; row 4 P-0003 ISG-YEN-08 -4 h -> error hours_range.
  Cost sample 3.7.2: row 2 P-0001 2026-10 "brüt ücret" 10000,00 TRY PRJ-A -> valid gross_wage 10,000.00; row 3
      "SGK işveren payı" 2000,00 -> valid employer_ss 2,000.00; row 4 P-0004 ALT-07 "fazla mesai" -500,00 TL ->
      error negative_amount + warning currency_alias (TL -> TRY).

Tenant TCK (K1 / K2 / K8). Firm KA = 100 persons, firm KB = 50 persons, constant from 2025-01-01; employment declared
complete; min_feed_coverage 0.8; coverage mode warn (D-13 default).
  K1 KPI-008 Q3 2026 (92 days): KA 100 x 12 h in August (50 x HSE ISG_TEMEL, 50 x technical) imported with a
      'complete' Q3 declaration; KB header-only file 'complete' -> confirmed_empty.
      1,200 / 150 = 8.0 h; annualised 8.0 x 365 / 92 = 31.7391 -> good (30/20/16); C = 1, quality 100.
      Breakdown: hse 600 h, technical 600 h, compliance 0, soft 0; statutory HSE 600 h -> 600 / 150 x 365 / 92 =
      15.87 h per employee per year.
  K2 KB's training coverage removed: C = 300 / 450 = 0.6667 < 0.8 -> warn: value 8.0, no colour, quality 66.7.
  K8 KPI-019 October 2026: KA 100 x 50.000,00 TRY gross wage = 5,000,000 'complete'; KB undeclared ->
      5,000,000 / 150 = 33,333.33 with C = 100 / 150 = 0.6667 -> warn: no colour, quality 66.7.
      KB 50 x "40.000,00" (thousand separator) 'complete' -> 7,000,000 / 150 = 46,666.67, C = 1.
"""
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.analytics import engine
from app.models import (
    COST_CORRECT_ACTION,
    TRAINING_CORRECT_ACTION,
    AuditLog,
    DataFeedCoverage,
    DataQualityIssue,
    EmploymentRelationship,
    FxRate,
    ImportBatch,
    ImportStagingRow,
    LegalEntity,
    PayrollCost,
    PeriodClose,
    Person,
    Project,
    Role,
    RolePermission,
    Tenant,
    TrainingRecord,
    User,
    UserRoleAssignment,
)
from app.security.access import AccessContext
from app.security.auth import hash_password
from app.security.catalog import COST_WRITE_ROLES
from app.seed.reference import seed_permissions_and_roles, seed_reference
from app.services import cost_import as cst
from app.services import coverage as cov
from app.services import imports as imp
from app.services.imports import suggest_mapping
from app.services.training_import import TRAINING_FIELDS

D = date
ADMIN = "tc.admin@tci.local"
HRMAN = "tc.hrman@tci.local"  # HR_MANAGER: import.write, no cost.write / cost.read
PAYROLL = "tc.payroll@tci.local"  # custom role: import.write + cost.write, NO cost.read
PAYROLL_READ = "tc.payread@tci.local"  # custom role: import.write + cost.write + cost.read
KADMIN = "tc.admin@tck.local"

TR_HDR = "sicil;firma;eğitim kodu;eğitim adı;kategori;yasal dayanak;saat;tamamlanma tarihi;durum"
TR_SAMPLE = [  # WP-6 data 3.3.3, verbatim
    "P-0001;ANA-01;ISG-TEMEL-16;Temel İSG eğitimi;İSG;ISG_TEMEL;16;2026-10-07;completed",
    "P-0002;ALT-07;OP-VINC-01;Mobil vinç operatör tazeleme;mesleki;OPERATOR_BELGE;8;2026-10-08;completed",
    "P-0003;ANA-01;ISG-YEN-08;İSG yenileme;İSG;ISG_YENILEME;-4;2026-10-09;completed",
]
COST_HDR = "sicil;firma;dönem;kalem;tutar;para birimi;proje"
COST_SAMPLE = [  # WP-6 data 3.7.2, verbatim
    "P-0001;ANA-01;2026-10;brüt ücret;10000,00;TRY;PRJ-A",
    "P-0001;ANA-01;2026-10;SGK işveren payı;2000,00;TRY;PRJ-A",
    "P-0004;ALT-07;2026-10;fazla mesai;-500,00;TL;PRJ-B",
]
AMOUNT_STRINGS = ("10000", "2000", "500", "77777", "12345")


def _user(db, t, roles, email, role_id, ent=None):
    u = User(tenant_id=t.id, email=email, full_name=email.split("@")[0], password_hash=hash_password("Demo!2345"),
             locale="en")
    db.add(u)
    db.flush()
    db.add(UserRoleAssignment(user_id=u.id, role_id=role_id, legal_entity_id=ent, scope_type="legal_entity" if ent
                              else "tenant"))


def _custom_role(db, t, code, perms):
    r = Role(tenant_id=t.id, code=code, name=code, is_system=False)
    db.add(r)
    db.flush()
    for p in perms:
        db.add(RolePermission(role_id=r.id, permission_code=p))
    return r.id


@pytest.fixture(scope="module")
def tc(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="TCI", name="Training / cost import test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        ents = {"A": LegalEntity(tenant_id=t.id, code="ANA-01", name="Ana", entity_type="main_contractor"),
                "B": LegalEntity(tenant_id=t.id, code="ALT-07", name="Alt", entity_type="subcontractor")}
        db.add_all(ents.values())
        db.flush()
        prj = {c: Project(tenant_id=t.id, code=c, name=c, owner_entity_id=ents["A"].id) for c in ("PRJ-A", "PRJ-B")}
        db.add_all(prj.values())
        db.flush()
        emps = {}
        for code, ent in (("P-0001", "A"), ("P-0002", "B"), ("P-0003", "A"), ("P-0004", "B"), ("P-0005", "A")):
            p = Person(tenant_id=t.id, person_code=code, first_name="Tc", last_name=code)
            db.add(p)
            db.flush()
            e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ents[ent].id,
                                       employee_number=code, start_date=D(2024, 1, 1))
            db.add(e)
            db.flush()
            emps[code] = e.id
        # P-0005 left on 15.09.2026: the September payroll still resolves (final payroll of a leaver)
        db.get(EmploymentRelationship, emps["P-0005"]).end_date = D(2026, 9, 15)
        db.add(PeriodClose(tenant_id=t.id, period_month=D(2026, 6, 1), status="closed"))
        db.add(FxRate(rate_date=D(2026, 10, 20), from_ccy="CHF", to_ccy="TRY", rate=40, source="TCI-TEST"))
        _user(db, t, roles, ADMIN, roles["SYSTEM_ADMIN"])
        _user(db, t, roles, HRMAN, roles["HR_MANAGER"])
        _user(db, t, roles, PAYROLL, _custom_role(db, t, "PAYROLL_CLERK", ["import.write", "cost.write"]))
        _user(db, t, roles, PAYROLL_READ, _custom_role(db, t, "PAYROLL_LEAD", ["import.write", "cost.write",
                                                                                "cost.read"]))
        db.commit()
        return {"tenant_id": t.id, "entities": {k: v.id for k, v in ents.items()},
                "projects": {k: v.id for k, v in prj.items()}, "emps": emps}


@pytest.fixture(scope="module")
def tk(database):
    """K1 / K2 / K8 tenant: KA 100 persons, KB 50 persons."""
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="TCK", name="Training / cost K-examples tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        ka = LegalEntity(tenant_id=t.id, code="KA", name="KA", entity_type="main_contractor")
        kb = LegalEntity(tenant_id=t.id, code="KB", name="KB", entity_type="main_contractor")
        db.add_all([ka, kb])
        db.flush()
        for code, ent, n in (("KA", ka, 100), ("KB", kb, 50)):
            for i in range(n):
                p = Person(tenant_id=t.id, person_code=f"{code}{i:03d}", first_name="K", last_name=f"{code}{i}")
                db.add(p)
                db.flush()
                db.add(EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ent.id,
                                              employee_number=f"{code}{i:03d}", start_date=D(2025, 1, 1)))
        _user(db, t, roles, KADMIN, roles["SYSTEM_ADMIN"])
        cov.declare_complete(db, t.id, [ka.id, kb.id], ["employment"], D(2026, 1, 1), D(2026, 10, 31), note="test")
        db.commit()
        return {"tenant_id": t.id, "KA": ka.id, "KB": kb.id}


def _csv(rows, header):
    return (header + "\n" + "\n".join(rows) + "\n").encode()


def _upload(client, auth, content, name, user=ADMIN, coverage=None, status=201, **form):
    data = {"entity_type": form.pop("entity_type", "training"), **{k: str(v).lower() if isinstance(v, bool) else v
                                                                   for k, v in form.items()}}
    if coverage is not None:
        data["coverage"] = json.dumps(coverage)
    r = client.post("/api/v1/imports", headers=auth(user), files={"file": (name, content)}, data=data)
    assert r.status_code == status, r.text
    return r.json()


def _run(client, auth, content, name, user=ADMIN, coverage=None, mapping=None, commit=True, **form):
    up = _upload(client, auth, content, name, user, coverage, **form)
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={"mapping": mapping}, headers=auth(user))
    assert v.status_code == 200, v.text
    v = v.json()
    c = None
    if commit and (v["valid_rows"] or not v["total_rows"]):
        c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(user))
        assert c.status_code == 200, c.text
        c = c.json()
    return up, v, c


def _issues(db, batch_id) -> dict[int | None, set[str]]:
    out: dict[int | None, set[str]] = {}
    for i in db.execute(select(DataQualityIssue).where(DataQualityIssue.import_batch_id == batch_id)).scalars():
        out.setdefault(i.row_number, set()).add(i.rule_code)
    return out


def _training(db, tc, code):
    db.expire_all()
    return list(db.execute(select(TrainingRecord).where(TrainingRecord.tenant_id == tc["tenant_id"],
                                                        TrainingRecord.employment_id == tc["emps"][code])
                           .order_by(TrainingRecord.id)).scalars())


def _costs(db, tc, code):
    db.expire_all()
    return list(db.execute(select(PayrollCost).where(PayrollCost.tenant_id == tc["tenant_id"],
                                                     PayrollCost.employment_id == tc["emps"][code])
                           .order_by(PayrollCost.id)).scalars())


# ------------------------------------------------------------------ training (item 18)


def test_training_import_field_set_and_aliases(client, auth, db, tc):
    sug = suggest_mapping(TR_HDR.split(";"), TRAINING_FIELDS)
    assert {k for k, v in sug.items() if v} == {"external_id", "legal_entity_code", "course_code", "course_name",
                                                "category", "legal_basis", "hours", "completed_on", "status"}
    assert set(imp.FIELD_SETS) >= {"training", "cost"} and cov.IMPORT_FEEDS["training"] == "training"
    up, v, c = _run(client, auth, _csv(TR_SAMPLE, TR_HDR), "training_sample.csv")
    assert up["dropped_columns"] == []  # "eğitim adı" is the course name, never dropped as a person's name
    assert v["valid_rows"] == 2 and v["error_rows"] == 1 and c["committed_rows"] == 2
    iss = _issues(db, up["batch_id"])
    assert "hours_range" in iss[4]  # WP-6 3.3.3 row 4: -4 h
    t1, = _training(db, tc, "P-0001")
    assert (t1.category, t1.legal_basis, float(t1.hours), t1.completed_on) == ("hse", "ISG_TEMEL", 16.0, D(2026, 10, 7))
    t2, = _training(db, tc, "P-0002")
    assert (t2.category, t2.legal_basis, float(t2.hours)) == ("technical", "OPERATOR_BELGE", 8.0)
    assert t1.mandatory is False and t1.source_record_id == "P-0001"
    rows = {r.row_number: r.mapped for r in db.execute(select(ImportStagingRow).where(
        ImportStagingRow.batch_id == up["batch_id"])).scalars()}
    assert rows[2]["is_statutory_hse"] is True and rows[3]["is_statutory_hse"] is False
    assert _training(db, tc, "P-0003") == []


def test_training_import_unknown_category_rejected_no_default(client, auth, db, tc):
    rows = ["P-0003;ANA-01;X-01;Bilinmeyen;yoga;;2;2026-10-01;completed",
            "P-0003;ANA-01;X-02;Uyum;yasal;ISG_TEMEL;2;2026-10-01;completed",
            "P-0003;ANA-01;X-03;Katılım;teknik;;2;2026-10-01;katıldı",
            "P-0003;ANA-01;X-04;Bos durum;teknik;;2;2026-10-01;",
            "P-0003;ANA-01;X-05;Dayanak;teknik;KANUN_99;2;2026-10-01;completed"]
    up, v, _c = _run(client, auth, _csv(rows, TR_HDR), "training_cat.csv")
    iss = _issues(db, up["batch_id"])
    assert "unknown_training_category" in iss[2] and v["error_rows"] == 1
    assert "legal_basis_category_mismatch" in iss[3]  # compliance + ISG_* -> not statutory HSE
    assert "training_not_completed" in iss[4] and "completion_unknown" in iss[5]
    assert "unknown_legal_basis" in iss[6]
    stored = {t.course_code: t for t in _training(db, tc, "P-0003")}
    assert set(stored) == {"X-02", "X-05"}  # attended / blank status never stored, no default category
    assert stored["X-05"].legal_basis is None and stored["X-02"].category == "compliance"


def test_training_status_column_or_all_completed_required(client, auth, db, tc):
    hdr = "sicil;firma;eğitim kodu;eğitim adı;kategori;saat;tamamlanma tarihi"
    content = _csv(["P-0003;ANA-01;N-01;Ilk yardim;technical;4;2026-10-02"], hdr)
    up = _upload(client, auth, content, "nostatus.csv")
    r = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN))
    assert r.status_code == 400 and "all_completed" in r.json()["detail"]
    _upload(client, auth, content, "x.csv", all_completed="maybe", status=422)
    _upload(client, auth, content, "x.csv", approved_source=True, status=400)  # timesheet option only
    up, v, c = _run(client, auth, content, "nostatus2.csv", all_completed=True)
    assert v["valid_rows"] == 1 and c["committed_rows"] == 1
    assert "N-01" in {t.course_code for t in _training(db, tc, "P-0003")}


def test_training_dates_hours_and_closed_period(client, auth, db, tc):
    hdr = TR_HDR + ";başlangıç"
    rows = ["P-0005;ANA-01;D-01;Kapali donem;teknik;;8;2026-06-10;completed;",
            "P-0005;ANA-01;D-02;Gelecek;teknik;;8;2099-01-01;completed;",
            "P-0005;ANA-01;D-03;Uzun;teknik;;30;2026-08-03;completed;",
            "P-0005;ANA-01;D-04;Iki gun;teknik;;49;2026-08-05;completed;2026-08-04",
            "P-0005;ANA-01;D-05;Ters;teknik;;8;2026-08-05;completed;2026-08-06",
            "P-0005;ANA-01;D-06;Ayrilik sonrasi;teknik;;8;2026-10-05;completed;"]
    up, v, _c = _run(client, auth, _csv(rows, hdr), "training_dates.csv")
    iss = _issues(db, up["batch_id"])
    assert "period_closed" in iss[2] and "future_date" in iss[3]
    assert "hours_exceed_single_day" in iss[4] and v["valid_rows"] == 1  # 30 h without start date: warning
    assert "hours_range" in iss[5]  # 49 h > 24 h x 2 days
    assert "date_order" in iss[6]
    assert "no_employment" in iss[7]  # P-0005 left on 15.09.2026
    assert [t.course_code for t in _training(db, tc, "P-0005")] == ["D-03"]


def test_training_reimport_skip_changed_and_replace_with_audit(client, auth, db, tc):
    base = "P-0001;ANA-01;RE-01;Yeniden;teknik;;6;2026-09-01;completed"
    _run(client, auth, _csv([base], TR_HDR), "re1.csv")
    up, _v, _c = _run(client, auth, _csv([base], TR_HDR) + b"\n", "re2.csv")  # same rows, other file
    assert "already_imported" in _issues(db, up["batch_id"])[2]
    changed = base.replace(";6;", ";7,5;")
    up, _v, _c = _run(client, auth, _csv([changed], TR_HDR), "re3.csv")
    assert "already_imported_changed" in _issues(db, up["batch_id"])[2]
    rec = [t for t in _training(db, tc, "P-0001") if t.course_code == "RE-01"]
    assert len(rec) == 1 and float(rec[0].hours) == 6.0 and rec[0].record_version == 1
    up, _v, _c = _run(client, auth, _csv([changed], TR_HDR) + b"\n", "re4.csv", mode="replace")
    assert "training_corrected" in _issues(db, up["batch_id"])[2]
    rec = [t for t in _training(db, tc, "P-0001") if t.course_code == "RE-01"]
    assert len(rec) == 1 and float(rec[0].hours) == 7.5 and rec[0].record_version == 2
    a = db.execute(select(AuditLog).where(AuditLog.action == TRAINING_CORRECT_ACTION,
                                          AuditLog.entity_id == str(rec[0].id))).scalar_one()
    assert a.details["changed"]["hours"] == {"from": 6.0, "to": 7.5}
    dup = _upload(client, auth, _csv([base, base], TR_HDR), "dup.csv")
    client.post(f"/api/v1/imports/{dup['batch_id']}/validate", json={}, headers=auth(ADMIN))
    assert "duplicate_in_file" in _issues(db, dup["batch_id"])[3]


def test_training_kvkk_columns_dropped_and_minimised(client, auth, db, tc):
    hdr = TR_HDR + ";ad soyad;tc kimlik;not"
    up, _v, _c = _run(client, auth, _csv(["P-0003;ANA-01;K-01;Kvkk;teknik;;2;2026-10-03;completed;Ali Veli;12345678901;"
                                        "serbest metin"], hdr), "kvkk.csv")
    assert set(up["dropped_columns"]) == {"ad soyad", "tc kimlik"} and "Ali Veli" not in json.dumps(up)
    raw = db.execute(select(ImportStagingRow.raw).where(ImportStagingRow.batch_id == up["batch_id"])).scalar_one()
    assert "not" not in raw and "ad soyad" not in raw  # unmapped column minimised after commit


def test_training_coverage_and_stale_marking(client, auth, db, tc, monkeypatch):
    calls = []
    real = imp.mark_stale_from
    monkeypatch.setattr(imp, "mark_stale_from", lambda db_, tid, d: (calls.append(d), real(db_, tid, d)))
    decl = {"period_start": "2026-08-01", "period_end": "2026-08-31", "legal_entity_codes": ["ANA-01"],
            "completeness": "complete"}
    up, _v, c = _run(client, auth, _csv(["P-0003;ANA-01;C-01;Kapsam;teknik;;2;2026-08-12;completed",
                                        "P-0003;ANA-01;C-02;Kapsam;teknik;;2;2026-08-20;completed"], TR_HDR),
                    "cov.csv", coverage=decl)
    assert calls and min(calls) == D(2026, 8, 12)  # INTEGRATION_CONTRACTS section 5: earliest completed_on
    r = db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == tc["tenant_id"], DataFeedCoverage.feed_type == "training",
        DataFeedCoverage.legal_entity_id == tc["entities"]["A"], DataFeedCoverage.period_month == D(2026, 8, 1))
    ).scalar_one()
    assert r.completeness == "complete" and r.confirmed_empty is False and r.import_batch_id == up["batch_id"]
    # header-only training file + 'complete' -> confirmed empty month for ALT-07
    empty = (TR_HDR + "\n").encode()
    _upload(client, auth, empty, "empty0.csv", status=400)
    up, _v, c = _run(client, auth, empty, "empty.csv", coverage={**decl, "legal_entity_codes": ["ALT-07"]})
    assert c["status"] == "committed"
    r = db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == tc["tenant_id"], DataFeedCoverage.feed_type == "training",
        DataFeedCoverage.legal_entity_id == tc["entities"]["B"], DataFeedCoverage.period_month == D(2026, 8, 1))
    ).scalar_one()
    assert r.completeness == "complete" and r.confirmed_empty is True


# ------------------------------------------------------------------ cost (item 19)


def test_cost_import_currency_alias_and_negative_amount(client, auth, db, tc):
    sug = suggest_mapping(COST_HDR.split(";"), cst.COST_FIELDS)
    assert {k for k, v in sug.items() if v} == {"external_id", "legal_entity_code", "period_month", "cost_type",
                                                "amount", "currency", "project_code"}
    up, v, c = _run(client, auth, _csv(COST_SAMPLE, COST_HDR), "cost_sample.csv", entity_type="cost")
    assert v["valid_rows"] == 2 and v["error_rows"] == 1 and c["committed_rows"] == 2
    iss = _issues(db, up["batch_id"])
    assert {"negative_amount", "currency_alias"} <= iss[4]
    rows = {(r.cost_type, r.amount, r.currency, r.project_id, r.period_month, r.is_correction)
            for r in _costs(db, tc, "P-0001")}
    assert rows == {("gross_wage", Decimal("10000.00"), "TRY", tc["projects"]["PRJ-A"], D(2026, 10, 1), False),
                    ("employer_ss", Decimal("2000.00"), "TRY", tc["projects"]["PRJ-A"], D(2026, 10, 1), False)}
    assert _costs(db, tc, "P-0004") == []
    # no amount survives in staging after commit (L-13 safe default)
    for raw, mapped in db.execute(select(ImportStagingRow.raw, ImportStagingRow.mapped).where(
            ImportStagingRow.batch_id == up["batch_id"])):
        assert "tutar" not in raw and mapped.get("amount") in (None, cst.MINIMISED)


def test_cost_amount_parsing_fx_and_validation_codes():
    assert cst.parse_amount("1.234,50") == Decimal("1234.50")
    assert cst.parse_amount("1,234.50") == Decimal("1234.50")
    assert cst.parse_amount("10000,00") == Decimal("10000.00") and cst.parse_amount("-500") == Decimal("-500.00")
    assert cst.parse_amount("1 234,5") == Decimal("1234.50")
    for bad in ("abc", "1,2,3", "1.2.3", "10,123", "1e5", ""):
        if bad == "10,123":
            with pytest.raises(ValueError):  # three decimals
                cst.parse_amount(bad)
            continue
        with pytest.raises(ValueError):
            cst.parse_amount(bad)
    assert cst.parse_month("2026-10") == D(2026, 10, 1) and cst.parse_month("10.2026") == D(2026, 10, 1)
    assert cst.parse_month("2026-10-31") == D(2026, 10, 1)


def test_cost_rows_validation_fx_leaver_and_leave_pay(client, auth, db, tc):
    rows = ["P-0003;ANA-01;2026-10;bonus;100,00;TRY;",
            "P-0003;ANA-01;2026-10;yemek;100,00;GBP;",
            "P-0003;ANA-01;2026-10;yol;100,00;CHF;",
            "P-0003;ANA-01;2026-10;yan hak;100,00;XYZ;",
            "P-0003;ANA-01;2026-06;konaklama;100,00;TRY;",
            "P-0003;ANA-01;2099-01;kamp;100,00;TRY;",
            "P-0005;ANA-01;2026-09;kullanılmayan izin ücreti;100,00;TRY;",
            "P-0003;ANA-01;2026-09;izin ücreti;100,00;TRY;",
            "P-0003;ANA-01;2026-10;servis;100,00;TRY;PRJ-X",
            "P-0003;ANA-01;2026-10;brüt ücret;abc;TRY;"]
    up, _v, _c = _run(client, auth, _csv(rows, COST_HDR), "cost_rules.csv", entity_type="cost")
    iss = _issues(db, up["batch_id"])
    assert "unknown_cost_type" in iss[2]
    assert "fx_missing" in iss[3] and "fx_missing" not in iss.get(4, set())  # CHF rate on 20.10, GBP none
    assert "currency_code" in iss[5] and "period_closed" in iss[6] and "future_date" in iss[7]
    assert "no_employment" not in iss.get(8, set())  # P-0005 left 15.09: final payroll resolves in September
    assert "unknown_project" in iss[10] and "amount_format" in iss[11]
    assert "leave_pay_without_termination" in iss[None]  # P-0003 is active (count only)
    msg = db.execute(select(DataQualityIssue.message).where(
        DataQualityIssue.import_batch_id == up["batch_id"],
        DataQualityIssue.rule_code == "leave_pay_without_termination")).scalar_one()
    assert msg.startswith("1 employee-month(s)")
    assert {r.cost_type for r in _costs(db, tc, "P-0005")} == {"unused_leave_pay"}


def test_cost_reimport_correction_rows_and_replace_with_audit(client, auth, db, tc):
    base = "P-0002;ALT-07;2026-09;brüt ücret;30000,00;TRY;"
    _run(client, auth, _csv([base], COST_HDR), "c1.csv", entity_type="cost")
    up, v, c = _run(client, auth, _csv([base], COST_HDR) + b"\n", "c2.csv", entity_type="cost")
    assert "already_imported" in _issues(db, up["batch_id"])[2]
    changed = base.replace("30000,00", "31000,00")
    up, v, c = _run(client, auth, _csv([changed], COST_HDR), "c3.csv", entity_type="cost")
    assert "already_imported_changed" in _issues(db, up["batch_id"])[2]
    assert [r.amount for r in _costs(db, tc, "P-0002")] == [Decimal("30000.00")]
    up, v, c = _run(client, auth, _csv([changed], COST_HDR) + b"\n", "c4.csv", entity_type="cost", mode="replace")
    assert "cost_corrected" in _issues(db, up["batch_id"])[2]
    row, = _costs(db, tc, "P-0002")
    assert row.amount == Decimal("31000.00") and row.record_version == 2
    a = db.execute(select(AuditLog).where(AuditLog.action == COST_CORRECT_ACTION,
                                          AuditLog.entity_id == str(row.id))).scalar_one()
    assert a.details["changed_fields"] == ["amount"]
    assert not any(s in json.dumps(a.details) for s in ("30000", "31000"))  # audit.read is not cost.read
    # correction rows: separate rows, negative allowed, idempotent on re-import
    hdr = COST_HDR + ";düzeltme"
    corr = "P-0002;ALT-07;2026-09;brüt ücret;-1000,00;TRY;;evet"
    up, v, c = _run(client, auth, _csv([corr], hdr), "c5.csv", entity_type="cost")
    assert v["valid_rows"] == 1 and c["committed_rows"] == 1
    up, v, c = _run(client, auth, _csv([corr], hdr) + b"\n", "c6.csv", entity_type="cost")
    assert "already_imported" in _issues(db, up["batch_id"])[2]
    rows = _costs(db, tc, "P-0002")
    assert sorted((r.amount, r.is_correction) for r in rows) == [(Decimal("-1000.00"), True),
                                                                 (Decimal("31000.00"), False)]


def test_cost_coverage_stale_and_permissions(client, auth, db, tc, monkeypatch):
    calls = []
    real = imp.mark_stale_from
    monkeypatch.setattr(imp, "mark_stale_from", lambda db_, tid, d: (calls.append(d), real(db_, tid, d)))
    decl = {"period_start": "2026-08-01", "period_end": "2026-08-31", "legal_entity_codes": ["ANA-01"],
            "completeness": "complete"}
    up, _v, _c = _run(client, auth, _csv(["P-0003;ANA-01;2026-08;brüt ücret;100,00;TRY;"], COST_HDR), "cov.csv",
                    entity_type="cost", coverage=decl)
    assert calls and min(calls) == D(2026, 8, 1)
    r = db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == tc["tenant_id"], DataFeedCoverage.feed_type == "cost",
        DataFeedCoverage.legal_entity_id == tc["entities"]["A"], DataFeedCoverage.period_month == D(2026, 8, 1))
    ).scalar_one()
    assert r.completeness == "complete" and r.import_batch_id == up["batch_id"]
    # import.write without cost.write: no cost upload, no cost batch, no manual cost coverage declaration
    _upload(client, auth, _csv(COST_SAMPLE, COST_HDR), "deny.csv", user=HRMAN, entity_type="cost", status=403)
    assert client.get(f"/api/v1/imports/{up['batch_id']}", headers=auth(HRMAN)).status_code == 400
    assert client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(HRMAN)).status_code == 400
    assert all(b["entity_type"] != "cost" for b in client.get("/api/v1/imports", headers=auth(HRMAN)).json())
    r = client.post("/api/v1/coverage", headers=auth(HRMAN), json={
        "feed_type": "cost", "legal_entity_id": tc["entities"]["A"], "period_start": "2026-07-01",
        "period_end": "2026-07-31", "completeness": "complete"})
    assert r.status_code == 403
    assert COST_WRITE_ROLES == {"SYSTEM_ADMIN"}
    holders = set(db.execute(select(Role.code).join(RolePermission, RolePermission.role_id == Role.id).where(
        Role.tenant_id == tc["tenant_id"], RolePermission.permission_code == "cost.write")).scalars())
    assert holders == {"SYSTEM_ADMIN", "PAYROLL_CLERK", "PAYROLL_LEAD"}


def test_cost_import_preview_masks_amounts_without_cost_read(client, auth, db, tc):
    """Review condition 5 / L-13: a user with import.write + cost.write but without cost.read never sees an amount -
    not in the upload preview, validate summary, errors.csv, batch list, batch detail or data-quality issues."""
    hdr = COST_HDR + ";net ücret"
    rows = ["P-0003;ANA-01;2026-07;brüt ücret;77777,00;TRY;PRJ-A;12345,00",
            "P-0003;ANA-01;2026-07;fazla mesai;-77777,00;TRY;PRJ-A;12345,00",
            "P-0003;ANA-01;2026-07;bonus;77777,00;TRY;PRJ-A;12345,00",
            "P-0003;ANA-01;2026-07;yemek;77777,00x;TRY;PRJ-A;12345,00"]
    content = _csv(rows, hdr)

    def no_amount(payload) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        assert not any(s in text for s in AMOUNT_STRINGS), text[:500]

    up = _upload(client, auth, content, "mask.csv", user=PAYROLL, entity_type="cost")
    assert up["preview_masked"] is True
    no_amount(up)
    assert up["preview"][0]["sicil"] == "P-0003" and up["preview"][0]["tutar"] == cst.MASK
    assert up["preview"][0]["net ücret"] == cst.MASK  # unmapped columns are masked too
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(PAYROLL))
    assert v.status_code == 200
    no_amount(v.json())
    assert v.json()["error_rows"] == 3  # negative, unknown item, bad number
    no_amount(client.get(f"/api/v1/imports/{up['batch_id']}/errors.csv", headers=auth(PAYROLL)).text)
    no_amount(client.get("/api/v1/imports", headers=auth(PAYROLL)).json())
    no_amount(client.get(f"/api/v1/imports/{up['batch_id']}", headers=auth(PAYROLL)).json())
    no_amount(client.get("/api/v1/data-quality/issues?limit=500", headers=auth(PAYROLL)).json())
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(PAYROLL))
    assert c.status_code == 200 and c.json()["committed_rows"] == 1
    no_amount(c.json())
    for raw, mapped in db.execute(select(ImportStagingRow.raw, ImportStagingRow.mapped).where(
            ImportStagingRow.batch_id == up["batch_id"])):
        no_amount(raw)
        no_amount(mapped)
    for msg in db.execute(select(DataQualityIssue.message).where(
            DataQualityIssue.import_batch_id == up["batch_id"])).scalars():
        no_amount(msg)
    # the stored amount is the real one (only the echo is masked)
    assert Decimal("77777.00") in {r.amount for r in _costs(db, tc, "P-0003")}
    # with cost.read the preview shows the amounts
    up2 = _upload(client, auth, content + b"\n", "mask2.csv", user=PAYROLL_READ, entity_type="cost")
    assert up2["preview_masked"] is False and up2["preview"][0]["tutar"] == "77777,00"
    # the HR manager (import.write only) sees neither the batch nor its data-quality queue
    assert all(i["batch_id"] not in (up["batch_id"], up2["batch_id"])
               for i in client.get("/api/v1/data-quality/issues?limit=500", headers=auth(HRMAN)).json())


# ------------------------------------------------------------------ K1 / K2 / K8 via the imports


def _ctx(tk) -> AccessContext:
    return AccessContext(user_id=None, tenant_id=tk["tenant_id"], email="t", locale="en",
                         grants={"kpi.read": None, "cost.read": None, "health.read": None})


def _ev(db, tk, code, ps, pe):
    db.info.pop("kpi_memo", None)
    db.info.pop("coverage_memo", None)  # request-scoped memo of the coverage gate
    db.expire_all()
    return engine.evaluate(db, _ctx(tk), code, ps, pe)


def test_training_k1_k2_hand_calculation_via_import(client, auth, db, tk):
    q3 = {"period_start": "2026-07-01", "period_end": "2026-09-30", "completeness": "complete"}
    rows = [f"KA{i:03d};KA;ISG-TEMEL-16;Temel ISG;İSG;ISG_TEMEL;12;2026-08-10;completed" for i in range(50)] + \
           [f"KA{i:03d};KA;TEK-01;Teknik;mesleki;;12;2026-08-10;completed" for i in range(50, 100)]
    _up, _v, c = _run(client, auth, _csv(rows, TR_HDR), "k1a.csv", user=KADMIN,
                    coverage={**q3, "legal_entity_codes": ["KA"]})
    assert c["committed_rows"] == 100
    _run(client, auth, (TR_HDR + "\n").encode(), "k1b.csv", user=KADMIN, coverage={**q3, "legal_entity_codes": ["KB"]})
    k1 = _ev(db, tk, "KPI-008", D(2026, 7, 1), D(2026, 9, 30))
    assert k1["value"] == pytest.approx(8.0) and k1["annualized_value"] == pytest.approx(31.7391, abs=1e-4)
    assert k1["threshold_status"] == "good" and k1["status_reason"] is None and k1["quality_score"] == 100.0
    assert k1["lineage"]["coverage"]["feeds"] == {"employment": 1.0, "training": 1.0}
    assert k1["breakdown"] == {"hours_by_category": {"hse": 600.0, "technical": 600.0, "compliance": 0.0, "soft": 0.0},
                               "statutory_hse_hours": 600.0, "statutory_hse_hours_per_employee_annualized": 15.87}
    db.execute(delete(DataFeedCoverage).where(DataFeedCoverage.tenant_id == tk["tenant_id"],
                                              DataFeedCoverage.feed_type == "training",
                                              DataFeedCoverage.legal_entity_id == tk["KB"]))
    engine.mark_stale_from(db, tk["tenant_id"], D(2026, 7, 1))
    db.commit()
    k2 = _ev(db, tk, "KPI-008", D(2026, 7, 1), D(2026, 9, 30))
    assert k2["status"] == "ok" and k2["value"] == pytest.approx(8.0)
    assert k2["threshold_status"] is None and k2["status_reason"] == "low_coverage"
    assert k2["quality_score"] == pytest.approx(66.7)
    assert k2["lineage"]["coverage"]["feeds"]["training"] == pytest.approx(0.6667, abs=1e-4)


def test_training_breakdown_small_groups_suppressed(client, auth, db, tk):
    rows = [f"KB{i:03d};KB;SOFT-1;Iletisim;kişisel gelişim;;2;2026-05-12;completed" for i in range(3)]
    _run(client, auth, _csv(rows, TR_HDR), "soft.csv", user=KADMIN)
    r = _ev(db, tk, "KPI-008", D(2026, 4, 1), D(2026, 6, 30))
    assert r["value"] == pytest.approx(6 / 150, abs=1e-4)  # KPI value unchanged by the breakdown
    assert r["breakdown"]["hours_by_category"]["soft"] is None  # 3 persons < min group size 5
    assert r["breakdown"]["hours_by_category"]["hse"] == 0.0 and r["breakdown"]["statutory_hse_hours"] == 0.0


def test_labour_cost_k8_partial_entities_via_import(client, auth, db, tk):
    hdr = "sicil;firma;dönem;kalem;tutar;para birimi"
    octo = {"period_start": "2026-10-01", "period_end": "2026-10-31", "completeness": "complete"}
    rows = [f"KA{i:03d};KA;2026-10;brüt ücret;50000,00;TRY" for i in range(100)]
    _run(client, auth, _csv(rows, hdr), "k8a.csv", user=KADMIN, entity_type="cost",
         coverage={**octo, "legal_entity_codes": ["KA"]})
    r = _ev(db, tk, "KPI-019", D(2026, 10, 1), D(2026, 10, 31))
    assert r["value"] == pytest.approx(33333.33, abs=0.01)
    assert r["status_reason"] == "low_coverage" and r["quality_score"] == pytest.approx(66.7)
    assert r["lineage"]["coverage"]["feeds"]["cost"] == pytest.approx(0.6667, abs=1e-4)
    rows = [f"KB{i:03d};KB;2026-10;brüt ücret;40.000,00;TL" for i in range(50)]
    up, _v, c = _run(client, auth, _csv(rows, hdr), "k8b.csv", user=KADMIN, entity_type="cost",
                    coverage={**octo, "legal_entity_codes": ["KB"]})
    assert c["committed_rows"] == 50
    r = _ev(db, tk, "KPI-019", D(2026, 10, 1), D(2026, 10, 31))
    assert r["value"] == pytest.approx(46666.67, abs=0.01), r
    assert r["status_reason"] is None, (r["note"], r["lineage"])
    assert r["lineage"]["coverage"]["feeds"]["cost"] == 1.0
    assert db.execute(select(ImportBatch.status).where(ImportBatch.id == up["batch_id"])).scalar() == "committed"
