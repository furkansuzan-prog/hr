"""Seed catalog: 50 KPI business definitions (Master Prompt section 5).

Rules:
- A KPI is `active` + `validated` only if a registered calculator exists AND its rules were reviewed
  (see docs/KPI_CATALOG.md, review status column). All others are `draft` and cannot run in production.
- Standard references are *indicative mappings for disclosure preparation*, not compliance claims.
  Framework versions must be verified by the Sustainability specialist before reporting (version_verified=false).
"""
from app.models.operations import LONG_ABSENCE_TYPES

ACTIVE_COMMON = {
    "frequency": "monthly",
    "scope_levels": ["tenant", "legal_entity", "project", "occupation_group"],
    "missing_data_behavior": "insufficient_data when source rows absent; not_computable when denominator = 0; "
    "suppressed when population < HRSWI_MIN_GROUP_SIZE",
}


# Thresholds of KPIs with params.annualize=True apply to the ANNUALISED value (value x 365 / period days),
# so that a 1-month and a 12-month view are judged on the same scale.


def _k(code, family, module, tr, en, ru, unit, direction, calculator=None, *, formula, num, den, sources, owner,
       standards=(), purpose="", incl="", excl="", params=None, target=None, warn=None, crit=None, sensitive=False,
       status=None):
    active = calculator is not None and status != "draft"
    return {
        "code": code, "family": family, "module": module, "name": {"tr": tr, "en": en, "ru": ru},
        "description": formula, "purpose": purpose or f"Track {en.lower()}", "unit": unit, "direction": direction,
        "frequency": ACTIVE_COMMON["frequency"], "data_sources": list(sources), "data_owner_role": owner,
        "scope_levels": ACTIVE_COMMON["scope_levels"], "inclusion_rules": incl or "All records in scope and period",
        "exclusion_rules": excl or "None", "missing_data_behavior": ACTIVE_COMMON["missing_data_behavior"],
        "standard_refs": list(standards), "target_value": target, "warning_threshold": warn,
        "critical_threshold": crit, "status": "active" if active else "draft", "validated": active,
        "sensitive": sensitive,
        "formula": {"calculator_key": calculator or "unimplemented", "formula_text": formula, "numerator_def": num,
                    "denominator_def": den, "parameters": params or {}},
    }


# WP-2 R1/R2 (HR Director approved with conditions, 2026-11-05); ADR-018.
_SAFETY_PURPOSE = (
    "Colour only when hours worked in the window reach the minimum exposure (formula parameter "
    "threshold_min_exposure_hours); below it the value and event counts are shown without colour (status_reason "
    "low_exposure) - small scopes are never coloured, judge them at company/tenant level. Any fatality in scope "
    "surfaces an alert independent of the rate. The thresholds count as active only once KPI-043 (near-miss "
    "reporting rate) is active (HR Director section 3.3 rule 2; docs/hr/hse/WP-2). One safety_events row = one case "
    "(ADR-021). Scope: the company's own employees only (decision D-10) - the HSE import does not store "
    "subcontractor, temporary-agency or third-party cases."
)
# Decision D-10 (PO-3): HSE covers ONLY the firm's own employees. Formula parameter own_employees_only replaces the
# ADR-021 out-of-rate rule (exclude_out_of_rate_cases, kept only for older formula versions); new versions via sync.
_OWN_EMPLOYEES_INCL = ("Cases of the company's own employees (worker_category own_employee; legacy rows without a "
                       "category count as own - they were recorded per employing legal entity)")
_OWN_EMPLOYEES_EXCL = ("Subcontractor, temporary-agency and third-party cases (decision D-10: rejected by the HSE "
                       "import; legacy rows of these categories are not counted and shown as a count only)")

