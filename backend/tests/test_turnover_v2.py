"""SGK exit code import + turnover v2 (wave-2 items 13-14, ADR-023).

Hand-calculated expectations: docs/hr/data/WP-5_turnover_definitions.md section 4 + HR Director review (conditions 1-7,
S-1..S-6). Tenant TOV, every feed declared complete (the coverage gate is tested elsewhere), mapping table verified.

4.1 cohort 2026-01-01..2026-03-31, 10 hires per entity, observed 2026-11 (>= 180 days after the cohort):
  H1 01-05 -> 02-20 voluntary 03 permanent (46 d); H2 01-12 -> 03-31 project_end 18 project, planned 03-31 (78 d);
  H3 02-02 -> 04-15 contract_end 05 fixed_term, planned 04-15 (72 d); H4 01-20 -> 06-25 voluntary 03 (156 d);
  H5 02-16 -> 09-30 project_end 18 project (226 d); H6-H10 no exit.
  W1 (v1): <30 0 %, <90 H1 H2 H3 = 30 %, <180 + H4 = 40 %.
  W2 (v2 verified, entity W2): KPI-021 0 %, KPI-018 H1 = 10 % good, KPI-022 H1 H4 = 20 % good; excluded 2/10 = 20 %.
  W3 (entity W3): H3 source contract_end but SGK 04, permanent -> employer_initiated -> KPI-018 20 % = critical
      (review condition 6), KPI-022 30 % warning, excluded 1/10 = 10 %.
  W4 (entity W4): H2 planned end 06-30, exit 03-31 < 06-23 -> not planned -> 20 % critical, KPI-022 30 %.
  W5 (entity W5): H2 without code, contract type blank (-> unknown), no planned end -> NULL -> counted: 20 %,
      classification quality 100 x (1 - 1/3) = 66.7. W5-alt (category mode): 10 %, KPI-022 20 %.
4.2 KPI-020, 12-month window 2025-11-01..2026-10-31, 400 persons employed all year, 100 leave on 2026-10-31 (still
  employed that day -> headcount 400 at every point, average 400):
  V1 vol 30 / emp 20 / contract 15 / project 25 / unknown 10 -> 7.5 %, no colour (unknown_category_share),
      range [7.5, 10.0], quality 90; KPI-001 25 %.
  V2 unknown 5 (vol 35) -> 8.75 % good. V3 unknown 25 (vol 15) -> insufficient_data, quality 75.
  V4 no separations -> 0 % good. V6 vol 38 (8 of them SGK 23/25), unknown 2 -> 9.5 % good, quality 98,
      voluntary_just_cause 8. Low information: unknown 6 + other 10 = 16 % > 15 % -> note.
"""
import json
from datetime import date

import pytest
from sqlalchemy import select, update

from app.analytics import calculators as calc
from app.analytics.calculators import Scope
from app.analytics.engine import KpiError, mark_stale_from, validate_parameters
from app.models import (
    DataQualityIssue,
    EmploymentRelationship,
    ImportStagingRow,
    KpiDefinition,
    KpiFormulaVersion,
    KpiResult,
    LegalEntity,
    Person,
    PersonIdentifier,
    Tenant,
    TerminationCodeMap,
    TerminationCounter,
    User,
    UserRoleAssignment,
)
from app.security.auth import hash_password
from app.seed.reference import seed_permissions_and_roles, seed_reference, sync_kpi_catalog
from app.services import exit_codes as xc
from tests.factories import declare_all_feeds

D = date
ADMIN = "tov.admin@tov.local"
HDR = "sicil;ad;soyad;firma;sözleşme tipi;işe giriş;çıkış tarihi;çıkış nedeni;sgk çıkış kodu;planlanan bitiş"
COHORT = {"period_start": "2026-01-01", "period_end": "2026-03-31"}
YEAR = {"period_start": "2025-11-01", "period_end": "2026-10-31"}
BASE = {  # WP-5 4.1 (start, end, category, sgk, contract, planned end)
    "H1": ("2026-01-05", "2026-02-20", "voluntary", "03", "permanent", ""),
    "H2": ("2026-01-12", "2026-03-31", "project_end", "18", "project", "2026-03-31"),
    "H3": ("2026-02-02", "2026-04-15", "contract_end", "05", "fixed_term", "2026-04-15"),
    "H4": ("2026-01-20", "2026-06-25", "voluntary", "03", "permanent", ""),
    "H5": ("2026-02-16", "2026-09-30", "project_end", "18", "project", "2026-09-30"),
    **{f"H{i}": (f"2026-0{1 + i % 3}-1{i % 10}", "", "", "", "permanent", "") for i in range(6, 11)},
}
VARIANTS = {
    "W2": {},
    "W3": {"H3": ("2026-02-02", "2026-04-15", "contract_end", "04", "permanent", "")},
    "W4": {"H2": ("2026-01-12", "2026-03-31", "project_end", "18", "project", "2026-06-30")},
    "W5": {"H2": ("2026-01-12", "2026-03-31", "project_end", "", "", "")},
}


