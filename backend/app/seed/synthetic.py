"""Synthetic data generator (section 18). Deterministic (seeded). Produces NO real personal data:
names are drawn from small generic lists combined with sequence numbers, identity numbers are random digits.

Usage:  python -m app.seed.synthetic --employees 1200 --subcontractors 12 --months 28 [--reset]

Simplification (documented): the generator writes WEEKLY aggregated timesheet rows (work_date = Monday);
production feeds are daily. KPI formulas sum hours over dates and are granularity-independent.
"""
import argparse
import math
import os
import random
from collections import defaultdict
from datetime import date, time, timedelta

import numpy as np
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app.analytics import summary
from app.analytics.domain import month_end, month_starts
from app.core.db import SessionLocal, engine
from app.models import (
    FEED_TYPES,
    Assignment,
    Competency,
    ContractorRelationship,
    DataSource,
    EmploymentRelationship,
    FxRate,
    ImprovementAction,
    LegalEntity,
    Occupation,
    OccupationCompetency,
    Person,
    Project,
    ProjectLocation,
    Requisition,
    RiskItem,
    Shift,
    SurveyAggregate,
    SurveyCampaign,
    Tenant,
    User,
    UserRoleAssignment,
    WorkCalendar,
    WorkPackage,
)
from app.security.auth import hash_password, national_id_hash
from app.seed.reference import seed_permissions_and_roles, seed_reference
from app.services.coverage import declare_complete
from app.services.work_patterns import assign_pattern, define_pattern

FIRST = ["Ali", "Ayşe", "Mehmet", "Fatma", "Can", "Elif", "Murat", "Zeynep", "Ivan", "Olga", "John", "Maria", "Emre",
         "Deniz", "Selin", "Burak", "Nikolai", "Anna", "Kerem", "Ece"]
LAST = ["Demo", "Örnek", "Test", "Sample", "Primer", "Sentetik", "Model", "Pilot"]
OCCUPATIONS = [
    ("ENG-CIV", "İnşaat Mühendisi", "engineering", "2142", True),
    ("ENG-ELE", "Elektrik Mühendisi", "engineering", "2151", True),
    ("CON-FORE", "Şantiye Formeni", "construction", "3123", True),
    ("CON-CONC", "Beton Ustası", "construction", "7114", False),
    ("CON-REBAR", "Demirci", "construction", "7214", False),
    ("CON-FORM", "Kalıpçı", "construction", "7115", False),
    ("CON-LAB", "İnşaat İşçisi", "construction", "9313", False),
    ("MNT-CRANE", "Vinç Operatörü", "maintenance", "8343", True),
    ("MNT-ELEC", "Bakım Elektrikçisi", "maintenance", "7411", False),
    ("OFF-ADM", "İdari Personel", "office", "4110", False),
    ("HR-SPEC", "İK Uzmanı", "hr", "2423", False),
    ("SRV-CATR", "Yemekhane Personeli", "service", "9412", False),
]
SUB_TRADES = [
    ("Beton", ["CON-CONC", "CON-LAB", "CON-FORE"]), ("Demir", ["CON-REBAR", "CON-LAB", "CON-FORE"]),
    ("Kalıp", ["CON-FORM", "CON-LAB", "CON-FORE"]), ("Elektrik", ["MNT-ELEC", "ENG-ELE", "CON-LAB"]),
    ("Vinç", ["MNT-CRANE", "MNT-ELEC"]), ("Catering", ["SRV-CATR"]), ("Mühendislik", ["ENG-CIV", "ENG-ELE", "OFF-ADM"]),
]
TERM_WEIGHTS = [("voluntary", 45), ("employer_initiated", 15), ("contract_end", 12), ("project_end", 15),
                ("retirement", 3), ("other", 4), (None, 6)]  # None -> reason not provided (unknown)
WAGES = {"engineering": 95000, "construction": 42000, "maintenance": 52000, "office": 40000, "hr": 55000,
         "service": 33000}
DEMO_PASSWORD = os.getenv("HRSWI_DEMO_PASSWORD", "Demo!2345")


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _copy(table: str, cols: list[str], rows: list[tuple]) -> None:
    if not rows:
        return
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
        raw.commit()
    finally:
        raw.close()