KPI_CATALOG: list[dict] = [
    # ---------------------------------------------------------------- Workforce sustainability / turnover
    _k("KPI-001", "workforce_stability", "E", "Personel Devir Oranı", "Employee Turnover Rate", "Текучесть кадров", "%",
       "lower_better", "turnover_rate",
       formula="Separations in period / average headcount in period x 100",
       num="Employment spells ending in period, excluding internal transfers (new in-scope employment within 7 days)",
       den="Mean distinct-person headcount at period start and each month end",
       sources=["employment_relationships"], owner="HR_DIRECTOR",
       standards=["ISO 30414 (turnover rate)", "GRI 401-1", "ESRS S1-6"],
       purpose="Workforce stability; separations are also reported by termination category",
       incl="Voluntary, employer-initiated, contract/project end, retirement, death, other, unknown",
       excl="Internal transfers between in-scope entities", warn=30, crit=45,
       params={"annualize": True}),
    _k("KPI-002", "workforce_stability", "B", "Personel Devamlılık Oranı", "Retention Rate", "Коэффициент удержания", "%",
       "higher_better", "retention_rate",
       formula="Persons employed in scope at period start and still employed in scope at period end / persons at start x 100",
       num="Start population still employed at period end", den="Distinct persons employed at period start",
       sources=["employment_relationships"], owner="HR_DIRECTOR", standards=["ISO 30414 (retention rate)"],
       purpose="Workforce stability. Judged only on >= 12-month windows (colour from 360 days; shorter windows show "
       "the value without colour, status_reason short_window). Never annualised: retention does not scale linearly "
       "(docs/hr/data/WP-5 section 3.6)",
       target=80, warn=70, crit=60, params={"threshold_min_days": 360}),
    _k("KPI-003", "attendance", "D", "Devamsızlık Oranı", "Absenteeism Rate", "Уровень абсентеизма", "%",
       "lower_better", "absenteeism_rate",
       formula="Absence hours of included types / planned working hours x 100",
       num="Leave hours prorated to period for types in parameter absence_types",
       den="Planned hours on approved timesheets", sources=["leave_records", "timesheets"], owner="HR_MANAGER",
       standards=["ISO 30414 (absenteeism)"],
       # Company definition (decision D-3, revised by D-8 / PO-1 option C): unexcused absence, sick-note days, annual
       # leave, unpaid leave and other leave count as absence. Work-injury / occupational-disease rest (work_injury,
       # ADR-019) is EXCLUDED (D-8) - it is reported under safety (KPI-014A/B, lost days), not attendance. The change
       # was a catalog parameter edit only; the catalog sync created a new formula version, older results keep theirs.
       incl="Unexcused absence, sick-note days (raporlu), annual leave, unpaid leave, other leave",
       excl="Public holidays (not working days); parental/maternity and related protected leave (decision D-6); "
       "work-injury / occupational-disease rest (work_injury, SGK report codes 1/2 - decision D-8, reported under "
       "occupational safety)",
       params={"absence_types": ["unexcused", "sick", "annual", "unpaid", "other"],
               "excluded_types": ["public_holiday", "parental", "work_injury"]}),
    _k("KPI-004", "productivity", "C", "İş Gücü Kapasite Kullanımı", "Workforce Capacity Utilisation",
       "Использование мощности персонала", "%", "higher_better", "capacity_utilization",
       formula="Productive (work-package booked) hours / planned available hours x 100",
       num="Productive hours on timesheets", den="Planned hours on the same timesheet rows",
       sources=["timesheets"], owner="OPERATIONS_DIRECTOR",
       incl="Only timesheet rows where productive hours were recorded", excl="Rows with unknown productive time",
       params={"min_coverage": 0.5}),
    _k("KPI-005", "productivity", "C", "İş Gücü Verimlilik Endeksi", "Labour Productivity Index",
       "Индекс производительности труда", "index", "higher_better", "productivity_index",
       formula="Standard hours earned by quality-accepted output / actual labour hours for that output x 100",
       num="Sum(accepted quantity x standard hours per unit)", den="Labour hours on outputs having a standard",
       sources=["work_outputs", "production_standards", "quality inspections"], owner="OPERATIONS_DIRECTOR",
       incl="Work types with a valid production standard and an inspection result",
       excl="Outputs without standard or without accepted quantity (counted in lineage)", target=100, warn=90, crit=80),
    _k("KPI-006", "planning", "I", "İş Gücü Plan Sapması", "Workforce Plan Deviation", "Отклонение от плана персонала",
       "persons", "neutral", "plan_deviation",
       formula="Actual headcount - planned headcount (mean over months with a plan); % in breakdown",
       num="Actual month-end headcount", den="Planned headcount (workforce_plans)",
       sources=["workforce_plans", "employment_relationships"], owner="OPERATIONS_DIRECTOR"),
    _k("KPI-007", "working_time", "D", "Fazla Çalışma Oranı", "Overtime Rate", "Доля сверхурочной работы", "%",
       "lower_better", "overtime_rate",
       formula="Overtime hours / total actual worked hours x 100", num="Overtime hours (approved timesheets)",
       den="Worked hours incl. overtime", sources=["timesheets"], owner="HR_MANAGER",
       standards=["4857 s. İş Kanunu md. 41 (annual cap 270h) - legal review required"], warn=10, crit=15),
    _k("KPI-008", "development", "H", "Çalışan Başına Eğitim Saati", "Training Hours per Employee",
       "Часы обучения на сотрудника", "hours", "higher_better", "training_hours_per_employee",
       formula="Total training hours completed in period / average headcount",
       num="Training hours completed", den="Average headcount", sources=["training_records"], owner="HR_MANAGER",
       standards=["ISO 30414 (training hours)", "GRI 404-1", "ESRS S1-13"], params={"annualize": True},
       target=24, warn=16, crit=8),
    _k("KPI-009", "development", "H", "Yetkinlik Kapsama Oranı", "Competency Coverage Rate", "Покрытие компетенций", "%",
       "higher_better", "competency_coverage",
       formula="Active assignments whose holder has all mandatory competencies valid / evaluated assignments x 100",
       num="Covered assignments at period end", den="Active assignments in occupations with mandatory competencies",
       sources=["assignments", "occupation_competencies", "employee_competencies"], owner="HR_MANAGER",
       target=95, warn=90, crit=80),
    _k("KPI-010", "cost", "F", "İş Gücü Birim Maliyeti", "Unit Labour Cost", "Удельные затраты на труд",
       "TRY/unit", "lower_better", "unit_labor_cost",
       formula="Labour cost allocated to the output (reporting currency) / accepted output quantity",
       num="Payroll cost converted with fx_rates", den="Accepted output quantity",
       sources=["payroll_costs", "fx_rates", "work_outputs"], owner="FINANCE_ANALYST",
       incl="Meaningful at project / work-package level with a single output unit", sensitive=True,
       params={"reporting_currency": "TRY"}),
    _k("KPI-011", "recruitment", "A", "İşe Alım Süresi", "Time to Hire", "Время найма", "days", "lower_better",
       "time_to_hire", formula="Mean days between approved requisition and accepted offer",
       num="Sum of days", den="Requisitions filled in period", sources=["requisitions"], owner="HR_MANAGER",
       standards=["ISO 30414 (time to fill)"]),
    _k("KPI-012", "continuity", "K", "İş Gücü Süreklilik Riski", "Workforce Continuity Risk",
       "Риск непрерывности персонала", "index", "lower_better", None,
       formula="Explainable composite of critical-role turnover, critical vacancy, competency gap and backup capacity",
       num="Weighted component scores", den="Sum of weights", sources=["positions", "assignments", "KPI-001", "KPI-009"],
       owner="HR_DIRECTOR",
       purpose="Organisation-level only. DRAFT until weights are validated with HR; never computed per individual"),
    _k("KPI-013", "quality", "C", "Yeniden İş Yapma Oranı", "Rework Rate", "Доля переделок", "%", "lower_better",
       "rework_rate", formula="Rework hours / total labour hours on recorded outputs x 100",
       num="Rework hours", den="Labour hours", sources=["work_outputs"], owner="OPERATIONS_DIRECTOR", warn=5, crit=8),
    _k("KPI-014A", "health_safety", "G", "Kayıp Günlü Kaza Sıklık Oranı (LTIFR)", "Lost Time Injury Frequency Rate",
       "LTIFR", "per 1M h", "lower_better", "ltifr",
       formula="(Lost-time injuries + fatalities) / hours worked x 1,000,000",
       num="LTI + fatality events", den="Hours worked (timesheets)", sources=["safety_events", "timesheets"],
       owner="HSE_SPECIALIST", standards=["ISO 45001 (context)", "GRI 403-9", "ESRS S1-14", "ISO 30414"],
       purpose="Lost-time injury frequency. " + _SAFETY_PURPOSE + " Minimum exposure: 1,000,000 h.",
       incl=_OWN_EMPLOYEES_INCL, excl=_OWN_EMPLOYEES_EXCL,
       params={"multiplier": 1_000_000, "threshold_min_exposure_hours": 1_000_000, "own_employees_only": True}),
    _k("KPI-014B", "health_safety", "G", "Toplam Kaydedilebilir Olay Oranı (TRIR)", "Total Recordable Incident Rate",
       "TRIR", "per 200k h", "lower_better", "trir",
       formula="Recordable injuries (fatality, LTI, restricted work, medical treatment) / hours worked x 200,000",
       num="Recordable events", den="Hours worked", sources=["safety_events", "timesheets"], owner="HSE_SPECIALIST",
       standards=["GRI 403-9 (rate basis must be disclosed)"],
       purpose="Recordable injury frequency. " + _SAFETY_PURPOSE + " Minimum exposure: 500,000 h (HR Director WP-2 Q2).",
       incl=_OWN_EMPLOYEES_INCL, excl=_OWN_EMPLOYEES_EXCL,
       params={"multiplier": 200_000, "threshold_min_exposure_hours": 500_000, "own_employees_only": True}),
    # WP-2 R3 + HR Director Q3/Q6 (permanently neutral, no thresholds); wave-2 item 10, ADR-021
    _k("KPI-043", "health_safety", "G", "Ramak Kala Bildirim Oranı", "Near-Miss Reporting Rate",
       "Частота сообщений о почти-происшествиях", "per 200k h", "neutral", "near_miss_rate",
       formula="Near-miss reports / hours worked x 200,000",
       num="Safety cases classified near_miss with event_date in the window (one row per report, de-duplicated by "
           "the import key source_system + event_ref + case_no)",
       den="Hours worked on approved timesheets in the same scope (same denominator as KPI-014A/B)",
       sources=["safety_events", "timesheets"], owner="HSE_SPECIALIST",
       standards=["ISO 45001 10.2 (context)", "6331 md. 14 (to be confirmed)"],
       purpose="Context for KPI-014A/B and input of the reporting-quality signals - NEVER a target, quota or bonus "
               "basis (lower_better punishes reporting, higher_better becomes a quota). Direction neutral, no "
               "thresholds. Below 200,000 h the value carries the 'low exposure' label. Reporter identity is not "
               "collected; no person / team / foreman breakdown.",
       incl="Near misses reported to the HSE system (6331 md. 14(1)(b) to be confirmed)",
       excl="Unsafe condition / unsafe act observations (HR Director WP-2 Q6); injuries; scopes whose safety coverage "
            "declares includes_near_miss = false (not covered - not a real zero)",
       params={"multiplier": 200_000, "threshold_min_exposure_hours": 200_000}),
    _k("KPI-015", "sustainable_employment", "B", "Sürdürülebilir İstihdam Göstergeleri",
       "Sustainable Employment Indicator Family", "Показатели устойчивой занятости", "family", "neutral", None,
       formula="Indicator FAMILY (KPI-002, 008, 009, 014A/B, survey wellbeing, social protection) - not a single score",
       num="n/a", den="n/a", sources=["multiple"], owner="SUSTAINABILITY_MANAGER",
       purpose="Presented as a dashboard family; deliberately not collapsed into a legal sustainability score"),
    _k("KPI-016", "workforce_size", "A", "Aktif Personel Sayısı", "Active Headcount", "Численность персонала",
       "persons", "neutral", "active_headcount", formula="Distinct persons with active employment on period end date",
       num="Distinct persons", den="n/a", sources=["employment_relationships"], owner="HR_MANAGER",
       standards=["ISO 30414 (workforce)", "GRI 2-7", "ESRS S1-6"]),
    _k("KPI-017", "workforce_stability", "E", "İşe Giriş Sayısı", "New Hires", "Новые сотрудники", "persons",
       "neutral", "new_hire_count", formula="Employments starting in period, excluding internal transfers",
       num="New employments", den="n/a", sources=["employment_relationships"], owner="HR_MANAGER",
       standards=["GRI 401-1"]),
    _k("KPI-018", "workforce_stability", "E", "İlk 90 Gün Ayrılış Oranı", "90-Day Early Turnover",
       "Текучесть в первые 90 дней", "%", "lower_better", "early_turnover_90",
       formula="Hires starting in period who left within 90 days / hires starting in period x 100",
       num="Early leavers", den="Hires in cohort", sources=["employment_relationships"], owner="HR_MANAGER",
       params={"days": 90}),
    _k("KPI-019", "cost", "F", "Çalışan Başına İşgücü Maliyeti", "Labour Cost per Employee",
       "Затраты на сотрудника", "TRY", "neutral", "labor_cost_per_employee",
       formula="Total employer labour cost (reporting currency) / average headcount",
       num="Employer cost incl. social security and benefits", den="Average headcount",
       sources=["payroll_costs", "fx_rates"], owner="FINANCE_ANALYST", standards=["ISO 30414 (workforce costs)"],
       sensitive=True, params={"reporting_currency": "TRY"}),
    # ---------------------------------------------------------------- B-10: activated from the draft catalog
    _k("KPI-020", "workforce_stability", "E", "Gönüllü Devir Oranı", "Voluntary Turnover Rate", "Добровольная текучесть",
       "%", "lower_better", "turnover_rate",
       formula="Voluntary separations in period / average headcount in period x 100",
       num="Employment spells ending in period with termination category 'voluntary', excluding internal transfers "
           "(new in-scope employment within 7 days)",
       den="Mean distinct-person headcount at period start and each month end (same as KPI-001)",
       sources=["employment_relationships"], owner="HR_DIRECTOR", standards=["ISO 30414", "GRI 401-1"],
       purpose="Separations the organisation can influence through retention measures",
       incl="Termination category 'voluntary' only",
       excl="Internal transfers; separations with no recorded reason are NOT assumed voluntary (lowers quality score)",
       params={"annualize": True, "include_categories": ["voluntary"]}),
    _k("KPI-021", "workforce_stability", "E", "İlk 30 Gün Ayrılış Oranı", "30-Day Early Turnover",
       "Текучесть за 30 дней", "%", "lower_better", "early_turnover_90",
       formula="Hires starting in period who left within 30 days / hires starting in period x 100",
       num="Early leavers (end date < start + 30 days, not a transfer out)", den="Hires in cohort (transfers in excluded)",
       sources=["employment_relationships"], owner="HR_MANAGER", params={"days": 30}),
    _k("KPI-022", "workforce_stability", "E", "İlk 180 Gün Ayrılış Oranı", "180-Day Early Turnover",
       "Текучесть за 180 дней", "%", "lower_better", "early_turnover_90",
       formula="Hires starting in period who left within 180 days / hires starting in period x 100",
       num="Early leavers (end date < start + 180 days, not a transfer out)",
       den="Hires in cohort (transfers in excluded)", sources=["employment_relationships"], owner="HR_MANAGER",
       params={"days": 180}),
    _k("KPI-023", "workforce_stability", "B", "Ortalama Kıdem", "Average Tenure", "Средний стаж", "years", "neutral",
       "average_tenure", formula="Mean tenure (years) of persons active in scope at period end",
       num="Sum over active persons of (period end - start of current spell) in days / 365.25",
       den="Distinct persons active in scope at period end", sources=["employment_relationships"],
       owner="HR_MANAGER", standards=["ISO 30414"],
       incl="Current spell only; concurrent spells -> the longest-running one",
       excl="Earlier (ended) spells of re-hired persons are not added"),
    _k("KPI-028", "attendance", "D", "Hastalık Devamsızlık Oranı (iş kazası hariç)",
       "Sickness Absence Rate (excl. work injury)", "Уровень болезней (без производственных травм)", "%",
       "lower_better", "absenteeism_rate", formula="Sick leave hours / planned hours x 100 (aggregate only)",
       num="Sick leave hours prorated to period", den="Planned hours on approved timesheets",
       sources=["leave_records", "timesheets"], owner="HR_MANAGER", standards=["ISO 30414"],
       incl="Leave type 'sick' only", excl="Work-injury / occupational-disease rest (work_injury, SGK report codes "
       "1/2 - WP-1, ADR-019) and all other leave types. Before ADR-019 work-injury rest was imported as 'sick'",
       params={"absence_types": ["sick"],
               "excluded_types": ["unexcused", "work_injury", "annual", "unpaid", "other", "public_holiday",
                                  "parental"]}),
    _k("KPI-032", "working_time", "D", "Gece Çalışması Oranı", "Night Work Share", "Доля ночной работы", "%",
       "lower_better", "night_work_share", formula="Night hours / worked hours x 100",
       num="Night hours on approved timesheets", den="Worked hours (incl. overtime) on approved timesheets",
       sources=["timesheets"], owner="HR_MANAGER", standards=["4857 md. 69"]),
    _k("KPI-034", "productivity", "C", "Duruş Kaynaklı Kayıp Saat Oranı", "Downtime Lost Hours Share",
       "Потери из-за простоев", "%", "lower_better", "downtime_lost_hours_share",
       formula="Downtime lost hours / planned hours x 100 (lost hours by cause in breakdown)",
       num="downtime_events.lost_hours in period (project / legal entity scope)",
       den="Planned hours on approved timesheets in the same scope", sources=["downtime_events", "timesheets"],
       owner="OPERATIONS_DIRECTOR",
       excl="Occupation filter not supported (events carry no occupation -> insufficient_data); events without a "
            "legal entity are excluded when an entity filter applies (counted in lineage)"),
    _k("KPI-036", "cost", "F", "Fazla Mesai Maliyet Oranı", "Overtime Cost Share", "Доля затрат на сверхурочные", "%",
       "lower_better", "overtime_cost_share", formula="Overtime cost / total labour cost x 100 (reporting currency)",
       num="payroll_costs of cost_type 'overtime' converted with fx_rates",
       den="All payroll_costs converted with fx_rates (same as KPI-019 total)", sources=["payroll_costs", "fx_rates"],
       owner="FINANCE_ANALYST", incl="All cost types in denominator", excl="Any missing FX rate -> insufficient_data",
       sensitive=True, params={"reporting_currency": "TRY"}),
    _k("KPI-048", "diversity", "B", "Kadın Çalışan Oranı", "Female Employee Share", "Доля женщин", "%", "neutral",
       "female_share", formula="Female headcount / headcount with recorded gender x 100 (at period end)",
       num="Distinct active persons recorded as female", den="Distinct active persons with a recorded gender",
       sources=["employment_relationships", "people"], owner="HR_DIRECTOR", standards=["GRI 405-1", "ESRS S1-9"],
       incl="Persons with recorded gender", excl="Unknown gender (counted in lineage); suppressed when the female OR "
                                                  "male count is below the minimum group size"),
    _k("KPI-050", "contractor", "J", "Alt Yüklenici İş Gücü Payı", "Subcontractor Workforce Share",
       "Доля персонала субподрядчиков", "%", "neutral", "subcontractor_share",
       formula="Persons employed by subcontractor entities / all persons in scope x 100 (at period end)",
       num="Distinct active persons with an employment at an entity of type 'subcontractor'",
       den="Distinct active persons in scope", sources=["employment_relationships", "legal_entities"],
       owner="OPERATIONS_DIRECTOR", standards=["ESRS S1-7 (non-employees)"]),
]