def _csv(rows, header=HDR):
    return (header + "\n" + "\n".join(rows) + "\n").encode()


def _w_rows(ent):
    out = []
    for h, (sd, ed, cat, code, ct, ped) in {**BASE, **VARIANTS[ent]}.items():
        out.append(f"{ent}-{h};Tov;{h};{ent};{ct};{sd};{ed};{cat};{code};{ped}")
    return out


def _run(client, auth, content, name, user=ADMIN, status=200):
    up = client.post("/api/v1/imports", headers=auth(user), files={"file": (name, content)},
                     data={"entity_type": "employment"})
    assert up.status_code == 201, up.text
    up = up.json()
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={"mapping": None}, headers=auth(user))
    assert v.status_code == 200, v.text
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(user))
    assert c.status_code == status, c.text
    return up, v.json(), c.json()


def _issues(db, batch_id):
    db.expire_all()
    return list(db.execute(select(DataQualityIssue).where(DataQualityIssue.import_batch_id == batch_id)).scalars())


def _emp(db, tid, ext):
    db.expire_all()
    return db.execute(select(EmploymentRelationship).join(
        PersonIdentifier, PersonIdentifier.person_id == EmploymentRelationship.person_id).where(
        PersonIdentifier.tenant_id == tid, PersonIdentifier.external_id == ext)).scalar_one()


