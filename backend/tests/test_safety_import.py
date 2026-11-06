"""HSE case import `safety_event` (wave-2 item 9) and KPI-043 / fatality alert categories (wave-2 item 10) - ADR-021.

Hand-calculated expectations: docs/hr/hse/WP-6_safety_import_fields.md section 4 (+ HR Director review conditions
1-6, Q1-Q6) and docs/hr/hse/WP-2_safety_rate_safeguards.md examples K, L, M-P.

Tenant HSEI (import flow). FIRMA-A (main contractor) owns PRJ-01 / PRJ-02, FIRMA-B is its subcontractor (no
timesheets at all). FIRMA-A: 10 persons x 14 days x 500 h = 70,000 h on PRJ-01 in each of Feb, Mar and Apr 2026.

  4.1  Mar 2026 sample file: 7 rows -> 3 valid (1, 3, 5), 4 errors (2 worker_category_out_of_scope - 'Taşeron', decision
       D-10; 4 lost_days_class_conflict; 6 severity_required + worker_category_out_of_scope; 7 event_date_in_future);
       'Yaralanan Adı Soyadı' dropped before storage.
  4.2  Mar 2026 tenant: LTIFR 2 / 70,000 x 10^6 = 28.5714; TRIR 2 / 70,000 x 200,000 = 5.7143 (the FIRMA-B subcontractor
       case was rejected by the import - D-10 - so there is no out-of-rate case any more); KPI-043 1 / 70,000 x 200,000
       = 2.8571 'low exposure'. No colour.
  4.3  Apr 2026 event ISG-26-0201: case 1 LTI, 2 MTC, 3 FAC -> LTIFR numerator 1, TRIR numerator 2. May file: case 1
       fatality (reclassified_up, alert opens, LTIFR numerator 1). June file: case 2 first aid (reclassified_down,
       TRIR numerator 2 -> 1); case 3 missing from the file is not deleted.
  K'   (D-10 replaces WP-2 K) Feb 2026: visitor fatality, subcontractor LTI, agency MTC -> rejected by the import
       (worker_category_out_of_scope), FAC without category -> unknown_worker_category; nothing stored. A legacy
       third-party fatality row already in the database: not in LTIFR (0 / 70,000, count 1 shown), no alert.
  L    Feb 2026: one own-employee fatality reclassified (reason code) -> 'fatality_reclassified' alert stays.
  D-13 Mar 2026, NMX entity SO (no safety declaration), own-employee fatality: shipped default safety=enforce ->
       KPI-014A/B insufficient_data, the fatality alert is still shown.

Tenant NMX (KPI-043). Timesheet rows 500 h each.
  M  R12 2025-11..2026-10, project PM: 15 p x 140 d = 1,050,000 h, 63 near misses -> 12.0 (neutral, no colour)
  N  Mar 2026, project PN: 10 p x 16 d = 80,000 h, 3 near misses -> 7.5 + low_exposure
  O  Mar 2026, entity SO: 80,000 h, 2 near misses, NO safety declaration -> warn: 5.0 low_coverage; enforce (shipped
     default for safety, D-13): insufficient_data (never a coloured value)
  P  Mar 2026, entity SP: 10 p x 18 d = 90,000 h, declared, 0 near misses -> 0.0 (real zero)
  Q  Mar 2026, entity SQ: 80,000 h, declared with includes_near_miss = false, 1 near miss -> KPI-043 not covered
     (warn low_coverage / enforce insufficient_data); KPI-014A unaffected.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import insert, select

from app.analytics import engine
from app.analytics.engine import KpiError, validate_parameters
from app.core.config import get_settings
from app.models import (
    AuditLog,
    ContractorRelationship,
    DataFeedCoverage,
    DataQualityIssue,
    EmploymentRelationship,
    ImportBatch,
    ImportStagingRow,
    KpiDefinition,
    LegalEntity,
    PeriodClose,
    Person,
    Project,
    SafetyEvent,
    Tenant,
    Timesheet,
    User,
    UserRoleAssignment,
)
from app.security.access import AccessContext
from app.security.auth import hash_password
from app.seed.reference import seed_permissions_and_roles, seed_reference
from app.services import coverage as cov
from app.services import safety_import as sfi

D = date
ADMIN = "hs.admin@hsei.local"  # SYSTEM_ADMIN: import.write + hse.read tenant-wide
HRS = "hs.hrs@hsei.local"  # HR_SPECIALIST: import.write WITHOUT hse.read
CM = "hs.cm@hsei.local"  # CONTRACTOR_MANAGER on FIRMA-B only: import.write + hse.read for FIRMA-B
NM_ADMIN = "nm.admin@nmx.local"
MAR = {"period_start": "2026-03-01", "period_end": "2026-03-31"}
APR = {"period_start": "2026-04-01", "period_end": "2026-04-30"}
FEB = {"period_start": "2026-02-01", "period_end": "2026-02-28"}
R12 = {"period_start": "2025-11-01", "period_end": "2026-10-31"}
HDR = ("Olay No;Vaka No;Firma;Proje;Olay Tarihi;İç Bildirim Tarihi;Olay Sınıfı;Kayıp Gün;Çalışan Türü;Vaka Durumu;"
       "Yaralanan Adı Soyadı")


def _users(db, t, roles, ents, spec):
    pw = hash_password("Demo!2345")
    for email, role, ent in spec:
        u = User(tenant_id=t.id, email=email, full_name=email.split("@")[0], password_hash=pw, locale="en")
        db.add(u)
        db.flush()
        db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role], scope_type="legal_entity" if ent else "tenant",
                                  legal_entity_id=ents[ent].id if ent else None))


def _employ(db, t, ent, count, prefix):
    out = []
    for i in range(count):
        p = Person(tenant_id=t.id, person_code=f"{prefix}{i:04d}", first_name="Hse", last_name=f"T{i}")
        db.add(p)
        db.flush()
        e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ent.id, start_date=D(2020, 1, 1))
        db.add(e)
        db.flush()
        out.append(e.id)
    return out


def _timesheets(db, t, emp_ids, days, first, project_id=None):
    db.execute(insert(Timesheet), [
        {"tenant_id": t.id, "employment_id": e, "project_id": project_id, "work_date": first + timedelta(d),
         "planned_hours": 500, "worked_hours": 500, "overtime_hours": 0, "night_hours": 0, "status": "approved"}
        for e in emp_ids for d in range(days)])


@pytest.fixture(scope="module")
def hs(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="HSEI", name="HSE import test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        ents = {"A": LegalEntity(tenant_id=t.id, code="FIRMA-A", name="Firma A", entity_type="main_contractor"),
                "B": LegalEntity(tenant_id=t.id, code="FIRMA-B", name="Firma B", entity_type="subcontractor")}
        db.add_all(ents.values())
        db.flush()
        prj = {c: Project(tenant_id=t.id, code=c, name=c, owner_entity_id=ents["A"].id) for c in ("PRJ-01", "PRJ-02")}
        db.add_all(prj.values())
        db.flush()
        db.add(ContractorRelationship(tenant_id=t.id, parent_entity_id=ents["A"].id, child_entity_id=ents["B"].id,
                                      valid_from=D(2020, 1, 1), data_sharing_granted=True))
        emps = _employ(db, t, ents["A"], 10, "HA")
        for first in (D(2026, 2, 2), D(2026, 3, 2), D(2026, 4, 1)):
            _timesheets(db, t, emps, 14, first, prj["PRJ-01"].id)
        _users(db, t, roles, ents, [(ADMIN, "SYSTEM_ADMIN", None), (HRS, "HR_SPECIALIST", None),
                                    (CM, "CONTRACTOR_MANAGER", "B")])
        # every feed except safety is complete; the safety feed is declared by the HSE imports themselves
        cov.declare_complete(db, t.id, [e.id for e in ents.values()], ["employment", "timesheet"], D(2023, 1, 1),
                             D(2026, 10, 31), note="test tenant: feeds complete", invalidate=False)
        db.commit()
        return {"tenant_id": t.id, "entities": {k: v.id for k, v in ents.items()},
                "projects": {k: v.id for k, v in prj.items()}}


def _csv(rows, header=HDR):
    return (header + "\n" + "\n".join(";".join(r) for r in rows) + "\n").encode()


def _decl(month: str, codes=("FIRMA-A", "FIRMA-B"), **attrs):
    y, m = map(int, month.split("-"))
    end = (D(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).isoformat()
    return {"period_start": f"{month}-01", "period_end": end, "legal_entity_codes": list(codes),
            "completeness": "complete", "attributes": {"includes_near_miss": True, **attrs}}


def _upload(client, auth, content, name, user=ADMIN, coverage=None, status=201):
    import json

    data = {"entity_type": "safety_event"}
    if coverage is not None:
        data["coverage"] = json.dumps(coverage)
    r = client.post("/api/v1/imports", headers=auth(user), files={"file": (name, content)}, data=data)
    assert r.status_code == status, r.text
    return r.json()


def _run(client, auth, content, name, user=ADMIN, coverage=None, mapping=None):
    up = _upload(client, auth, content, name, user, coverage)
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={"mapping": mapping}, headers=auth(user))
    assert v.status_code == 200, v.text
    v = v.json()
    c = None
    if v["valid_rows"]:
        c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(user))
        assert c.status_code == 200, c.text
        c = c.json()
    return up, v, c


def _issues(db, batch_id):
    db.expire_all()
    return list(db.execute(select(DataQualityIssue).where(DataQualityIssue.import_batch_id == batch_id)
                           .order_by(DataQualityIssue.row_number)).scalars())


def ev(client, auth, code, user=ADMIN, **params):
    r = client.get(f"/api/v1/kpi/evaluate/{code}", params=params, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


def _case(db, hs, key):
    db.expire_all()
    return db.execute(select(SafetyEvent).where(SafetyEvent.tenant_id == hs["tenant_id"],
                                                SafetyEvent.source_record_id == key)).scalar_one_or_none()


SAMPLE_41 = [
    ["ISG-26-0141", "1", "FIRMA-A", "PRJ-01", "2026-03-02", "2026-03-02", "Kayıp Günlü (LTI)", "9", "Kendi personeli",
     "Kapalı", "Ali Veli"],
    ["ISG-26-0142", "1", "FIRMA-B", "PRJ-01", "2026-03-05", "2026-03-12", "Tıbbi müdahale", "0", "Taşeron", "",
     "Ayşe Yılmaz"],
    ["ISG-26-0150", "", "FIRMA-A", "PRJ-02", "2026-03-09", "2026-03-09", "Ramak kala", "", "", "", ""],
    ["ISG-26-0155", "1", "FIRMA-A", "PRJ-01", "2026-03-10", "2026-03-10", "İlk yardım", "4", "Kendi personeli", "",
     "Mehmet Kaya"],
    ["ISG-26-0158", "1", "FIRMA-A", "PRJ-01", "2026-03-16", "2026-03-16", "İş kazası", "3", "Kendi personeli", "",
     "Can Demir"],
    ["ISG-26-0159", "1", "FIRMA-B", "PRJ-02", "2026-03-18", "2026-03-18", "İş kazası", "0", "Taşeron", "",
     "Ece Ak"],
    ["ISG-26-0163", "1", "FIRMA-A", "PRJ-01", "2026-12-01", "2026-12-01", "Ramak kala", "", "", "", ""],
]


# ---------------------------------------------------------------- dictionary / columns (unit level, section 3.4, 3.9)


@pytest.mark.parametrize("label,lost,expected", [
    ("Ölümlü", None, "fatality"), ("ÖLÜM", None, "fatality"), ("Fatality", None, "fatality"), ("FAT", None, "fatality"),
    ("FATAL", None, "fatality"), ("смертельный", None, "fatality"),
    ("Kayıp Günlü (LTI)", 9, "lost_time_injury"), ("LTI", 2, "lost_time_injury"), ("DAFW", 1, "lost_time_injury"),
    ("Lost time injury", 5, "lost_time_injury"), ("с потерей трудоспособности", 4, "lost_time_injury"),
    ("Kısıtlı iş", None, "restricted_work"), ("RWC", None, "restricted_work"), ("light duty", None, "restricted_work"),
    ("Tıbbi müdahale", None, "medical_treatment"), ("MTC", None, "medical_treatment"),
    ("медицинская помощь", None, "medical_treatment"),
    ("İLK YARDIM", None, "first_aid"), ("FA", None, "first_aid"), ("FAC", None, "first_aid"),
    ("первая медицинская помощь", None, "first_aid"),
    ("Ramak kala", None, "near_miss"), ("NM", None, "near_miss"), ("near-miss", None, "near_miss"),
    ("почти-происшествие", None, "near_miss"),
])
def test_classification_dictionary_exact(label, lost, expected):
    assert sfi.classify(label, lost) == (expected, None, [])


def test_short_codes_match_whole_token_only():
    assert sfi.classify("FATAL", None)[0] == "fatality"  # 'FA' must not match inside 'FATAL'
    assert sfi.classify("NMX", None)[1] == "unmapped_classification"
    assert sfi.classify("Non-LTI", None)[1] == "unmapped_classification"  # negated code is not a class


def test_turkish_and_russian_needles_on_word_boundaries():
    """Review condition 2: after folding 'ölüm' = 'olum' must not match 'olumsuz'; RU 'почти' alone is 'almost'."""
    assert sfi.classify("olumsuz", None)[1] == "unmapped_classification"
    assert sfi.classify("Ölümsüz", None)[1] == "unmapped_classification"
    assert sfi.classify("Kazasız", None)[1] == "unmapped_classification"  # 'kaza' is a whole-word generic needle
    assert sfi.classify("почти", None)[1] == "unmapped_classification"


def test_classification_ambiguous_is_error():
    assert sfi.classify("İlk yardım / Tıbbi müdahale", None) == (None, "classification_ambiguous", [])


def test_generic_work_accident_derived_lti_only_with_lost_days():
    assert sfi.classify("İş kazası", 3) == ("lost_time_injury", None, ["classification_derived_from_lost_days"])
    assert sfi.classify("Injury", 0) == (None, "severity_required", [])
    assert sfi.classify("Kaza", None) == (None, "severity_required", [])


@pytest.mark.parametrize("label,code", [
    ("Meslek hastalığı", "illness_not_supported_v1"), ("Servis kazası", "commuting_policy_required"),
    ("Maddi hasar", "out_of_scope_event"), ("Lorem ipsum", "unmapped_classification"),
])
def test_unsupported_labels_wait_in_queue(label, code):
    assert sfi.classify(label, 5) == (None, code, [])


def test_worker_category_mapping():
    assert [sfi.worker_category(v) for v in ("Kendi personeli", "Taşeron", "Geçici iş ilişkisi", "Ziyaretçi",
                                             "Subcontractor", "Diğer", "")] == [
        "own_employee", "subcontractor_worker", "temporary_agency_worker", "third_party", "subcontractor_worker",
        None, None]


def test_prohibited_column_short_tokens_whole_word():
    dropped = sfi.prohibited_columns(["SGK Notification", "Organizasyon", "Not", "Notlar", "Yaş", "TC Kimlik",
                                      "Yaralanan Adı Soyadı", "Tanı", "Olay Özeti", "Vücut Bölgesi", "Olay No",
                                      "Vaka Durumu", "İç Bildirim Tarihi", "Kayıp Gün", "Çalışan Türü"])
    assert dropped == ["Not", "Notlar", "Yaş", "TC Kimlik", "Yaralanan Adı Soyadı", "Tanı", "Olay Özeti",
                       "Vücut Bölgesi"]
    names = [a for f, m in sfi.SAFETY_FIELDS.items() for a in [f, f.replace("_", " "), *m["aliases"]]]
    assert sfi.prohibited_columns(names) == []  # no field alias is itself a prohibited header


# ---------------------------------------------------------------- 4.1 sample file + 4.2 rates


def test_safety_import_sample_file_expected_results(client, auth, db, hs):
    """4.1: mapping suggestion, 4 valid / 3 error rows with the exact rule codes, prohibited column never stored."""
    up = _upload(client, auth, _csv(SAMPLE_41), "isg_mart.csv", coverage=_decl("2026-03"))
    assert up["dropped_columns"] == ["Yaralanan Adı Soyadı"] and "Yaralanan Adı Soyadı" not in up["headers"]
    assert "Ali Veli" not in str(up) and up["coverage"]["attributes"]["includes_near_miss"] is True
    m = up["suggested_mapping"]
    assert (m["event_ref"], m["case_no"], m["legal_entity_code"], m["project_code"], m["event_date"],
            m["reported_date"], m["classification"], m["lost_days"], m["worker_category"], m["case_status"]) == (
        "Olay No", "Vaka No", "Firma", "Proje", "Olay Tarihi", "İç Bildirim Tarihi", "Olay Sınıfı", "Kayıp Gün",
        "Çalışan Türü", "Vaka Durumu")
    assert "Yaralanan Adı Soyadı" not in m.values()
    staged = db.execute(select(ImportStagingRow.raw).where(ImportStagingRow.batch_id == up["batch_id"])).scalars().all()
    assert all("Yaralanan Adı Soyadı" not in r and "Ali Veli" not in str(r) for r in staged)

    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN)).json()
    assert (v["valid_rows"], v["error_rows"]) == (3, 4)
    assert v["dropped_columns"] == ["Yaralanan Adı Soyadı"]
    issues = _issues(db, up["batch_id"])
    errors = {(i.row_number - 1, i.rule_code) for i in issues if i.severity == "error"}
    assert errors == {(2, sfi.OUT_OF_SCOPE_CATEGORY), (4, "lost_days_class_conflict"), (6, "severity_required"),
                      (6, sfi.OUT_OF_SCOPE_CATEGORY), (7, "event_date_in_future")}
    warnings = {(i.row_number and i.row_number - 1, i.rule_code) for i in issues if i.severity == "warning"}
    assert {(5, "classification_derived_from_lost_days"), (None, "prohibited_columns_dropped")} <= warnings
    assert all(i.record_ref is None or i.record_ref.startswith("ISG-26-") for i in issues)
    assert not any("Ali" in i.message or "Kayıp Günlü" in i.message for i in issues)  # no names, no labels echoed
    csv_text = client.get(f"/api/v1/imports/{up['batch_id']}/errors.csv", headers=auth(ADMIN)).text
    assert "ISG-26-0155#1" in csv_text and "Mehmet" not in csv_text

    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(ADMIN)).json()
    assert c["status"] == "committed" and c["committed_rows"] == 3
    got = {(e.source_record_id, e.event_type, e.lost_days, e.worker_category, e.case_no) for e in db.execute(
        select(SafetyEvent).where(SafetyEvent.tenant_id == hs["tenant_id"])).scalars()}
    assert got == {("ISG-26-0141#1", "lost_time_injury", 9, "own_employee", 1),  # 0142 (Taşeron) never stored
                   ("ISG-26-0150#1", "near_miss", None, None, 1),
                   ("ISG-26-0158#1", "lost_time_injury", 3, "own_employee", 1)}
    assert _case(db, hs, "ISG-26-0141#1").case_status == "closed"
    assert _case(db, hs, "ISG-26-0142#1") is None
    # after commit the raw staging rows keep only mapped source columns (KVKK md.4)
    db.expire_all()
    staged = db.execute(select(ImportStagingRow.raw).where(ImportStagingRow.batch_id == up["batch_id"])).scalars().all()
    assert all(set(r) <= set(v for v in m.values() if v) for r in staged)
    # coverage: safety feed, both employers, March, attributes kept
    rows = db.execute(select(DataFeedCoverage).where(DataFeedCoverage.tenant_id == hs["tenant_id"],
                                                     DataFeedCoverage.feed_type == "safety")).scalars().all()
    assert {(r.legal_entity_id, r.period_month, r.completeness) for r in rows} == {
        (hs["entities"]["A"], D(2026, 3, 1), "complete"), (hs["entities"]["B"], D(2026, 3, 1), "complete")}
    assert all(r.attributes == {"includes_near_miss": True} for r in rows)


def test_safety_import_rates_example_42(client, auth, hs):
    """4.2: own employees only (D-10) - the FIRMA-B subcontractor case never reached the database."""
    a = ev(client, auth, "KPI-014A", **MAR)
    assert a["numerator"] == 2 and a["denominator"] == 70_000 and a["value"] == pytest.approx(28.5714, abs=1e-4)
    assert a["threshold_status"] is None and a["status_reason"] == "low_exposure"
    b = ev(client, auth, "KPI-014B", **MAR)
    assert b["numerator"] == 2 and b["value"] == pytest.approx(5.7143, abs=1e-4)
    assert b["threshold_status"] is None and b["status_reason"] == "low_exposure"
    assert "out_of_rate_cases" not in b["breakdown"] and "non_own_cases_not_counted" not in b["breakdown"]
    assert b["formula"]["parameters"]["own_employees_only"] is True
    assert "exclude_out_of_rate_cases" not in b["formula"]["parameters"]
    nm = ev(client, auth, "KPI-043", **MAR)
    assert nm["status"] == "ok" and nm["numerator"] == 1 and nm["value"] == pytest.approx(2.8571, abs=1e-4)
    assert nm["threshold_status"] is None and nm["status_reason"] == "low_exposure"
    assert nm["direction"] == "neutral" and a["alerts"] == [] and nm["alerts"] == []


# ---------------------------------------------------------------- 4.3 multi-case event, aggravation, downgrade


def _rows_43(*cases):
    return [["ISG-26-0201", str(n), "FIRMA-A", "PRJ-01", "2026-04-08", "2026-04-08", label, lost, "Kendi personeli",
             status, ""] for n, label, lost, status in cases]


def test_case_level_counting_multi_victim_event(client, auth, db, hs):
    _, v, c = _run(client, auth, _csv(_rows_43((1, "Kayıp günlü", "6", "Açık"), (2, "Tıbbi müdahale", "0", ""),
                                               (3, "İlk yardım", "0", ""))), "isg_nisan.csv",
                   coverage=_decl("2026-04", codes=("FIRMA-A",)))
    assert v["valid_rows"] == 3 and c["committed_rows"] == 3
    a, b = ev(client, auth, "KPI-014A", **APR), ev(client, auth, "KPI-014B", **APR)
    assert a["numerator"] == 1 and a["value"] == pytest.approx(14.2857, abs=1e-4)
    assert b["numerator"] == 2 and b["value"] == pytest.approx(5.7143, abs=1e-4)
    assert b["breakdown"]["events_by_type"] == {"lost_time_injury": 1, "medical_treatment": 1}


def test_reimport_updates_case_and_records_direction(client, auth, db, hs):
    # May: case 1 becomes a fatality -> same record updated, reclassified_up, alert opens, LTIFR numerator unchanged
    up, v, _ = _run(client, auth, _csv(_rows_43((1, "Ölümlü", "", "Kapalı"))), "isg_mayis.csv")
    assert v["valid_rows"] == 1
    assert "reclassified_up" in {i.rule_code for i in _issues(db, up["batch_id"])}
    ev1 = _case(db, hs, "ISG-26-0201#1")
    assert (ev1.event_type, ev1.record_version, ev1.lost_days) == ("fatality", 2, None)
    a = ev(client, auth, "KPI-014A", **APR)
    assert a["numerator"] == 1
    assert a["alerts"] == [{"type": "fatality", "count": 1, "by_month": {"2026-04": 1},
                            "by_worker_category": {"own_employee": 1}}]
    # June: case 2 downgraded MTC -> FAC; case 3 absent from the extract
    up, v, _ = _run(client, auth, _csv(_rows_43((1, "Ölümlü", "", "Kapalı"), (2, "İlk yardım", "0", ""))),
                    "isg_haziran.csv")
    rules = {i.rule_code for i in _issues(db, up["batch_id"])}
    assert {"reclassified_down", "already_imported"} <= rules
    assert ev(client, auth, "KPI-014B", **APR)["numerator"] == 1  # fatality only (FAC is not recordable)
    audit = [a.details for a in db.execute(select(AuditLog).where(
        AuditLog.tenant_id == hs["tenant_id"], AuditLog.action == "safety.reclassify").order_by(AuditLog.id)).scalars()]
    assert [(d["from"], d["to"], d["direction"]) for d in audit] == [
        ("lost_time_injury", "fatality", "reclassified_up"), ("medical_treatment", "first_aid", "reclassified_down")]
    assert all(set(d) <= {"from", "to", "direction", "legal_entity_id", "project_id", "event_date", "import_batch_id",
                          "record_version", "reason_code"} for d in audit)  # no person / free text


def test_missing_case_in_full_extract_not_deleted(db, hs):
    assert _case(db, hs, "ISG-26-0201#3").event_type == "first_aid"  # V16: never deleted by a later extract
    n = db.execute(select(SafetyEvent).where(SafetyEvent.tenant_id == hs["tenant_id"],
                                             SafetyEvent.event_ref == "ISG-26-0201")).scalars().all()
    assert len(n) == 3


def test_event_date_change_needs_manual_review(client, auth, db, hs):
    rows = _rows_43((3, "İlk yardım", "0", ""))
    rows[0][4] = rows[0][5] = "2026-04-09"
    up, v, _ = _run(client, auth, _csv(rows), "isg_tarih.csv")
    assert v["error_rows"] == 1 and "event_date_changed" in {i.rule_code for i in _issues(db, up["batch_id"])}


def test_closed_period_keeps_frozen_safety_value_with_note(client, auth, db, hs):
    """WP-6 HSE 4.3 last sentence: a closed month keeps its frozen value; the view counts later reclassifications."""
    ctx = AccessContext(user_id=None, tenant_id=hs["tenant_id"], email="t", locale="en",
                        grants={"kpi.read": None, "hse.read": None, "timekeeping.read": None})
    engine.freeze_period(db, ctx, D(2026, 4, 1))
    db.add(PeriodClose(tenant_id=hs["tenant_id"], period_month=D(2026, 4, 1), status="closed"))
    db.commit()
    _run(client, auth, _csv(_rows_43((3, "Tıbbi müdahale", "0", ""))), "isg_kapali.csv")
    assert _case(db, hs, "ISG-26-0201#3").event_type == "medical_treatment"
    b = ev(client, auth, "KPI-014B", **APR)
    assert b["frozen"] is True and b["is_stale"] is True and b["numerator"] == 1
    assert "1 safety case(s) reclassified after closing" in b["note"]


# ---------------------------------------------------------------- WP-2 K and L (fatality alert categories / marker)


def test_non_own_employee_cases_rejected_and_not_in_alert(client, auth, db, hs):
    """Decision D-10 (PO-3, replaces WP-2 K): HSE covers own employees only. Visitor / subcontractor / agency cases are
    rejected by the import with a rule code (row number + event_ref only, no label or person); an injury without a
    category is an error. A legacy third-party fatality already stored counts in neither the rate nor the alert."""
    rows = [["ISG-26-0090", "1", "FIRMA-A", "PRJ-01", "2026-02-10", "2026-02-10", "Fatality", "", "Ziyaretçi",
             "Kapalı", ""],
            ["ISG-26-0092", "1", "FIRMA-A", "PRJ-01", "2026-02-11", "2026-02-11", "LTI", "3", "Taşeron", "", ""],
            ["ISG-26-0093", "1", "FIRMA-A", "PRJ-01", "2026-02-11", "2026-02-11", "MTC", "0", "Geçici iş ilişkisi",
             "", ""],
            ["ISG-26-0094", "1", "FIRMA-A", "PRJ-01", "2026-02-11", "2026-02-11", "FAC", "0", "", "", ""]]
    up = _upload(client, auth, _csv(rows), "isg_subat.csv", coverage=_decl("2026-02", codes=("FIRMA-A",)))
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN)).json()
    assert (v["valid_rows"], v["error_rows"]) == (0, 4)
    issues = _issues(db, up["batch_id"])
    assert {(i.row_number - 1, i.rule_code) for i in issues if i.severity == "error"} == {
        (1, sfi.OUT_OF_SCOPE_CATEGORY), (2, sfi.OUT_OF_SCOPE_CATEGORY), (3, sfi.OUT_OF_SCOPE_CATEGORY),
        (4, "unknown_worker_category")}
    assert not any(w in i.message for i in issues for w in ("Ziyaretçi", "Taşeron", "Geçici", "Fatality"))
    assert all(i.record_ref is None or i.record_ref.startswith("ISG-26-009") for i in issues)
    # a legacy row of another category (stored before D-10) - written before the window is first evaluated
    db.add(SafetyEvent(tenant_id=hs["tenant_id"], legal_entity_id=hs["entities"]["A"],
                       project_id=hs["projects"]["PRJ-01"], event_date=D(2026, 2, 10), event_type="fatality",
                       worker_category="third_party", source_system="legacy", source_record_id="LEGACY-1#1"))
    db.commit()
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(ADMIN)).json()
    assert c["status"] == "committed" and c["committed_rows"] == 0  # the declaration still records the month
    db.expire_all()
    assert db.execute(select(SafetyEvent).where(SafetyEvent.tenant_id == hs["tenant_id"],
                                                SafetyEvent.event_ref.like("ISG-26-009%"))).first() is None
    a = ev(client, auth, "KPI-014A", **FEB)
    assert a["status"] == "ok" and a["numerator"] == 0 and a["value"] == 0.0
    assert a["breakdown"]["non_own_cases_not_counted"] == 1 and "out_of_rate_cases" not in a["breakdown"]
    assert "decision D-10" in a["note"]
    assert a["alerts"] == []  # the alert counts own employees only


def test_fatality_reclassification_keeps_marker(client, auth, db, hs):
    """WP-2 L / R2.5: a fatality is reclassified only with a reason code; the band keeps saying so."""
    base = ["ISG-26-0091", "1", "FIRMA-A", "PRJ-01", "2026-02-12", "2026-02-12", "Ölümlü", "", "Kendi personeli",
            "Kapalı", ""]
    _run(client, auth, _csv([base]), "isg_l1.csv")
    assert ev(client, auth, "KPI-014A", **FEB)["alerts"][0]["count"] == 1  # own employee only (legacy K' row not)
    hdr = HDR + ";Gerekçe Kodu"
    no_reason = [*base[:6], "Medical treatment", "0", *base[8:], ""]
    up, v, _ = _run(client, auth, _csv([no_reason], hdr), "isg_l2.csv")
    assert v["error_rows"] == 1
    assert "fatality_reclass_reason_required" in {i.rule_code for i in _issues(db, up["batch_id"])}
    assert _case(db, hs, "ISG-26-0091#1").event_type == "fatality"
    free_text = [*no_reason[:-1], "not work related, natural death"]
    up, v, _ = _run(client, auth, _csv([free_text], hdr), "isg_l3.csv")
    assert v["error_rows"] == 1  # free text is not a reason code (and is never stored)
    up, v, _ = _run(client, auth, _csv([[*no_reason[:-1], "NWR-NATURAL"]], hdr), "isg_l4.csv")
    assert v["valid_rows"] == 1
    e = _case(db, hs, "ISG-26-0091#1")
    assert (e.event_type, e.reclassified_from_fatality) == ("medical_treatment", True)
    a = ev(client, auth, "KPI-014A", **FEB)
    assert a["alerts"] == [{"type": "fatality_reclassified", "count": 1, "by_month": {"2026-02": 1}}]
    d = db.execute(select(AuditLog.details).where(AuditLog.tenant_id == hs["tenant_id"],
                                                  AuditLog.action == "safety.reclassify")
                   .order_by(AuditLog.id.desc()).limit(1)).scalar()
    assert d["reason_code"] == "NWR-NATURAL" and d["direction"] == "reclassified_down"


def test_fatality_alert_has_no_person_fields(client, auth, hs):
    for alert in ev(client, auth, "KPI-014A", **FEB)["alerts"]:
        assert set(alert) <= {"type", "count", "by_month", "by_worker_category"}
        assert all(len(m) == 7 for m in alert["by_month"])  # month level only


# ---------------------------------------------------------------- validation details


def test_validation_rules_v2_v14(client, auth, db, hs):
    hdr = HDR + ";Kısıtlı İş Günü;SGK Bildirim Tarihi"
    rows = [
        # V13 duplicate + V14 inconsistent project inside one event
        ["ISG-26-0300", "1", "FIRMA-A", "PRJ-01", "2026-03-20", "2026-03-20", "Ramak kala", "", "", "", "", "", ""],
        ["ISG-26-0300", "1", "FIRMA-A", "PRJ-02", "2026-03-20", "2026-03-20", "Ramak kala", "", "", "", "", "", ""],
        # case_no required when an event has several rows
        ["ISG-26-0301", "", "FIRMA-A", "PRJ-01", "2026-03-21", "2026-03-21", "FAC", "", "Kendi personeli", "", "",
         "", ""],
        ["ISG-26-0301", "2", "FIRMA-A", "PRJ-01", "2026-03-21", "2026-03-21", "FAC", "", "Kendi personeli", "", "",
         "", ""],
        # V3 / V4 dates, V6 LTI without lost days (closed), V10 unknown worker category
        ["ISG-26-0302", "1", "FIRMA-A", "PRJ-01", "2026-03-22", "2026-03-21", "LTI", "", "Diğer", "Kapalı", "", "",
         "2026-03-20"],
        # V6 open LTI without lost days -> warning only; V9 RWC without restricted days -> warning
        ["ISG-26-0303", "1", "FIRMA-A", "PRJ-01", "2026-03-23", "2026-03-23", "LTI", "", "Kendi personeli", "Açık",
         "", "", ""],
        ["ISG-26-0304", "1", "FIRMA-A", "PRJ-01", "2026-03-23", "2026-03-23", "RWC", "0", "Kendi personeli", "", "",
         "", ""],
        # V12 unknown project, V11 unknown entity, V5 range
        ["ISG-26-0305", "1", "FIRMA-X", "PRJ-99", "2026-03-24", "2026-03-24", "MTC", "-1", "Kendi personeli", "", "",
         "", ""],
        # V8 fatality with lost days -> ignored (warning)
        ["ISG-26-0306", "1", "FIRMA-A", "PRJ-01", "2026-03-25", "2026-03-25", "FAT", "12", "Kendi personeli", "", "",
         "", ""],
    ]
    up = _upload(client, auth, _csv(rows, hdr), "isg_kurallar.csv")
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN)).json()
    by_row: dict[int, set] = {}
    for i in _issues(db, up["batch_id"]):
        if i.row_number:
            by_row.setdefault(i.row_number - 1, set()).add((i.severity, i.rule_code))
    assert ("error", "event_fields_inconsistent") in by_row[1] and ("error", "duplicate_in_file") in by_row[2]
    assert ("error", "case_no_required") in by_row[3] and ("error", "case_no_required") not in by_row.get(4, set())
    assert {("error", "reported_before_event"), ("error", "sgk_date_before_event"), ("error", "lti_without_lost_days"),
            ("error", "unknown_worker_category")} <= by_row[5]
    assert by_row[6] == {("warning", "lost_days_pending")}
    assert by_row[7] == {("warning", "rwc_without_restricted_days")}
    assert {("error", "unknown_legal_entity"), ("error", "unknown_project"), ("error", "lost_days_range")} <= by_row[8]
    assert by_row[9] == {("warning", "fatality_lost_days_ignored")}
    assert (v["valid_rows"], v["error_rows"]) == (4, 5)  # rows 4, 6, 7, 9 valid


def test_prohibited_column_mapped_field_raises_rename_error(client, auth, hs):
    """Review condition 4: a column dropped as prohibited cannot be mapped silently - rename is requested."""
    hdr = "Olay No;Sorumlu Firma;Olay Tarihi;Olay Sınıfı;Çalışan Türü"
    up = _upload(client, auth, _csv([["ISG-26-0400", "FIRMA-A", "2026-03-02", "Ramak kala", ""]], hdr), "isg_r.csv")
    assert up["dropped_columns"] == ["Sorumlu Firma"] and up["suggested_mapping"]["legal_entity_code"] is None
    mapping = {**up["suggested_mapping"], "legal_entity_code": "Sorumlu Firma"}
    r = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={"mapping": mapping}, headers=auth(ADMIN))
    assert r.status_code == 400 and "rename" in r.text and "Sorumlu Firma" in r.text


# ---------------------------------------------------------------- permissions and coverage


def test_hse_import_requires_hse_read(client, auth, db, hs):
    r = client.post("/api/v1/imports", headers=auth(HRS), files={"file": ("x.csv", _csv([SAMPLE_41[2]]))},
                    data={"entity_type": "safety_event"})
    assert r.status_code == 403
    batch = db.execute(select(ImportBatch.id).where(ImportBatch.tenant_id == hs["tenant_id"],
                                                    ImportBatch.entity_type == "safety_event")).scalars().first()
    assert client.get(f"/api/v1/imports/{batch}", headers=auth(HRS)).status_code == 400  # "Batch not found"
    listing = client.get("/api/v1/imports", headers=auth(HRS))
    assert listing.status_code == 200 and all(b.get("entity_type") != "safety_event" for b in listing.json())
    issues = client.get("/api/v1/data-quality/issues", headers=auth(HRS)).json()
    assert not any(i["batch_id"] == batch for i in issues)


def test_hse_import_scope_is_whole_entity_import_and_hse(client, auth, db, hs):
    """CONTRACTOR_MANAGER on FIRMA-B may import FIRMA-B cases only; a declaration for FIRMA-A is refused."""
    _upload(client, auth, _csv([SAMPLE_41[2]]), "cm1.csv", user=CM, coverage=_decl("2026-03", codes=("FIRMA-A",)),
            status=403)
    row_a = [*SAMPLE_41[2]]
    row_a[0] = "ISG-26-0500"
    row_b = ["ISG-26-0501", "1", "FIRMA-B", "PRJ-01", "2026-03-26", "2026-03-26", "Ramak kala", "", "", "", ""]
    up, v, _ = _run(client, auth, _csv([row_a, row_b]), "cm2.csv", user=CM)
    assert (v["valid_rows"], v["error_rows"]) == (1, 1)
    assert ("scope", 2) in {(i.rule_code, i.row_number) for i in _issues(db, up["batch_id"])}


def test_header_only_file_confirms_empty_month(client, auth, db, hs):
    _upload(client, auth, (HDR + "\n").encode(), "bos.csv", status=400)
    up = _upload(client, auth, (HDR + "\n").encode(), "bos2.csv", coverage=_decl("2026-01", codes=("FIRMA-A",)))
    client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={}, headers=auth(ADMIN))
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(ADMIN)).json()
    assert c["status"] == "committed"
    db.expire_all()
    row = db.execute(select(DataFeedCoverage).where(
        DataFeedCoverage.tenant_id == hs["tenant_id"], DataFeedCoverage.feed_type == "safety",
        DataFeedCoverage.period_month == D(2026, 1, 1))).scalar_one()
    assert row.confirmed_empty is True


# ---------------------------------------------------------------- KPI-043 (WP-2 R3, examples M-P) + includes_near_miss


@pytest.fixture(scope="module")
def nm(database):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="NMX", name="Near-miss test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        ents = {c: LegalEntity(tenant_id=t.id, code=f"NM-{c}", name=c, entity_type="main_contractor")
                for c in ("SM", "SO", "SP", "SQ")}
        db.add_all(ents.values())
        db.flush()
        prj = {c: Project(tenant_id=t.id, code=f"NM-P{c}", name=c, owner_entity_id=ents["SM"].id) for c in "MN"}
        db.add_all(prj.values())
        db.flush()
        sm = _employ(db, t, ents["SM"], 15, "NM")
        _timesheets(db, t, sm, 140, D(2025, 11, 1), prj["M"].id)
        _timesheets(db, t, sm[:10], 16, D(2026, 3, 1), prj["N"].id)
        for code, days in (("SO", 16), ("SP", 18), ("SQ", 16)):
            _timesheets(db, t, _employ(db, t, ents[code], 10, f"N{code}"), days, D(2026, 3, 1))

        def near_misses(ent, n, first, span, project=None, et="near_miss"):
            for i in range(n):
                db.add(SafetyEvent(tenant_id=t.id, legal_entity_id=ents[ent].id, project_id=project,
                                   event_date=first + timedelta(days=(i * 5) % span), event_type=et, lost_days=0))
        near_misses("SM", 63, D(2025, 11, 1), 330, prj["M"].id)
        near_misses("SM", 3, D(2026, 3, 1), 16, prj["N"].id)
        near_misses("SO", 2, D(2026, 3, 1), 16)
        near_misses("SQ", 1, D(2026, 3, 1), 16)
        _users(db, t, roles, ents, [(NM_ADMIN, "SYSTEM_ADMIN", None)])
        feeds = ["employment", "timesheet"]
        ids = [e.id for e in ents.values()]
        cov.declare_complete(db, t.id, ids, feeds, D(2023, 1, 1), D(2026, 10, 31), note="test", invalidate=False)
        cov.declare_complete(db, t.id, [ents["SM"].id, ents["SP"].id], ["safety"], D(2023, 1, 1), D(2026, 10, 31),
                             note="test", invalidate=False)
        cov.declare(db, tenant_id=t.id, user_id=None, basis="manual_declaration", invalidate=False, decl={
            "feed_type": "safety", "period_start": "2026-03-01", "period_end": "2026-03-31",
            "legal_entity_ids": [ents["SQ"].id], "project_ids": [None], "completeness": "complete",
            "coverage_ratio": None, "confirmed_empty": False, "planned_on_leave_days": None, "note": "no NM feed",
            "attributes": {"includes_subcontractor_cases": True, "includes_near_miss": False}})
        db.commit()
        return {"entities": {k: v.id for k, v in ents.items()}, "projects": {k: v.id for k, v in prj.items()}}


@pytest.fixture()
def mode(monkeypatch):
    def set_mode(value: str):
        monkeypatch.setattr(get_settings(), "coverage_modes", value)
    return set_mode


def test_kpi043_neutral_no_thresholds(db):
    k = db.execute(select(KpiDefinition).where(KpiDefinition.code == "KPI-043")).scalar_one()
    assert (k.status, k.direction) == ("active", "neutral")
    from app.analytics.catalog import APPROVED_THRESHOLDS, KPI_CATALOG, REQUIRED_FEEDS

    assert "KPI-043" not in APPROVED_THRESHOLDS  # permanently neutral, no thresholds (HR Director WP-2 Q3)
    item = next(i for i in KPI_CATALOG if i["code"] == "KPI-043")
    p = item["formula"]["parameters"]
    assert p["multiplier"] == 200_000 and p["threshold_min_exposure_hours"] == 200_000
    assert p["required_feeds"] == ["timesheet", "safety"] and p["min_feed_coverage"] == 0.9
    assert p["required_feed_attributes"] == {"safety": {"includes_near_miss": True}}
    assert REQUIRED_FEEDS["KPI-043"] == ("timesheet", "safety")


def test_kpi043_near_miss_rate_exact(client, auth, nm):
    m = ev(client, auth, "KPI-043", NM_ADMIN, project_id=nm["projects"]["M"], **R12)  # M: 63 / 1,050,000 x 200,000
    assert m["numerator"] == 63 and m["denominator"] == 1_050_000 and m["value"] == pytest.approx(12.0)
    assert m["threshold_status"] is None and m["status_reason"] is None
    n = ev(client, auth, "KPI-043", NM_ADMIN, project_id=nm["projects"]["N"], **MAR)  # N: 3 / 80,000 x 200,000
    assert n["value"] == pytest.approx(7.5) and n["status_reason"] == "low_exposure"


def test_kpi043_missing_feed_is_insufficient_not_zero(client, auth, nm, mode):
    mode("default=warn")  # the shipped default enforces safety (D-13); warn behaviour checked explicitly
    o = ev(client, auth, "KPI-043", NM_ADMIN, entity_ids=str(nm["entities"]["SO"]), **MAR)  # O: undeclared
    assert o["value"] == pytest.approx(5.0) and o["threshold_status"] is None and o["status_reason"] == "low_coverage"
    p = ev(client, auth, "KPI-043", NM_ADMIN, entity_ids=str(nm["entities"]["SP"]), **MAR)  # P: real zero
    assert p["status"] == "ok" and p["value"] == 0.0 and p["denominator"] == 90_000
    assert p["status_reason"] == "low_exposure"  # 90,000 h < 200,000 h: label only (no colour anyway)
    mode("default=warn,safety=enforce")
    o = ev(client, auth, "KPI-043", NM_ADMIN, entity_ids=str(nm["entities"]["SO"]), **MAR)
    assert o["status"] == "insufficient_data" and o["value"] is None


def test_includes_near_miss_false_uncovers_kpi043_only(client, auth, nm, mode):
    mode("default=warn")
    q = ev(client, auth, "KPI-043", NM_ADMIN, entity_ids=str(nm["entities"]["SQ"]), **MAR)
    assert q["status_reason"] == "low_coverage" and q["lineage"]["coverage"]["feeds"]["safety"] == 0.0
    a = ev(client, auth, "KPI-014A", NM_ADMIN, entity_ids=str(nm["entities"]["SQ"]), **MAR)
    assert a["status"] == "ok" and a["status_reason"] == "low_exposure"
    assert a["lineage"]["coverage"]["feeds"]["safety"] == 1.0
    mode("default=warn,safety=enforce")
    q = ev(client, auth, "KPI-043", NM_ADMIN, entity_ids=str(nm["entities"]["SQ"]), **MAR)
    assert q["status"] == "insufficient_data"


def test_kpi043_occupation_filter_insufficient(client, auth, nm):
    from app.core.db import SessionLocal
    from app.models import Occupation

    with SessionLocal() as s:
        occ = s.execute(select(Occupation.id).limit(1)).scalar()
    if occ is None:
        pytest.skip("no occupation reference data")
    r = ev(client, auth, "KPI-043", NM_ADMIN, occupation_group="construction", **MAR)
    assert r["status"] == "insufficient_data"


def test_formula_parameter_whitelist_for_new_keys():
    validate_parameters({"multiplier": 200_000, "threshold_min_exposure_hours": 200_000,
                         "required_feed_attributes": {"safety": {"includes_near_miss": True}}}, "near_miss_rate")
    validate_parameters({"exclude_out_of_rate_cases": True}, "trir")  # older formula versions
    validate_parameters({"own_employees_only": True}, "ltifr")  # D-10
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"own_employees_only": True}, "near_miss_rate")
    with pytest.raises(KpiError):
        validate_parameters({"own_employees_only": "yes"}, "trir")
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"exclude_out_of_rate_cases": True}, "near_miss_rate")
    with pytest.raises(KpiError):
        validate_parameters({"event_types": ["near_miss"]}, "near_miss_rate")
    for bad in ({"leave": {"includes_near_miss": True}}, {"safety": {"includes_near_miss": "yes"}},
                {"safety": {"hazard_class": True}}, {}):
        with pytest.raises(KpiError):
            validate_parameters({"required_feed_attributes": bad}, "near_miss_rate")
    with pytest.raises(KpiError):
        validate_parameters({"exclude_out_of_rate_cases": 1}, "ltifr")


def test_ascii_olumlu_is_never_a_fatality():
    """'olumlu' (= positive) typed without Turkish letters folds onto 'ölümlü'; it must not raise a fatality."""
    from app.services.safety_import import classify

    assert classify("OLUMLU", None) == (None, "fatality_label_ascii_ambiguous", [])
    assert classify("Olumlu", None)[0] is None
    assert classify("ÖLÜMLÜ", None)[0] == "fatality"
    assert classify("FAT", None)[0] == "fatality"


def test_d13_undeclared_safety_feed_insufficient_but_fatality_alert_shown(client, auth, db, nm):
    """Decision D-13 (PO-6): the shipped default enforces the safety feed. Entity SO has no safety declaration ->
    KPI-014A and KPI-014B are insufficient_data (never a value), while an own-employee fatality alert still appears."""
    assert get_settings().coverage_modes == "default=warn,safety=enforce"
    from app.core.db import SessionLocal

    with SessionLocal() as s:
        tid = s.get(LegalEntity, nm["entities"]["SO"]).tenant_id
        e = SafetyEvent(tenant_id=tid, legal_entity_id=nm["entities"]["SO"], event_date=D(2026, 3, 4),
                        event_type="fatality", worker_category="own_employee", source_system="test-d13",
                        source_record_id="D13#1")
        s.add(e)
        s.commit()
        eid = e.id
    try:
        for code in ("KPI-014A", "KPI-014B"):
            r = ev(client, auth, code, NM_ADMIN, entity_ids=str(nm["entities"]["SO"]), **MAR)
            assert r["status"] == "insufficient_data" and r["value"] is None and r["threshold_status"] is None, code
            assert r["alerts"] == [{"type": "fatality", "count": 1, "by_month": {"2026-03": 1},
                                    "by_worker_category": {"own_employee": 1}}], code
    finally:
        with SessionLocal() as s:
            s.delete(s.get(SafetyEvent, eid))
            s.commit()