# ---------------------------------------------------------------- Draft catalog (designed, not yet validated)
_DRAFTS = [
    ("KPI-024", "workforce_stability", "E", "Kritik Rol Devir Oranı", "Critical Role Turnover", "Текучесть критических ролей", "%", "Separations in critical occupations / avg critical headcount x 100", ["ISO 30414"]),
    ("KPI-025", "continuity", "K", "Halefiyet Kapsama Oranı", "Succession Coverage", "Покрытие преемственности", "%", "Critical positions with >=1 ready successor / critical positions x 100", ["ISO 30414"]),
    ("KPI-026", "continuity", "K", "Kritik Pozisyon Doluluk Oranı", "Critical Position Fill Rate", "Заполненность критических позиций", "%", "Filled critical positions / budgeted critical positions x 100", []),
    ("KPI-027", "continuity", "K", "Yedekleme Kapasitesi", "Backup Capacity Ratio", "Коэффициент резервирования", "ratio", "Qualified persons per critical competency / required holders", []),
    ("KPI-029", "attendance", "D", "Geç Kalma Oranı", "Late Arrival Rate", "Доля опозданий", "%", "Shifts started late per badge data / scheduled shifts x 100 (presence only)", []),
    ("KPI-030", "attendance", "D", "Puantaj-Kart Uyumsuzluk Oranı", "Timesheet-Badge Mismatch Rate", "Несоответствие табеля и пропусков", "%", "Timesheet days without matching badge presence / timesheet days x 100", []),
    ("KPI-031", "working_time", "D", "Yıllık Fazla Mesai Limit Aşımı", "Annual Overtime Cap Breaches", "Превышение лимита сверхурочных", "persons", "Employees above annual overtime cap (default 270h, legal review)", ["4857 md. 41"]),
    ("KPI-033", "productivity", "C", "Adam-Saat Plan Sapması", "Man-Hour Plan Variance", "Отклонение человеко-часов", "%", "(Actual - planned man-hours) / planned x 100", []),
    ("KPI-035", "productivity", "C", "Fazla Mesai Verimlilik Esnekliği", "Overtime Output Elasticity", "Эластичность выпуска по сверхурочным", "ratio", "% change accepted output / % change overtime hours (team level, descriptive only)", []),
    ("KPI-037", "cost", "F", "İşçilik Bütçe Sapması", "Labour Budget Variance", "Отклонение бюджета на труд", "%", "(Actual - budget) / budget x 100 per cost centre", []),
    ("KPI-038", "cost", "F", "Personel Değişim Maliyeti", "Cost of Turnover", "Стоимость текучести", "currency", "Separations x (recruitment + onboarding + productivity ramp cost) - model assumptions disclosed", ["ISO 30414"]),
    ("KPI-039", "cost", "F", "İşe Alım Başına Maliyet", "Cost per Hire", "Стоимость найма", "currency", "Recruitment cost / hires", ["ISO 30414"]),
    ("KPI-040", "development", "H", "Zorunlu Eğitim Tamamlama Oranı", "Mandatory Training Completion", "Выполнение обязательного обучения", "%", "Completed mandatory trainings / required x 100", []),
    ("KPI-041", "development", "H", "Süresi Dolan Sertifika Oranı", "Expiring Certificates Share", "Истекающие сертификаты", "%", "Certificates expiring in 60 days / valid certificates x 100", []),
    ("KPI-042", "development", "H", "Eğitim Yatırımı / Çalışan", "Training Spend per Employee", "Затраты на обучение на сотрудника", "currency", "Training cost / avg headcount", ["ISO 30414"]),
    ("KPI-044", "health_safety", "G", "Kayıp Gün Oranı", "Lost Day Rate", "Коэффициент потерянных дней", "per 200k h", "Lost days / hours worked x 200,000", ["GRI 403-9 (2016 legacy)"]),
    ("KPI-045", "health_safety", "G", "İSG Eğitim Saati / Çalışan", "HSE Training Hours per Employee", "Часы обучения ОТ на сотрудника", "hours", "HSE category training hours / avg headcount", ["6331 s. Kanun"]),
    ("KPI-046", "wellbeing", "L", "Çalışan Bağlılık Skoru", "Employee Engagement Score", "Индекс вовлеченности", "score", "Mean engagement score from survey aggregates (n>=10)", ["ISO 30414"]),
    ("KPI-047", "wellbeing", "L", "İş Yükü Algısı", "Perceived Workload Score", "Восприятие нагрузки", "score", "Mean workload score from survey aggregates (n>=10)", []),
    ("KPI-049", "diversity", "B", "Yönetimde Kadın Oranı", "Women in Management", "Женщины в руководстве", "%", "Female managers / managers x 100", ["GRI 405-1", "ESRS S1-9"]),
    ("KPI-051", "contractor", "J", "Alt Yüklenici Belge Uyum Oranı", "Subcontractor Document Compliance", "Соответствие документов субподрядчиков", "%", "Workers with valid mandatory documents / workers x 100", []),
    ("KPI-052", "environment", "N", "Çalışan Başına Ulaşım Emisyonu (Tahmini)", "Estimated Commuting Emissions per Employee", "Оценка выбросов от поездок на сотрудника", "kgCO2e", "Sum(distance x factor) / headcount - ESTIMATE, factors and boundary disclosed", ["GHG Protocol Scope 3 Cat. 7", "ISO 14064-1"]),
]