def ev(client, auth, code, ent_id, user=ADMIN, **params):
    r = client.get(f"/api/v1/kpi/evaluate/{code}", params={"entity_ids": str(ent_id), **params}, headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def tov(database, client, auth):
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        seed_reference(db)
        t = Tenant(code="TOV", name="Turnover v2 test tenant")
        db.add(t)
        db.flush()
        roles = seed_permissions_and_roles(db, t.id)
        codes = ("W2", "W3", "W4", "W5", "HX", "VB", "AL", "AL2")
        ents = {c: LegalEntity(tenant_id=t.id, code=c, name=c, entity_type="main_contractor") for c in codes}
        db.add_all(ents.values())
        db.flush()
        u = User(tenant_id=t.id, email=ADMIN, full_name="tov admin", password_hash=hash_password("Demo!2345"),
                 locale="en")
        db.add(u)
        db.flush()
        db.add(UserRoleAssignment(user_id=u.id, role_id=roles["SYSTEM_ADMIN"], scope_type="tenant"))
        declare_all_feeds(db, t.id)
        xc.ensure_map(db, t.id)
        db.commit()
        # the payroll / SGK practitioner verified the list line by line (L-12)
        xc.verify(db, t.id, [r[0] for r in xc.SEED_ROWS], "bordro uygulayıcısı (test)")
        # KPI-020 population: 400 persons all year, 100 of them leave on the last day (categories set per scenario)
        vb = []
        for i in range(400):
            p = Person(tenant_id=t.id, person_code=f"VB-{i:03d}", first_name="Vb", last_name=str(i))
            db.add(p)
            db.flush()
            e = EmploymentRelationship(tenant_id=t.id, person_id=p.id, legal_entity_id=ents["VB"].id,
                                       start_date=D(2020, 1, 1), contract_type="permanent")
            db.add(e)
            db.flush()
            vb.append(e.id)
        db.commit()
        out = {"tenant_id": t.id, "entities": {k: v.id for k, v in ents.items()}, "vb": vb}
    content = _csv([r for ent in VARIANTS for r in _w_rows(ent)])
    up, v, c = _run(client, auth, content, "wp5_cohorts.csv")
    out["w_batch"] = up["batch_id"]
    assert v["error_rows"] == 0 and c["committed_rows"] == 40
    return out


def _calc(db, tov, ent, **params):
    db.info.clear()
    return calc.early_turnover_90(db, Scope(tov["tenant_id"], frozenset({tov["entities"][ent]})), D(2026, 1, 1),
                                  D(2026, 3, 31), params)


# ------------------------------------------------------------------ KPI-018/021/022 v2 (WP-5 4.1)


def test_early_turnover_excludes_only_verified_planned_ends(client, auth, db, tov):
    # W1: v1 parameters (formula version 1 behaviour) on the same data
    assert _calc(db, tov, "W2", days=30).value == 0.0
    assert _calc(db, tov, "W2", days=90).value == 30.0
    assert _calc(db, tov, "W2", days=180).value == 40.0
    # W2: current catalog version (verified)
    e = tov["entities"]["W2"]
    r21, r18, r22 = (ev(client, auth, c, e, **COHORT) for c in ("KPI-021", "KPI-018", "KPI-022"))
    assert (r21["value"], r21["threshold_status"]) == (0.0, "good")  # real zero: cohort fully observed
    assert (r18["value"], r18["threshold_status"]) == (10.0, "good")
    assert (r22["value"], r22["threshold_status"]) == (20.0, "good")
    assert r18["breakdown"]["planned_end_excluded_share"] == 20.0
    assert r22["breakdown"]["planned_end_excluded_share"] == 20.0
    assert r18["lineage"]["planned_end_excluded"] == 2 and r18["lineage"]["early_leavers_before_exclusion"] == 3
    assert r18["quality_score"] == 100.0 and r18["formula"]["parameters"]["planned_end_exclusion"] == "verified"
    assert r18["denominator"] == 10  # planned ends stay in the cohort
    h2 = _emp(db, tov["tenant_id"], "W2-H2")
    assert (h2.planned_end_verified, h2.termination_reason_code, h2.termination_reason_source) == (True, "18", "sgk")
    assert _emp(db, tov["tenant_id"], "W2-H1").planned_end_verified is False


def test_early_turnover_sgk_code_overrides_conflicting_category(client, auth, db, tov):
    e = tov["entities"]["W3"]
    r18, r22 = ev(client, auth, "KPI-018", e, **COHORT), ev(client, auth, "KPI-022", e, **COHORT)
    assert (r18["value"], r18["threshold_status"]) == (20.0, "critical")  # value = critical threshold (condition 6)
    assert (r22["value"], r22["threshold_status"]) == (30.0, "warning")
    assert r18["breakdown"]["planned_end_excluded_share"] == 10.0
    h3 = _emp(db, tov["tenant_id"], "W3-H3")
    assert (h3.termination_category, h3.termination_reason_code, h3.planned_end_verified) == (
        "employer_initiated", "04", False)
    rules = {(i.record_ref, i.rule_code) for i in _issues(db, tov["w_batch"])}
    assert ("W3-H3", "category_code_conflict") in rules


def test_planned_end_before_planned_date_counts_as_early_leaver(client, auth, db, tov):
    e = tov["entities"]["W4"]
    r18, r22 = ev(client, auth, "KPI-018", e, **COHORT), ev(client, auth, "KPI-022", e, **COHORT)
    assert (r18["value"], r18["threshold_status"]) == (20.0, "critical")
    assert r22["value"] == 30.0 and r18["breakdown"]["planned_end_excluded_share"] == 10.0
    h2 = _emp(db, tov["tenant_id"], "W4-H2")
    assert (h2.planned_end_date, h2.planned_end_verified) == (D(2026, 6, 30), False)
    assert ("W4-H2", "planned_end_mismatch") in {(i.record_ref, i.rule_code) for i in _issues(db, tov["w_batch"])}


def test_unverifiable_planned_end_counted_and_lowers_quality(client, auth, db, tov):
    e = tov["entities"]["W5"]
    r18, r22 = ev(client, auth, "KPI-018", e, **COHORT), ev(client, auth, "KPI-022", e, **COHORT)
    assert (r18["value"], r18["threshold_status"]) == (20.0, "critical")
    assert r18["quality_score"] == 66.7 and r18["lineage"]["planned_end_unverified_counted"] == 1
    assert r22["value"] == 30.0
    h2 = _emp(db, tov["tenant_id"], "W5-H2")
    assert (h2.termination_category, h2.planned_end_verified, h2.termination_reason_source) == (
        "project_end", None, "hris")


def test_blank_contract_type_stored_as_unknown_not_permanent(db, tov):
    h2 = _emp(db, tov["tenant_id"], "W5-H2")
    assert h2.contract_type == "unknown"
    rules = {(i.record_ref, i.rule_code, i.severity) for i in _issues(db, tov["w_batch"])}
    assert ("W5-H2", "contract_type_missing", "warning") in rules
    assert ("W5-H2", "planned_end_unverifiable", "warning") in rules
    assert not any(r[0] == "W2-H1" and r[1] == "contract_type_missing" for r in rules)


def test_planned_end_exclusion_category_mode(db, tov):
    r90 = _calc(db, tov, "W5", days=90, planned_end_exclusion="category")
    r180 = _calc(db, tov, "W5", days=180, planned_end_exclusion="category")
    assert (r90.value, r90.breakdown["planned_end_excluded_share"]) == (10.0, 20.0)
    assert r180.value == 20.0


def test_early_turnover_excluded_share_in_breakdown_and_suppressed_with_value(db, tov):
    tid = tov["tenant_id"]
    # a 1-hire cohort (below HRSWI_MIN_GROUP_SIZE): no value, no share, no counts
    from app.analytics.engine import enforce_privacy

    db.info.clear()
    res = enforce_privacy(calc.early_turnover_90(db, Scope(tid, frozenset({tov["entities"]["W2"]})), D(2026, 1, 5),
                                                 D(2026, 1, 5), {"days": 90, "planned_end_exclusion": "verified"}))
    assert res.status == "suppressed" and res.value is None and res.breakdown == {}
    assert "planned_end_excluded" not in res.lineage and res.lineage.get("planned_end_exclusion") == "verified"


def test_repeated_planned_ends_and_coding_shift_alarm_po4_switch(db, tov):
    """S-2 / S-5 / PO-4. 30-day windows: current 2026-03-01..03-30, previous ones shifted by 30 days.
    First window (2025-12-31..2026-01-29): 10 hires 01-05, 4 voluntary early leavers -> 40 %, share 0.
    Middle window: 10 hires 02-02, none early. Current: 10 hires 03-02, 1 voluntary early + 2 verified planned ends
    (P, Q) -> 10 %, share 20 % -> early turnover fell, share +20 >= 10 points -> alarm.
    P had a verified planned end at AL on 2025-12-31 (same legal entity), Q at AL2 on 2026-01-15 (group company):
    same_employer_basis legal_entity -> 1 repeated person, group -> 2."""
    tid, al, al2 = tov["tenant_id"], tov["entities"]["AL"], tov["entities"]["AL2"]

    def spell(code, ent, sd, ed=None, cat=None, pev=None, ct="permanent", person=None):
        if person is None:
            person = Person(tenant_id=tid, person_code=code, first_name="Al", last_name=code)
            db.add(person)
            db.flush()
        db.add(EmploymentRelationship(tenant_id=tid, person_id=person.id, legal_entity_id=ent, start_date=sd,
                                      end_date=ed, termination_category=cat, planned_end_verified=pev,
                                      contract_type=ct))
        db.flush()
        return person

    for i in range(10):
        spell(f"AL-F{i}", al, D(2026, 1, 5), *((D(2026, 2, 15), "voluntary", False) if i < 4 else ()))
        spell(f"AL-M{i}", al, D(2026, 2, 2))
    p = spell("AL-P", al, D(2025, 6, 1), D(2025, 12, 31), "contract_end", True, "fixed_term")
    q = spell("AL-Q", al2, D(2025, 10, 1), D(2026, 1, 15), "contract_end", True, "fixed_term")
    spell("", al, D(2026, 3, 2), D(2026, 4, 30), "contract_end", True, "fixed_term", person=p)
    spell("", al, D(2026, 3, 2), D(2026, 4, 30), "contract_end", True, "fixed_term", person=q)
    spell("AL-C0", al, D(2026, 3, 2), D(2026, 3, 20), "voluntary", False)
    for i in range(1, 8):
        spell(f"AL-C{i}", al, D(2026, 3, 2))
    db.commit()
    scope = Scope(tid, frozenset({al, al2}))
    params = {"days": 90, "planned_end_exclusion": "verified", "planned_end_alarm_points": 10}
    db.info.clear()
    r = calc.early_turnover_90(db, scope, D(2026, 3, 1), D(2026, 3, 30), {**params, "same_employer_basis": "legal_entity"})
    assert (r.value, r.breakdown["planned_end_excluded_share"]) == (10.0, 20.0)
    assert r.lineage["planned_end_alarm"] is True and "check the exit coding" in r.note
    assert r.lineage["repeated_short_fixed_term_persons"] == 1 and "1 person(s) with repeated" in r.note
    db.info.clear()
    g = calc.early_turnover_90(db, scope, D(2026, 3, 1), D(2026, 3, 30), {**params, "same_employer_basis": "group"})
    assert g.lineage["repeated_short_fixed_term_persons"] == 2 and g.value == 10.0
    db.info.clear()
    quiet = calc.early_turnover_90(db, scope, D(2026, 3, 1), D(2026, 3, 30), {**params, "planned_end_alarm_points": 25})
    assert "planned_end_alarm" not in quiet.lineage


def test_turnover_formula_v1_results_preserved_after_v2(client, auth, db, tov):
    """An existing database whose current KPI-018 formula is v1 (no planned-end exclusion): its persisted result keeps
    its version and value; catalog sync adds v2 as a NEW version (ADR-018 rule 6 / INTEGRATION_CONTRACTS 9)."""
    from app.analytics.engine import run_calculation
    from app.security.access import AccessContext

    d = db.execute(select(KpiDefinition).where(KpiDefinition.code == "KPI-018")).scalar_one()
    cur = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == d.id,
                                                     KpiFormulaVersion.is_current.is_(True))).scalar_one()
    cur.is_current = False
    old = KpiFormulaVersion(kpi_id=d.id, version=cur.version + 1, calculator_key="early_turnover_90",
                            formula_text=cur.formula_text, numerator_def=cur.numerator_def,
                            denominator_def=cur.denominator_def, is_current=True, change_note="v1 (simulated)",
                            parameters={"days": 90, "required_feeds": ["employment"], "min_feed_coverage": 0.8})
    db.add(old)
    db.commit()
    db.info.clear()
    w2 = tov["entities"]["W2"]
    system = AccessContext(user_id=None, tenant_id=tov["tenant_id"], email="system:test", locale="en",
                           grants={"kpi.read": None, "cost.read": None})
    run = run_calculation(db, system, ["KPI-018"], D(2026, 1, 1), D(2026, 3, 31), entity_ids={w2})
    v1_row = db.execute(select(KpiResult).where(KpiResult.run_id == run.id)).scalar_one()
    assert (float(v1_row.value), v1_row.formula_version_id) == (30.0, old.id)
    assert "KPI-018" in [c["code"] for c in sync_kpi_catalog(db) if c["new_formula_version"]]
    sync_kpi_catalog(db, apply=True)
    db.info.clear()
    now = ev(client, auth, "KPI-018", w2, **COHORT)
    assert now["value"] == 10.0 and now["formula"]["version"] == old.version + 1
    assert ev(client, auth, "KPI-018", w2, version=old.version, **COHORT)["value"] == 30.0
    db.expire_all()
    assert (float(db.get(KpiResult, v1_row.id).value), db.get(KpiResult, v1_row.id).formula_version_id) == (30.0, old.id)
    assert sync_kpi_catalog(db) == []
    # KPI-001 is not touched by any rule
    assert ev(client, auth, "KPI-001", w2, **COHORT)["formula"]["parameters"].get("planned_end_exclusion") is None