def reset(db: Session, tenant_code: str) -> None:
    t = db.execute(select(Tenant).where(Tenant.code == tenant_code)).scalar()
    if t is None:
        return
    tables = [r[0] for r in db.execute(text(
        "SELECT table_name FROM information_schema.columns WHERE column_name = 'tenant_id' AND table_schema='public'"))]
    # delete children first: repeat until no FK errors (simple and safe for a dev reset)
    db.execute(text("SET session_replication_role = replica"))
    for tb in tables:
        db.execute(text(f"DELETE FROM {tb} WHERE tenant_id = :t"), {"t": t.id})
    db.execute(text("DELETE FROM user_role_assignments WHERE user_id NOT IN (SELECT id FROM users)"))
    db.execute(text("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE tenant_id = :t)"), {"t": t.id})
    db.execute(text("DELETE FROM import_staging_rows WHERE batch_id NOT IN (SELECT id FROM import_batches)"))
    db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": t.id})
    db.execute(text("SET session_replication_role = DEFAULT"))
    db.commit()


def generate(db: Session, *, employees: int = 1200, subcontractors: int = 12, months: int = 28,
             as_of: date | None = None, seed: int = 42, tenant_code: str = "DEMO") -> dict:
    rnd = random.Random(seed)
    nrng = np.random.default_rng(seed)
    as_of = as_of or date.today()
    seed_reference(db)
    t = Tenant(code=tenant_code, name="Demo İnşaat Grubu (SENTETİK VERİ)", default_currency="TRY", country="TR")
    db.add(t)
    db.flush()
    tid = t.id
    role_ids = seed_permissions_and_roles(db, tid)

    # ---------------- organisation
    client = LegalEntity(tenant_id=tid, code="OWNER", name="Proje Sahibi İdare (Demo)", entity_type="client")
    mc = LegalEntity(tenant_id=tid, code="MC", name="Ana Yüklenici İnşaat A.Ş. (Demo)", entity_type="main_contractor")
    db.add_all([client, mc])
    db.flush()
    subs = []
    for i in range(subcontractors):
        trade, occs = SUB_TRADES[i % len(SUB_TRADES)]
        le = LegalEntity(tenant_id=tid, code=f"SUB{i + 1:02d}", name=f"{trade} Alt Yüklenici {i + 1:02d} Ltd. (Demo)",
                         entity_type="subcontractor", country="TR" if i % 6 else "RU")
        subs.append((le, occs))
    db.add_all([s[0] for s in subs])
    db.flush()
    start_hist = _add_months(date(as_of.year, as_of.month, 1), -months)
    projects = []
    for code, name, phase in [("PRJ-MTR", "Metro Hattı (Demo)", "construction"),
                              ("PRJ-HSP", "Şehir Hastanesi (Demo)", "construction"),
                              ("PRJ-HES", "Hidroelektrik Santrali (Demo)", "mobilisation")]:
        p = Project(tenant_id=tid, code=code, name=name, owner_entity_id=client.id, phase=phase,
                    start_date=start_hist - timedelta(days=400), end_date=as_of + timedelta(days=700))
        projects.append(p)
    db.add_all(projects)
    db.flush()
    db.add_all([ProjectLocation(tenant_id=tid, project_id=p.id, name=f"{p.code} Şantiye", city=c)
                for p, c in zip(projects, ["İstanbul", "Ankara", "Artvin"], strict=False)])
    rels = []
    for i, (le, _) in enumerate(subs):
        parent = mc.id
        tier = 1
        if i >= subcontractors - 2 and subcontractors > 3:
            parent, tier = subs[0][0].id, 2  # tier-2 under SUB01
        rels.append(ContractorRelationship(
            tenant_id=tid, parent_entity_id=parent, child_entity_id=le.id, project_id=projects[i % 3].id, tier=tier,
            valid_from=start_hist - timedelta(days=365), valid_to=None, scope_of_work=SUB_TRADES[i % len(SUB_TRADES)][0],
            data_sharing_granted=(i % 5 != 4)))  # every 5th firm has NOT granted data sharing
    db.add_all(rels)

    occ_rows = {}
    for code, name, grp, isco, crit in OCCUPATIONS:
        o = Occupation(tenant_id=tid, code=code, name=name, occupation_group=grp, isco_code=isco, is_critical=crit)
        db.add(o)
        occ_rows[code] = o
    db.flush()
    comps = {}
    for code, name, cat, val in [("ISG-TEMEL", "Temel İSG Eğitimi", "hse", 12), ("YUKSEKTE", "Yüksekte Çalışma", "hse", 24),
                                 ("VINC-BELGE", "Vinç Operatör Belgesi", "legal_certificate", 60),
                                 ("ELEK-YETKI", "Elektrik Yetki Belgesi", "legal_certificate", 60),
                                 ("MESLEKI-YET", "Mesleki Yeterlilik Belgesi", "legal_certificate", 60)]:
        c = Competency(tenant_id=tid, code=code, name=name, category=cat, validity_months=val)
        db.add(c)
        comps[code] = c
    db.flush()
    reqs = {o: ["ISG-TEMEL"] for o in occ_rows}
    for o in ("CON-CONC", "CON-REBAR", "CON-FORM", "CON-LAB", "CON-FORE"):
        reqs[o] += ["YUKSEKTE", "MESLEKI-YET"]
    reqs["MNT-CRANE"] += ["VINC-BELGE"]
    reqs["MNT-ELEC"] += ["ELEK-YETKI"]
    for o, cs in reqs.items():
        for c in cs:
            db.add(OccupationCompetency(tenant_id=tid, occupation_id=occ_rows[o].id, competency_id=comps[c].id))
    db.add(WorkCalendar(tenant_id=tid, code="TR-45", country="TR", weekly_hours=45))
    db.add_all([Shift(tenant_id=tid, code="DAY", name="Gündüz", start_time=time(8), end_time=time(18), planned_hours=9),
                Shift(tenant_id=tid, code="NIGHT", name="Gece", start_time=time(22), end_time=time(6),
                      crosses_midnight=True, planned_hours=7.5)])
    db.add_all([DataSource(tenant_id=tid, code="FILE", name="Excel/CSV upload", kind="file"),
                DataSource(tenant_id=tid, code="HRIS-MOCK", name="HRIS connector (mock)", kind="hris"),
                DataSource(tenant_id=tid, code="TURNSTILE", name="Turnike sistemi (mock)", kind="time_attendance",
                           status="unavailable", last_error="Connection timed out (simulated)")])
    db.flush()

    # ---------------- workforce simulation
    work_entities = [(mc, ["ENG-CIV", "ENG-ELE", "CON-FORE", "OFF-ADM", "HR-SPEC", "CON-LAB"]), *subs]
    shares = nrng.dirichlet(np.ones(len(work_entities)) * 2.0)
    shares[0] = max(shares[0], 0.15)
    shares = shares / shares.sum()
    profile = {}
    for idx, (le, occs) in enumerate(work_entities):
        profile[le.id] = {
            "base": max(8, int(employees * shares[idx])), "occs": occs,
            "attr": rnd.uniform(0.012, 0.055), "ot": rnd.uniform(0.03, 0.2), "eff": rnd.uniform(0.8, 1.15),
            "rework": rnd.uniform(0.02, 0.09), "lti_rate": rnd.uniform(0.5, 8.0) / 1e6,
            "productive_tracking": rnd.random() > 0.15, "comp_cov": rnd.uniform(0.75, 0.99),
            "currency": "USD" if le.country == "RU" else "TRY", "project": projects[idx % 3],
            "train": rnd.uniform(0.4, 2.0),
        }
    month_list = month_starts(start_hist, as_of)
    persons: list[dict] = []
    emps: list[dict] = []  # {pidx, le, start, end, cat, occ}
    active: dict[int, list[int]] = defaultdict(list)

    def new_person():
        i = len(persons)
        persons.append({"tenant_id": tid, "person_code": f"P{i + 1:06d}", "first_name": rnd.choice(FIRST),
                        "last_name": f"{rnd.choice(LAST)}-{i + 1}", "birth_year": rnd.randint(1962, 2005),
                        "gender": rnd.choices(["M", "F"], [85, 15])[0], "nationality": "TR",
                        "national_id_hash": national_id_hash(str(rnd.randint(10**10, 10**11 - 1))),
                        "source_system": "synthetic"})
        return i

    def hire(le_id, start, pidx=None):
        pidx = new_person() if pidx is None else pidx
        occs = profile[le_id]["occs"]
        emps.append({"pidx": pidx, "le": le_id, "start": start, "end": None, "cat": None, "occ": rnd.choice(occs),
                     "ct": rnd.choices(["permanent", "fixed_term", "project"], [40, 25, 35])[0]})
        active[le_id].append(len(emps) - 1)

    def factor(m: date) -> float:
        ramp = min(1.0, 0.7 + 0.3 * month_list.index(m) / 6) if m in month_list else 1.0
        season = 1 + 0.15 * math.sin(2 * math.pi * (m.month - 4) / 12)
        return ramp * season

    m0 = month_list[0]
    for le_id, pr in profile.items():
        for _ in range(int(pr["base"] * factor(m0))):
            hire(le_id, m0 - timedelta(days=rnd.randint(30, 5 * 365)))
    for m in month_list:
        me = min(month_end(m), as_of)
        days = (me - m).days + 1
        for le_id, pr in profile.items():
            still = []
            for ei in active[le_id]:
                e = emps[ei]
                tenure = (m - e["start"]).days
                p_leave = pr["attr"] * (1.8 if tenure < 90 else 1.0)
                if e["start"] < m and rnd.random() < p_leave:
                    e["end"] = m + timedelta(days=rnd.randint(0, days - 1))
                    e["cat"] = rnd.choices([c for c, _ in TERM_WEIGHTS], [w for _, w in TERM_WEIGHTS])[0]
                    if rnd.random() < 0.04:  # internal transfer to another group company next day
                        other = rnd.choice([x for x in profile if x != le_id])
                        e["cat"] = "other"
                        hire(other, e["end"] + timedelta(days=1), pidx=e["pidx"])
                else:
                    still.append(ei)
            active[le_id] = still
            target = int(pr["base"] * factor(m))
            gap = target - len(still) + rnd.randint(-2, 2)
            for _ in range(max(0, gap)):
                hire(le_id, m + timedelta(days=rnd.randint(0, days - 1)))
    # persist people and employments
    pids = db.execute(insert(Person).returning(Person.id, sort_by_parameter_order=True), persons).scalars().all()
    emp_rows = [{"tenant_id": tid, "person_id": pids[e["pidx"]], "legal_entity_id": e["le"],
                 "employee_number": f"E{i + 1:07d}", "contract_type": e["ct"], "start_date": e["start"],
                 "end_date": e["end"], "termination_category": (e["cat"] or "unknown") if e["end"] else None,
                 "source_system": "synthetic"} for i, e in enumerate(emps)]
    # a few separations deliberately lack a reason (None) -> stored as NULL category to exercise 'unknown'
    for i, e in enumerate(emps):
        if e["end"] and e["cat"] is None:
            emp_rows[i]["termination_category"] = None
    eids = db.execute(insert(EmploymentRelationship).returning(EmploymentRelationship.id,
                                                              sort_by_parameter_order=True), emp_rows).scalars().all()
    occ_id = {k: v.id for k, v in occ_rows.items()}
    db.execute(insert(Assignment), [
        {"tenant_id": tid, "employment_id": eids[i], "project_id": profile[e["le"]]["project"].id,
         "occupation_id": occ_id[e["occ"]], "valid_from": e["start"],
         "valid_to": (e["end"] + timedelta(days=1)) if e["end"] else None, "fte": 1, "is_primary": True}
        for i, e in enumerate(emps)])
    db.commit()

    # ---------------- weekly timesheets, leave, payroll, training
    ts_rows, leave_rows, cost_rows, train_rows = [], [], [], []
    monthly_ot: dict[tuple[int, date], float] = defaultdict(float)
    first_monday = start_hist - timedelta(days=start_hist.weekday())
    night_emp = {i for i in range(len(emps)) if rnd.random() < 0.12}
    for i, e in enumerate(emps):
        pr = profile[e["le"]]
        s = max(e["start"], start_hist)
        end = min(e["end"] or as_of, as_of)
        if end < s:
            continue
        wk = first_monday + timedelta(days=7 * max(0, (s - first_monday).days // 7))
        while wk <= end:
            wk_end = wk + timedelta(days=6)
            act_days = (min(end, wk_end - timedelta(days=1)) - max(s, wk)).days + 1  # Mon-Sat
            if act_days > 0:
                planned = round(45 * min(act_days, 6) / 6, 2)
                absence, ltype = 0.0, None
                r = rnd.random()
                if r < 0.03:
                    ltype, absence = "sick", 9.0 * rnd.randint(1, 3)
                elif r < 0.07:
                    ltype, absence = "annual", 9.0 * rnd.randint(2, 5)
                elif r < 0.076:
                    ltype, absence = "unexcused", 9.0
                elif r < 0.081:
                    ltype, absence = "unpaid", 9.0 * rnd.randint(1, 2)
                absence = min(absence, planned)
                season = 1.4 if wk.month in (6, 7, 8) else 1.0
                ot = round(max(0.0, nrng.normal(pr["ot"] * 45 * season, 2.0)), 1) if absence == 0 else 0.0
                worked = round(planned - absence + ot, 2)
                prod = round((planned - absence) * rnd.uniform(0.68, 0.9), 2) if pr["productive_tracking"] else None
                night = round(min(worked, 37.5), 2) if i in night_emp else 0
                ts_rows.append((tid, eids[i], pr["project"].id, wk, planned, worked, ot, night, prod, "approved",
                                "synthetic", 1))
                monthly_ot[(i, date(wk.year, wk.month, 1))] += ot
                if ltype:
                    ls = max(wk, s)
                    leave_rows.append((tid, eids[i], ltype, ls, ls + timedelta(days=max(0, int(absence // 9) - 1)),
                                       absence, "synthetic", 1))
            wk += timedelta(days=7)
        # monthly payroll costs
        wage = WAGES[occ_rows[e["occ"]].occupation_group] * rnd.uniform(0.85, 1.25)
        for m in month_starts(s, end):
            frac = ((min(month_end(m), end) - max(m, s)).days + 1) / ((month_end(m) - m).days + 1)
            ccy = pr["currency"]
            gross = wage * frac / (34.0 if ccy == "USD" else 1)
            hourly = gross / 195 if frac else 0
            for ctype, amt in [("gross_wage", gross), ("employer_ss", gross * 0.1875),
                               ("overtime", monthly_ot[(i, m)] * hourly * 1.5), ("meal", 2600 * frac / (34 if ccy == "USD" else 1)),
                               ("transport", 1800 * frac / (34 if ccy == "USD" else 1))]:
                if amt > 0:
                    cost_rows.append((tid, eids[i], pr["project"].id, m, ctype, round(amt, 2), ccy, "synthetic", 1))
        # training
        for m in month_starts(s, end):
            if rnd.random() < 0.12 * pr["train"]:
                cat = rnd.choices(["hse", "technical", "compliance", "soft"], [50, 30, 10, 10])[0]
                train_rows.append((tid, eids[i], f"TR-{cat.upper()[:3]}-{rnd.randint(1, 20):02d}", f"{cat} eğitimi",
                                   cat, rnd.choice([2, 4, 8, 16]), m + timedelta(days=rnd.randint(0, 27)),
                                   cat == "hse", "synthetic", 1))
    _copy("timesheets", ["tenant_id", "employment_id", "project_id", "work_date", "planned_hours", "worked_hours",
                         "overtime_hours", "night_hours", "productive_hours", "status", "source_system",
                         "record_version"], ts_rows)
    _copy("leave_records", ["tenant_id", "employment_id", "leave_type", "start_date", "end_date", "hours",
                            "source_system", "record_version"], leave_rows)
    _copy("payroll_costs", ["tenant_id", "employment_id", "project_id", "period_month", "cost_type", "amount",
                            "currency", "source_system", "record_version"], cost_rows)
    _copy("training_records", ["tenant_id", "employment_id", "course_code", "course_name", "category", "hours",
                               "completed_on", "mandatory", "source_system", "record_version"], train_rows)

    # competencies: most active people hold their occupation's requirements
    ec_rows = []
    for e in emps:
        for c in reqs[e["occ"]]:
            if rnd.random() < profile[e["le"]]["comp_cov"]:
                ec_rows.append((tid, pids[e["pidx"]], comps[c].id, 1, e["start"] - timedelta(days=rnd.randint(0, 300)),
                                None))
    _copy("employee_competencies", ["tenant_id", "person_id", "competency_id", "level", "valid_from", "valid_to"],
          sorted(set(ec_rows), key=lambda r: (r[1], r[2])))

    # FX (synthetic, clearly not official)
    fx = []
    rate = 32.5
    for m in month_starts(start_hist - timedelta(days=40), as_of):
        fx.append(FxRate(rate_date=month_end(m), from_ccy="USD", to_ccy="TRY", rate=round(rate, 4), source="SYNTHETIC"))
        rate *= 1.012
    existing = {(r.rate_date, r.from_ccy) for r in db.execute(select(FxRate)).scalars()}
    db.add_all([f for f in fx if (f.rate_date, f.from_ccy) not in existing])

    # safety events, plans, outputs, downtime, requisitions
    hours_by = defaultdict(float)
    for r in ts_rows:
        hours_by[(r[1], date(r[3].year, r[3].month, 1))] += float(r[5])
    emp_le = {eids[i]: e["le"] for i, e in enumerate(emps)}
    le_month_hours = defaultdict(float)
    for (eid, m), h in hours_by.items():
        le_month_hours[(emp_le[eid], m)] += h
    se_rows, plan_rows, out_rows, dt_rows, rq_rows = [], [], [], [], []
    for (le_id, m), h in le_month_hours.items():
        pr = profile[le_id]
        for etype, mult in [("lost_time_injury", 1), ("medical_treatment", 2.5), ("first_aid", 8), ("near_miss", 15),
                            ("restricted_work", 0.8)]:
            for _ in range(nrng.poisson(h * pr["lti_rate"] * mult)):
                se_rows.append((tid, le_id, pr["project"].id, m + timedelta(days=rnd.randint(0, 27)), etype,
                                rnd.randint(1, 20) if etype == "lost_time_injury" else 0, "synthetic", 1))
    for le_id, pr in profile.items():
        for m in month_starts(start_hist, _add_months(as_of, 6)):
            plan_rows.append((tid, le_id, pr["project"].id, None, m,
                              max(1, int(pr["base"] * factor(m) * rnd.uniform(0.9, 1.12))), None, 1))
    db.commit()
    _copy("safety_events", ["tenant_id", "legal_entity_id", "project_id", "event_date", "event_type", "lost_days",
                            "source_system", "record_version"], se_rows)
    _copy("workforce_plans", ["tenant_id", "legal_entity_id", "project_id", "occupation_id", "period_month",
                              "planned_headcount", "planned_hours", "plan_version"], plan_rows)
    wps = []
    for p in projects:
        for code, name, unit, grp, std in [("BET", "Beton dökümü", "m3", "construction", 1.8),
                                           ("KAL", "Kalıp imalatı", "m2", "construction", 0.9),
                                           ("DON", "Donatı montajı", "ton", "construction", 22.0),
                                           ("ELK", "Kablo çekimi", "m", "maintenance", 0.12),
                                           ("DOK", "Doküman kontrol", "adet", "office", None)]:
            wp = WorkPackage(tenant_id=tid, project_id=p.id, code=f"{p.code}-{code}", name=name, unit=unit,
                             occupation_group=grp)
            db.add(wp)
            db.flush()
            wps.append((wp, std))
            if std is not None:
                db.execute(text("INSERT INTO production_standards (tenant_id, work_package_id, std_hours_per_unit, "
                                "source, valid_from, created_at, updated_at) VALUES (:t, :w, :s, 'Demo standard', :f, "
                                "now(), now())"), {"t": tid, "w": wp.id, "s": std, "f": start_hist - timedelta(days=500)})
    for le_id, pr in profile.items():
        mine = [(wp, std) for wp, std in wps if wp.project_id == pr["project"].id and wp.occupation_group in
                {occ_rows[o].occupation_group for o in pr["occs"]}]
        for m in month_starts(start_hist, as_of):
            for wp, std in mine:
                labor = round(max(50.0, nrng.normal(le_month_hours.get((le_id, m), 0) * 0.15 / max(1, len(mine)), 30)), 1)
                eff = pr["eff"] * rnd.uniform(0.9, 1.1)
                qty = round(labor / (std or 1.0) * eff, 2)
                rw = pr["rework"] * rnd.uniform(0.6, 1.4)
                accepted = round(qty * (1 - rw), 2) if rnd.random() > 0.05 else None
                out_rows.append((tid, wp.id, le_id, month_end(m) if month_end(m) <= as_of else as_of, qty, accepted,
                                 round(qty * rw, 2), labor, round(labor * rw, 1), "synthetic", 1))
            if rnd.random() < 0.5:
                dt_rows.append((tid, pr["project"].id, le_id, m + timedelta(days=rnd.randint(0, 27)),
                                rnd.choice(["equipment", "material", "weather", "permit"]),
                                round(rnd.uniform(4, 80), 1), rnd.randint(3, 40)))
    db.commit()  # COPY uses its own connection: referenced rows must be committed
    _copy("work_outputs", ["tenant_id", "work_package_id", "legal_entity_id", "output_date", "quantity",
                           "accepted_quantity", "rework_quantity", "labor_hours", "rework_hours", "source_system",
                           "record_version"], out_rows)
    _copy("downtime_events", ["tenant_id", "project_id", "legal_entity_id", "event_date", "cause", "lost_hours",
                              "affected_headcount"], dt_rows)
    for e in emps:
        if e["start"] >= start_hist and rnd.random() < 0.3:
            appr = e["start"] - timedelta(days=rnd.randint(20, 75))
            rq_rows.append(Requisition(tenant_id=tid, legal_entity_id=e["le"], occupation_id=occ_id[e["occ"]],
                                       approved_on=appr, offer_accepted_on=e["start"] - timedelta(days=rnd.randint(3, 14)),
                                       status="filled"))
    db.add_all(rq_rows)

    # surveys (aggregates only, n >= 10)
    camp = SurveyCampaign(tenant_id=tid, code="ENG-2026", title="Çalışan Deneyimi Anketi 2026",
                          start_date=_add_months(as_of, -3), end_date=_add_months(as_of, -2))
    db.add(camp)
    db.flush()
    for le_id in profile:
        n = int(len(active[le_id]) * rnd.uniform(0.3, 0.7))
        if n < 10:
            continue  # group too small: not stored at all
        for dim in ("engagement", "workload", "management_comm", "conditions"):
            db.add(SurveyAggregate(tenant_id=tid, campaign_id=camp.id, legal_entity_id=le_id, dimension=dim,
                                   respondents=n, mean_score=round(rnd.uniform(2.6, 4.3), 2),
                                   favorable_pct=round(rnd.uniform(35, 80), 1)))

    # ---------------- users (demo)
    users_spec = [
        ("admin@demo.local", "Sistem Yöneticisi", "SYSTEM_ADMIN", None),
        ("hr.director@demo.local", "İK Direktörü", "HR_DIRECTOR", None),
        ("hr.manager@demo.local", "İK Müdürü", "HR_MANAGER", None),
        ("finance@demo.local", "Finans Analisti", "FINANCE_ANALYST", None),
        ("exec@demo.local", "Üst Yönetim", "READONLY_EXECUTIVE", None),
        ("sustainability@demo.local", "Sürdürülebilirlik Müdürü", "SUSTAINABILITY_MANAGER", None),
        ("hse@demo.local", "İSG Uzmanı", "HSE_SPECIALIST", None),
        ("auditor@demo.local", "İç Denetçi", "INTERNAL_AUDITOR", None),
        ("mc.ops@demo.local", "Ana Yüklenici Operasyon Direktörü", "OPERATIONS_DIRECTOR", mc.id),
        ("sub01.manager@demo.local", "SUB01 Yüklenici Yöneticisi", "CONTRACTOR_MANAGER", subs[0][0].id),
        ("sub02.manager@demo.local", "SUB02 Yüklenici Yöneticisi", "CONTRACTOR_MANAGER", subs[1][0].id),
    ]
    pw = hash_password(DEMO_PASSWORD)
    users = {}
    for email, name, role, le_id in users_spec:
        u = User(tenant_id=tid, email=f"{email.split('@')[0]}@{tenant_code.lower()}.local" if tenant_code != "DEMO" else email,
                 full_name=name, password_hash=pw, locale="tr")
        db.add(u)
        db.flush()
        users[role if le_id is None else email] = u
        db.add(UserRoleAssignment(user_id=u.id, role_id=role_ids[role], scope_type="legal_entity" if le_id else "tenant",
                                  legal_entity_id=le_id))
    owner = users["HR_DIRECTOR"].id
    r1 = RiskItem(tenant_id=tid, legal_entity_id=subs[0][0].id, category="turnover", title="SUB01 yüksek personel devri",
                  severity="high", owner_user_id=owner, target_date=_add_months(as_of, 2), source_kpi_code="KPI-001")
    r2 = RiskItem(tenant_id=tid, legal_entity_id=mc.id, category="competency", title="Vinç operatörü belge süreleri doluyor",
                  severity="critical", owner_user_id=owner, target_date=_add_months(as_of, 1), source_kpi_code="KPI-009")
    r3 = RiskItem(tenant_id=tid, legal_entity_id=subs[1][0].id, category="overtime", title="Yaz döneminde aşırı fazla mesai",
                  severity="medium", owner_user_id=owner, target_date=_add_months(as_of, 3), source_kpi_code="KPI-007")
    db.add_all([r1, r2, r3])
    db.flush()
    db.add_all([
        ImprovementAction(tenant_id=tid, risk_id=r1.id, legal_entity_id=subs[0][0].id, problem="İlk 90 gün ayrılışları yüksek",
                          kpi_code="KPI-018", baseline_value=24.0, target_value=12.0, owner_user_id=owner, priority="P1",
                          start_date=_add_months(as_of, -5), due_date=_add_months(as_of, -1),
                          steps_taken="Oryantasyon programı ve usta-çırak eşleştirmesi", review_date=_add_months(as_of, -1),
                          result_value=17.5, effectiveness="partially", status="completed"),
        ImprovementAction(tenant_id=tid, risk_id=r3.id, legal_entity_id=subs[1][0].id, problem="Fazla mesai oranı > %15",
                          kpi_code="KPI-007", baseline_value=18.2, target_value=10.0, owner_user_id=owner, priority="P0",
                          start_date=_add_months(as_of, -4), due_date=_add_months(as_of, 1),
                          steps_taken="İlave ekip ve vardiya planı", status="in_progress", effectiveness="pending"),
        ImprovementAction(tenant_id=tid, risk_id=r2.id, legal_entity_id=mc.id, problem="Belge yenileme takibi",
                          kpi_code="KPI-009", baseline_value=82.0, target_value=95.0, owner_user_id=owner, priority="P0",
                          start_date=_add_months(as_of, -6), due_date=_add_months(as_of, -2), result_value=96.0,
                          effectiveness="effective", status="completed"),
    ])
    # B-20 / ADR-020: every feed this generator produces is complete for each employer and month it covers, so the
    # demo dashboard keeps its colours; a real tenant gets coverage from imports / manual declarations instead.
    # ADR-024: the generated timesheets carry the full planned week incl. leave days -> 'included' (WP-4 3.5 scenario A)
    declare_complete(db, tid, [mc.id] + [s[0].id for s in subs], FEED_TYPES, start_hist, as_of,
                     note="synthetic demo data: generated feeds are complete", invalidate=False,
                     planned_on_leave_days="included")
    # ADR-024 / WP-4 3.1: every employer works the 5 x 9 h pattern (the generator's leave hours are 9 h per day, so the
    # leave-days KPI-055/056 keep the demo values - WP-4 E2); a 6 x 7.5 h site pattern is defined for imports/demos.
    p59 = define_pattern(db, tid, "TR-5x9", [1, 2, 3, 4, 5], 9, source_system="synthetic")
    define_pattern(db, tid, "TR-6x7.5", [1, 2, 3, 4, 5, 6], 7.5, source_system="synthetic")
    for le_id in [mc.id] + [s[0].id for s in subs]:
        assign_pattern(db, tid, p59, "legal_entity", le_id, date(2000, 1, 1), source_system="synthetic",
                       invalidate=False)
    db.execute(text("UPDATE tenants SET data_version = data_version + 1 WHERE id = :t"), {"t": tid})
    db.commit()
    summary.refresh(db, tid)  # rebuild the timesheet monthly summary for the freshly loaded data (B-21)
    return {"tenant_id": tid, "persons": len(persons), "employments": len(emps), "timesheets": len(ts_rows),
            "leave": len(leave_rows), "costs": len(cost_rows), "safety_events": len(se_rows),
            "outputs": len(out_rows), "demo_password": DEMO_PASSWORD}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--employees", type=int, default=1200)
    ap.add_argument("--subcontractors", type=int, default=12)
    ap.add_argument("--months", type=int, default=28)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tenant", default="DEMO")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    with SessionLocal() as db:
        if a.reset:
            reset(db, a.tenant)
        if db.execute(select(Tenant).where(Tenant.code == a.tenant)).scalar():
            print(f"Tenant {a.tenant} already exists; use --reset to regenerate")
            return
        stats = generate(db, employees=a.employees, subcontractors=a.subcontractors, months=a.months, seed=a.seed,
                         tenant_code=a.tenant)
    print({k: v for k, v in stats.items() if k != "demo_password"})
    print("Demo users: admin@demo.local, hr.director@demo.local, mc.ops@demo.local, sub01.manager@demo.local, ... "
          "(password from HRSWI_DEMO_PASSWORD, default 'Demo!2345')")


if __name__ == "__main__":
    main()