for code, fam, mod, tr, en, ru, unit, formula, stds in _DRAFTS:
    KPI_CATALOG.append(
        _k(code, fam, mod, tr, en, ru, unit, "neutral", None, formula=formula, num="see formula", den="see formula",
           sources=["to be mapped"], owner="HR_DIRECTOR", standards=stds,
           sensitive=fam == "cost")
    )
KPI_CATALOG.sort(key=lambda k: k["code"])  # activated drafts sit in code order in docs and the definitions API


# ---------------------------------------------------------------- Leave & absence (decisions D-5..D-7, 2026-11-05)
KPI_CATALOG += [
    _k("KPI-053", "attendance", "D", "Plansız Devamsızlık Oranı", "Unplanned Absence Rate", "Незапланированное отсутствие",
       "%", "lower_better", "absenteeism_rate",
       formula="(Unexcused absence + sick-note hours) / planned working hours x 100",
       num="Unexcused and sick leave hours prorated to period", den="Planned hours on approved timesheets",
       sources=["leave_records", "timesheets"], owner="HR_MANAGER", standards=["ISO 30414 (absenteeism)"],
       purpose="Standard (unplanned) absenteeism, comparable with sector practice; complements the company KPI-003",
       incl="Unexcused absence, sick-note days", excl="Work-injury / occupational-disease rest (work_injury, SGK "
       "report codes 1/2 - WP-1, ADR-019; before ADR-019 imported as 'sick'), annual leave, unpaid leave, other leave, "
       "public holidays, maternity/parental and other protected leave",
       params={"absence_types": ["unexcused", "sick"],
               "excluded_types": ["work_injury", "annual", "unpaid", "other", "public_holiday", "parental"]}),
    _k("KPI-054", "attendance", "D", "Korunan İzin Payı", "Protected Leave Share", "Доля охраняемых отпусков", "%",
       "neutral", "protected_leave_share",
       formula="Maternity / parental and related protected leave hours / planned working hours x 100",
       num="Protected leave hours prorated to period (leave type 'parental')", den="Planned hours on approved timesheets",
       sources=["leave_records", "timesheets"], owner="HR_DIRECTOR",
       standards=["4857 md. 74, Ek md. 2", "6701 (non-discrimination)", "KVKK md. 6"],
       purpose="Capacity-planning figure only. Never part of absenteeism, never a target (decision D-6)",
       incl="Leave type 'parental' (maternity, paternity, parental, nursing, pregnancy check-ups - mapped on import)",
       excl="All other leave", params={"absence_types": ["parental"]}),
    _k("KPI-055", "wellbeing", "D", "Yıllık İzin Kullanım Oranı", "Annual Leave Utilisation", "Использование ежегодного отпуска",
       "%", "higher_better", "annual_leave_usage",
       formula="Annual-leave days taken / statutory entitlement days (4857 md.53, prorated to the period) x 100",
       num="Annual-leave days taken in the period (hours / hours_per_day)",
       den="Statutory entitlement of persons active at period end with >= 1 year tenure, prorated to period length",
       sources=["leave_records", "employment_relationships", "people"], owner="HR_DIRECTOR",
       standards=["4857 md. 53-59", "ISO 30414 (wellbeing)"],
       purpose="Sustainable, humane leave practice: leave is taken, not accumulated (decision D-7). Group level only",
       incl="Persons with >= 1 year of service with the current employer (all spells, 4857 md.54)",
       excl="Persons with < 1 year of service", params={"hours_per_day": 9, "threshold_min_days": 360}),
    _k("KPI-056", "wellbeing", "D", "Yıllık İzin Kullanmayan Çalışan Oranı", "Employees Without Annual Leave",
       "Доля сотрудников без отпуска", "%", "lower_better", "annual_leave_nonuse",
       formula="Eligible persons with no annual leave taken in the period / eligible persons x 100",
       num="Eligible persons with zero annual-leave days in the period", den="Persons active at period end with >= 1 "
       "year tenure", sources=["leave_records", "employment_relationships"], owner="HR_DIRECTOR",
       standards=["4857 md. 53-59"],
       purpose="Early warning for workload / burnout at team level (decision D-7); never used to rank individuals",
       incl="Persons whose one-year date is on/before the period start", excl="Persons with < 1 year of service at "
       "period start; persons on long sick / parental / unpaid / work-injury absence (> half of the window; reported "
       "as a count - an injured person is not 'not taking leave', WP-1 example 3, ADR-019)",
       params={"hours_per_day": 9, "threshold_min_days": 360, "long_absence_types": list(LONG_ABSENCE_TYPES)}),
]