# ------------------------------------------------------------------ KPI-020 v2 (WP-5 4.2)


def _set_separations(db, tov, mix: dict[str, int], codes: dict[str, int] | None = None):
    """First 100 VB employments leave on 2026-10-31 with categories in the given order; codes = SGK code counts
    assigned to the first voluntary leavers."""
    ids = tov["vb"]
    db.execute(update(EmploymentRelationship).where(EmploymentRelationship.id.in_(ids)).values(
        end_date=None, termination_category=None, termination_reason_code=None))
    i = 0
    code_list = [c for c, n in (codes or {}).items() for _ in range(n)]
    for cat, n in mix.items():
        for _ in range(n):
            code = code_list.pop(0) if cat == "voluntary" and code_list else None
            db.execute(update(EmploymentRelationship).where(EmploymentRelationship.id == ids[i]).values(
                end_date=D(2026, 10, 31), termination_category=cat, termination_reason_code=code))
            i += 1
    mark_stale_from(db, tov["tenant_id"], D(2020, 1, 1))
    db.commit()


def test_voluntary_turnover_unknown_share_bands(client, auth, db, tov):
    vb = tov["entities"]["VB"]
    _set_separations(db, tov, {"voluntary": 30, "employer_initiated": 20, "contract_end": 15, "project_end": 25,
                               "unknown": 10})
    v1, k1 = ev(client, auth, "KPI-020", vb, **YEAR), ev(client, auth, "KPI-001", vb, **YEAR)
    assert (v1["value"], v1["threshold_status"], v1["status_reason"]) == (7.5, None, "unknown_category_share")
    assert v1["breakdown"]["voluntary_range"] == [7.5, 10.0] and v1["quality_score"] == 90.0
    assert v1["breakdown"]["voluntary_range_annualized"] == [7.5, 10.0]  # 365-day window
    assert k1["value"] == 25.0 and k1["status_reason"] is None  # KPI-001 unchanged
    _set_separations(db, tov, {"voluntary": 35, "employer_initiated": 20, "contract_end": 15, "project_end": 25,
                               "unknown": 5})
    v2 = ev(client, auth, "KPI-020", vb, **YEAR)
    assert (v2["value"], v2["threshold_status"], v2["status_reason"]) == (8.75, "good", None)  # u = 5 % included
    _set_separations(db, tov, {"voluntary": 15, "employer_initiated": 20, "contract_end": 15, "project_end": 25,
                               "unknown": 25})
    v3 = ev(client, auth, "KPI-020", vb, **YEAR)
    assert (v3["status"], v3["value"], v3["quality_score"], v3["status_reason"]) == ("insufficient_data", None, 75.0,
                                                                                     None)
    assert ev(client, auth, "KPI-001", vb, **YEAR)["value"] == 25.0


def test_voluntary_turnover_true_zero_and_just_cause_breakdown(client, auth, db, tov):
    vb = tov["entities"]["VB"]
    _set_separations(db, tov, {})
    v4 = ev(client, auth, "KPI-020", vb, **YEAR)
    assert (v4["value"], v4["threshold_status"], v4["quality_score"]) == (0.0, "good", 100.0)
    # V6: 8 of V1's 10 unknowns were SGK 23-25 -> voluntary (24 is stored as no code, only in the total)
    _set_separations(db, tov, {"voluntary": 38, "employer_initiated": 20, "contract_end": 15, "project_end": 25,
                               "unknown": 2}, codes={"23": 4, "25": 3})
    v6 = ev(client, auth, "KPI-020", vb, **YEAR)
    assert (v6["value"], v6["threshold_status"], v6["quality_score"]) == (9.5, "good", 98.0)
    assert v6["breakdown"]["voluntary_just_cause"] == 7  # 23 + 25 only; the 24 case has no stored code
    # low-information note: unknown 4 + other 12 = 16 % > 15 % (review condition 1); u = 4 % -> still coloured
    _set_separations(db, tov, {"voluntary": 30, "employer_initiated": 20, "contract_end": 15, "project_end": 19,
                               "other": 12, "unknown": 4})
    li = ev(client, auth, "KPI-020", vb, **YEAR)
    assert li["threshold_status"] == "good" and "Low information share: 16.0 %" in li["note"]
    assert li["lineage"]["low_information_share"] == 0.16