# ---------------------------------------------------------------- Turnover v2 (WP-5 + HR Director review; ADR-023)
# KPI-018/021/022: verified planned contract/project ends leave the numerator (S-1 'verified'), coding-shift alarm at
# >= 10 points over 3 periods (S-5), repeated planned ends counted across the company group (decision D-11 / PO-4:
# seniority is recognised between group companies -> same_employer_basis 'group'). KPI-020: unknown-category bands 0.05 / 0.20 (S-4), low-information note > 15 %
# (condition 1: unknown + other), voluntary_just_cause = SGK 23 / 25 (condition 4). New formula versions via sync.
EARLY_TURNOVER_V2 = {"planned_end_exclusion": "verified", "planned_end_alarm_points": 10,
                     "same_employer_basis": "group"}
VOLUNTARY_TURNOVER_V2 = {"unknown_share_bands": [0.05, 0.2], "low_information_share": 0.15,
                         "voluntary_just_cause_codes": ["23", "25"]}
_PLANNED_END_TEXT = ("; verified planned contract/project ends (SGK code, fixed-term contract type, end within 7 days "
                     "of the planned end) are excluded from the numerator, the excluded share is shown separately")
for _item in KPI_CATALOG:
    if _item["code"] in ("KPI-018", "KPI-021", "KPI-022"):
        _item["formula"]["parameters"] = {**_item["formula"]["parameters"], **EARLY_TURNOVER_V2}
        _item["formula"]["numerator_def"] += _PLANNED_END_TEXT
        _item["exclusion_rules"] = ("Internal transfers; verified planned contract/project ends (planned_end_verified "
                                    "= true). Unverifiable planned ends are counted (never assumed planned)")
    elif _item["code"] == "KPI-020":
        _item["formula"]["parameters"] = {**_item["formula"]["parameters"], **VOLUNTARY_TURNOVER_V2}
        _item["missing_data_behavior"] += (
            "; unknown-category share u: u <= 5 % coloured, 5-20 % value without colour (status_reason "
            "unknown_category_share) with the possible range, > 20 % insufficient_data")

# ---------------------------------------------------------------- Work patterns (WP-4 + HR Director review; ADR-024)
# KPI-055/056: leave DAYS by work pattern (6-day site: hours / 7.5), entitlement = max(statutory, approved contractual
# rules), service = leave seniority date or the union of spells; pattern coverage below 0.8 -> insufficient_data
# (condition 1: key min_pattern_coverage, S-1: no transition value). KPI-003/028/053/054: planned-capacity denominator -
# leave hours added back per employee-day without a planned timesheet row (condition 2), quality 50 + note when it
# cannot be verified (S-2). New formula versions via catalog sync; older versions keep their results.
ANNUAL_LEAVE_V2 = {"day_basis": "leave_days", "min_pattern_coverage": 0.8}
ABSENCE_DENOMINATOR_V2 = {"denominator_basis": "planned_capacity"}
_CAPACITY_DEN = ("Planned hours on approved timesheets + leave hours (all types except public holidays) of employee "
                 "working days without a planned timesheet row (planned capacity; ADR-024)")
for _item in KPI_CATALOG:
    if _item["code"] in ("KPI-055", "KPI-056"):
        _item["formula"]["parameters"] = {**_item["formula"]["parameters"], **ANNUAL_LEAVE_V2}
        _item["missing_data_behavior"] += (
            "; work pattern known for < 80 % of the eligible population (min_pattern_coverage) -> insufficient_data "
            "(quality = pattern coverage); unknown-pattern persons otherwise use 9 h per day")
        if _item["code"] == "KPI-055":
            _item["formula"]["numerator_def"] = ("Annual-leave days taken in the period: stored leave days, else hours "
                                                 "/ daily hours of the person's work pattern (9 h fallback), "
                                                 "window share by pattern working days")
            _item["formula"]["denominator_def"] = (
                "Entitlement = max(statutory 4857 md.53, approved contractual / collective-agreement rules) of persons "
                "active at period end with >= 1 year of service (leave seniority date, else union of spells), "
                "prorated to period length")
            _item["formula"]["formula_text"] = ("Annual-leave days taken / entitlement days (max of statutory and "
                                                "approved contractual rules, prorated to the period) x 100")
            _item["inclusion_rules"] = ("Persons with >= 1 year of service with the current employer (payroll leave "
                                        "seniority date, else the union of all spells - overlaps once, 4857 md.54)")
        else:
            _item["formula"]["numerator_def"] = "Eligible persons with zero annual-leave days in the period"
            _item["exclusion_rules"] += ("; long absence = leave days of these types > half of the window's working "
                                         "days of the person's work pattern (5-day x 5/7, 6-day x 6/7)")
    elif _item["code"] in ("KPI-003", "KPI-028", "KPI-053", "KPI-054"):
        _item["formula"]["parameters"] = {**_item["formula"]["parameters"], **ABSENCE_DENOMINATOR_V2}
        _item["formula"]["denominator_def"] = _CAPACITY_DEN