# ------------------------------------------------------------------ item 13: exit code import (WP-5 3.1, L-8)


def test_health_related_exit_codes_not_stored(client, auth, db, tov):
    tid = tov["tenant_id"]
    rows = [f"X{i};Hx;X{i};HX;permanent;2025-01-01;2026-05-1{i};{cat};{code};" for i, (cat, code) in enumerate([
        ("", "09"), ("", "24"), ("employer_initiated", "28"), ("", "11"), ("other", "37"), ("", "3"), ("", "24.0"),
        ("", "ab")], start=1)]
    up = client.post("/api/v1/imports", headers=auth(ADMIN), files={"file": ("health.csv", _csv(rows))},
                     data={"entity_type": "employment"}).json()
    col = "sgk çıkış kodu"
    assert up["suggested_mapping"]["sgk_exit_code"] == col
    staged = [r[col] for r in up["preview"]]
    assert staged[5] == "03" and staged[7] == "ab"  # kept code / not a code (format error)
    assert all(xc.is_masked(v) for i, v in enumerate(staged) if i not in (5, 7))
    assert staged[4] == "masked:unknown::"  # not in the table: the importer is told, the code is not kept
    blob = json.dumps(up["preview"])
    db.expire_all()
    blob += json.dumps([r.raw for r in db.execute(select(ImportStagingRow).where(
        ImportStagingRow.batch_id == up["batch_id"])).scalars()])
    for code in ('"09"', '"24"', '"28"', '"11"', '"37"', '"24.0"'):
        assert code not in blob
    v = client.post(f"/api/v1/imports/{up['batch_id']}/validate", json={"mapping": None}, headers=auth(ADMIN)).json()
    assert v["error_rows"] == 1  # 'ab'
    issues = _issues(db, up["batch_id"])
    assert {(i.record_ref, i.rule_code) for i in issues} >= {("X5", "unknown_exit_code"), ("X8", "exit_code_format")}
    assert not any(c in (i.message or "") for i in issues for c in ("09", "24", "28", "11", "37"))
    c = client.post(f"/api/v1/imports/{up['batch_id']}/commit", headers=auth(ADMIN)).json()
    assert c["committed_rows"] == 7
    got = {f"X{i}": _emp(db, tid, f"X{i}") for i in range(1, 8)}
    assert {k: (e.termination_category, e.termination_reason_code, e.termination_reason_source)
            for k, e in got.items()} == {
        "X1": ("retirement", None, "sgk"), "X2": ("voluntary", None, "sgk"), "X3": ("employer_initiated", None, "sgk"),
        "X4": ("death", None, "sgk"), "X5": ("other", None, "sgk"), "X6": ("voluntary", "03", "sgk"),
        "X7": ("voluntary", None, "sgk")}
    # code 11: legal entity x month count only (review condition 3)
    cnt = db.execute(select(TerminationCounter).where(TerminationCounter.tenant_id == tid)).scalars().all()
    assert [(x.legal_entity_id, x.period_month, x.counter, x.count) for x in cnt] == [
        (tov["entities"]["HX"], D(2026, 5, 1), "work_accident_death_exits", 1)]
    # after commit the staging rows keep no marker detail (category / counter)
    db.expire_all()
    staged = db.execute(select(ImportStagingRow).where(ImportStagingRow.batch_id == up["batch_id"])).scalars().all()
    assert {r.raw[col] for r in staged} == {"masked", "03", "ab"}
    assert not any("_exit_counter" in (r.mapped or {}) for r in staged)
    # re-import of the same exits (different file): unchanged spells, the counter does not move
    _run(client, auth, _csv([*rows[3:4], "X9;Hx;X9;HX;permanent;2025-01-01;;;;"]), "health_again.csv")
    db.expire_all()
    assert db.execute(select(TerminationCounter.count).where(TerminationCounter.tenant_id == tid)).scalar_one() == 1
    # manual correction with a health code: category stored, code not (same safe default)
    e = got["X6"]
    r = client.post(f"/api/v1/manage/employments/{e.id}/terminate", headers=auth(ADMIN),
                    json={"end_date": "2026-05-16", "termination_category": "voluntary", "record_version":
                          e.record_version, "termination_reason_code": "24"})
    assert r.status_code == 200, r.text
    assert r.json()["termination_reason_code"] is None
    db.expire_all()
    assert db.get(EmploymentRelationship, e.id).termination_reason_source == "manual"