# ---------------------------------------------------------------- Approved thresholds (decision D-5)
# Source: docs/HR_DIRECTOR_RECOMMENDATIONS.md (Agent 17), approved by the product owner on 2026-11-05.
# (target, warning, critical); None = monitoring only. Thresholds of annualised KPIs apply to the annualised value.
APPROVED_THRESHOLDS: dict[str, tuple] = {
    "KPI-001": (35, 45, 60), "KPI-002": (75, 65, 55), "KPI-003": (7, 9, 11), "KPI-004": (85, 80, 70),
    "KPI-005": (100, 90, 80), "KPI-006": None, "KPI-007": (5, 8, 10), "KPI-008": (30, 20, 16),
    "KPI-009": (100, 98, 95), "KPI-010": None, "KPI-011": (30, 45, 60), "KPI-013": (3, 5, 8),
    "KPI-014A": (1.5, 3.0, 5.0), "KPI-014B": (1.0, 2.0, 3.5), "KPI-016": None, "KPI-017": None,
    "KPI-018": (10, 15, 20), "KPI-019": None, "KPI-020": (12, 18, 25), "KPI-021": (3, 5, 8),
    "KPI-022": (18, 25, 32), "KPI-023": None, "KPI-028": (2.0, 3.0, 4.5), "KPI-032": (5, 10, 15),
    "KPI-034": None, "KPI-036": (7, 11, 14), "KPI-048": None, "KPI-050": None,
    "KPI-053": (2.5, 3.5, 5.0), "KPI-054": None,
    "KPI-055": (90, 75, 60), "KPI-056": (5, 10, 15),  # HR Director annex E.1; colour only on >= 12-month windows
}
for _item in KPI_CATALOG:
    if _item["code"] in APPROVED_THRESHOLDS:
        _t = APPROVED_THRESHOLDS[_item["code"]] or (None, None, None)
        _item["target_value"], _item["warning_threshold"], _item["critical_threshold"] = _t