def test_unverified_mapping_row_ignored(client, auth, db, tov):
    tid = tov["tenant_id"]
    row = db.execute(select(TerminationCodeMap).where(TerminationCodeMap.tenant_id == tid,
                                                      TerminationCodeMap.code == "04")).scalar_one()
    row.verified_at = None
    db.commit()
    try:
        up, _, _ = _run(client, auth, _csv(["U1;Un;U1;HX;permanent;2025-01-01;2026-06-10;voluntary;04;",
                                            "U2;Un;U2;HX;permanent;2025-01-01;2026-06-10;;04;"]), "unverified.csv")
        assert {(i.record_ref, i.rule_code) for i in _issues(db, up["batch_id"])} >= {
            ("U1", "exit_code_mapping_unverified"), ("U2", "exit_code_mapping_unverified"),
            ("U2", "termination_reason_missing")}
        u1, u2 = _emp(db, tid, "U1"), _emp(db, tid, "U2")
        assert (u1.termination_category, u1.termination_reason_code) == ("voluntary", "04")  # source category, not 04's
        assert u2.termination_category == "unknown"
    finally:
        xc.verify(db, tid, ["04"], "bordro uygulayıcısı (test)")


def test_sgk_exit_code_mapping_table_drives_category(client, auth, db, tov):
    tid = tov["tenant_id"]
    up, _, _ = _run(client, auth, _csv([
        "M1;Mp;M1;HX;permanent;2025-01-01;2026-07-01;;23;",
        "M2;Mp;M2;HX;permanent;2025-01-01;2026-07-01;;25;",
        "M3;Mp;M3;HX;permanent;2025-01-01;2026-07-01;voluntary;13;",  # L-9: 13 -> other, never voluntary
        "M4;Mp;M4;HX;seasonal;2025-04-01;2026-07-01;;19;2026-07-03"]), "mapping.csv")
    got = {k: _emp(db, tid, k) for k in ("M1", "M2", "M3", "M4")}
    assert [got[k].termination_category for k in ("M1", "M2", "M3", "M4")] == [
        "voluntary", "voluntary", "other", "contract_end"]
    assert [got[k].termination_reason_code for k in ("M1", "M2", "M3")] == ["23", "25", "13"]
    assert got["M4"].planned_end_verified is True  # 07-01 >= 07-03 - 7 days
    assert ("M3", "category_code_conflict") in {(i.record_ref, i.rule_code) for i in _issues(db, up["batch_id"])}