# ---------------------------------------------------------------- Data-feed coverage gate (B-20, ADR-020)
# Source: docs/hr/data/WP-6_import_field_dictionaries.md section 3.6 + HR Director decision S-1 (general 0.8,
# safety 0.9). Engine-level formula parameters `required_feeds` / `min_feed_coverage`: the engine checks
# data_feed_coverage for these feeds over scope x window before a value may be coloured (warn) or shown (enforce).
# The warn / enforce mode is NOT a formula parameter but deployment configuration HRSWI_COVERAGE_MODES (D-13: default=warn,safety=enforce).
# A change here is a new formula version (catalog sync); older versions keep their (gate-free) behaviour.
REQUIRED_FEEDS: dict[str, tuple[str, ...]] = {
    **{c: ("employment",) for c in ("KPI-001", "KPI-002", "KPI-016", "KPI-017", "KPI-018", "KPI-020", "KPI-021",
                                    "KPI-022", "KPI-023")},
    **{c: ("timesheet", "leave") for c in ("KPI-003", "KPI-028", "KPI-053", "KPI-054")},
    **{c: ("employment", "leave") for c in ("KPI-055", "KPI-056")},
    **{c: ("timesheet",) for c in ("KPI-004", "KPI-007", "KPI-032")},
    **{c: ("timesheet", "safety") for c in ("KPI-014A", "KPI-014B", "KPI-043")},
    "KPI-008": ("employment", "training"),
    "KPI-019": ("cost", "employment"),  # denominator = average headcount
    "KPI-036": ("cost",),  # overtime cost / total cost - both from the cost feed
    "KPI-010": ("cost", "output"),  # denominator = accepted output quantity
}
MIN_FEED_COVERAGE = {"default": 0.8, "safety": 0.9}
for _item in KPI_CATALOG:
    _feeds = REQUIRED_FEEDS.get(_item["code"])
    if _feeds and _item["status"] == "active":
        _item["formula"]["parameters"] = {
            **_item["formula"]["parameters"], "required_feeds": list(_feeds),
            "min_feed_coverage": MIN_FEED_COVERAGE["safety" if "safety" in _feeds else "default"]}
        _item["missing_data_behavior"] += (
            f"; coverage gate on feeds {', '.join(_feeds)} (ADR-020): coverage below min_feed_coverage -> value without "
            "colour, status_reason low_coverage (warn) or insufficient_data (enforce, per HRSWI_COVERAGE_MODES)")
# ADR-021 / WP-6 HSE 3.6: a safety declaration that says near misses are not included does not cover KPI-043
REQUIRED_FEED_ATTRIBUTES = {"KPI-043": {"safety": {"includes_near_miss": True}}}
for _item in KPI_CATALOG:
    if _item["code"] in REQUIRED_FEED_ATTRIBUTES and _item["status"] == "active":
        _item["formula"]["parameters"]["required_feed_attributes"] = REQUIRED_FEED_ATTRIBUTES[_item["code"]]


FRAMEWORKS = [
    {"code": "ISO30414", "name": "ISO 30414 Human capital reporting", "version": "2025", "obligation": "voluntary",
     "note": "Guideline for internal/external human capital reporting. Version to be verified."},
    {"code": "GRI", "name": "GRI 401/403/404/405 workforce standards", "version": "2016/2018", "obligation": "voluntary",
     "note": "GRI 403 (2018). Check for newer GRI labour standards before reporting."},
    {"code": "ESRS-S1", "name": "ESRS S1 Own workforce", "version": "2023 (Set 1) - revision pending",
     "obligation": "conditional", "note": "Applies under CSRD scope only; EU simplification (Omnibus) may change datapoints."},
    {"code": "ESRS-S2", "name": "ESRS S2 Workers in the value chain", "version": "2023 (Set 1)", "obligation": "conditional",
     "note": "Relevant for subcontractor workforce when material."},
    {"code": "TSRS", "name": "TSRS 1 / TSRS 2 (KGK)", "version": "2024", "obligation": "conditional",
     "note": "Turkish IFRS S1/S2 adoption. TSRS 2 is climate; workforce metrics only where material under TSRS 1."},
    {"code": "ISO45001", "name": "ISO 45001 OH&S management systems", "version": "2018", "obligation": "voluntary",
     "note": "Management system standard - the software supports evidence, it does not certify."},
]

FRAMEWORK_REQUIREMENTS = {
    "ISO30414": [("Turnover", ["KPI-001", "KPI-020"]), ("Retention", ["KPI-002"]), ("Absenteeism", ["KPI-003"]),
                 ("Training hours", ["KPI-008"]), ("Time to fill", ["KPI-011"]), ("Workforce costs", ["KPI-019"]),
                 ("Lost time injuries", ["KPI-014A"]), ("Succession", ["KPI-025"])],
    "GRI": [("401-1 New hires and turnover", ["KPI-017", "KPI-001"]), ("403-9 Work-related injuries", ["KPI-014A", "KPI-014B"]),
            ("404-1 Training hours", ["KPI-008"]), ("405-1 Diversity", ["KPI-048", "KPI-049"])],
    "ESRS-S1": [("S1-6 Employee characteristics", ["KPI-016", "KPI-001"]), ("S1-13 Training", ["KPI-008"]),
                ("S1-14 Health and safety", ["KPI-014A"]), ("S1-9 Diversity", ["KPI-048"])],
    "ESRS-S2": [("Value chain worker data", ["KPI-050", "KPI-051"])],
    "TSRS": [("Material workforce information (TSRS 1)", ["KPI-016"])],
    "ISO45001": [("Performance evaluation evidence", ["KPI-014A", "KPI-014B", "KPI-043"])],
}