def test_transfer_code_16_without_new_spell_flagged(client, auth, db, tov):
    up, _, _ = _run(client, auth, _csv([
        "T1;Tr;T1;HX;permanent;2024-01-01;2026-08-14;;16;",
        "T2;Tr;T2;HX;permanent;2024-01-01;2026-08-14;;16;",
        "T2;Tr;T2;W2;permanent;2026-08-17;;;;"]), "transfer.csv")
    rules = {(i.record_ref, i.rule_code) for i in _issues(db, up["batch_id"])}
    assert ("T1", "transfer_without_new_spell") in rules and ("T2", "transfer_without_new_spell") not in rules


def test_planned_end_and_exit_code_validation_rules(client, auth, db, tov):
    up, _, _ = _run(client, auth, _csv([
        "P1;Pe;P1;HX;permanent;2025-01-01;;;;2026-12-31",  # planned end on permanent -> warning
        "P2;Pe;P2;HX;project;2025-01-01;;;;2024-12-31",  # planned end before start -> error
        "P3;Pe;P3;HX;permanent;2025-01-01;;;03;",  # code without end date -> ignored
        "P4;Pe;P4;HX;permanent;2025-01-01;2026-09-30;;18;"]), "rules.csv")  # project end on permanent
    rules = {(i.record_ref, i.rule_code, i.severity) for i in _issues(db, up["batch_id"])}
    assert {("P1", "planned_end_on_permanent", "warning"), ("P2", "planned_end_before_start", "error"),
            ("P3", "exit_code_without_end_date", "warning"), ("P4", "contract_end_on_permanent", "warning")} <= rules
    p4 = _emp(db, tov["tenant_id"], "P4")
    assert (p4.termination_category, p4.planned_end_verified) == ("project_end", False)
    assert _emp(db, tov["tenant_id"], "P3").termination_reason_code is None


def test_new_turnover_parameters_validated_per_calculator():
    validate_parameters({"days": 90, "planned_end_exclusion": "verified", "planned_end_alarm_points": 10,
                         "same_employer_basis": "group"}, "early_turnover_90")
    for bad in ({"planned_end_exclusion": "all"}, {"planned_end_alarm_points": 0}, {"same_employer_basis": "tenant"}):
        with pytest.raises(KpiError):
            validate_parameters(bad, "early_turnover_90")
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"planned_end_exclusion": "verified"}, "turnover_rate")
    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"unknown_share_bands": [0.05, 0.2]}, "retention_rate")
    for bad in ({"unknown_share_bands": [0.2, 0.05]}, {"unknown_share_bands": [0.05]}, {"low_information_share": 1.5},
                {"voluntary_just_cause_codes": ["023"]}):
        with pytest.raises(KpiError):
            validate_parameters(bad, "turnover_rate")
