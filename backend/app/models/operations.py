"""Timekeeping, cost, productivity, training, safety, planning, surveys."""
from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EffectiveDatedMixin, SourceMixin, TenantMixin, TimestampMixin

# ---------------------------------------------------------------- Timekeeping (Module D)


class WorkCalendar(Base, TenantMixin, TimestampMixin):
    __tablename__ = "work_calendars"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    country: Mapped[str] = mapped_column(String(2))
    weekly_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=45)  # 4857 s. Kanun md.63 default
    daily_max_hours: Mapped[float] = mapped_column(Numeric(4, 2), default=11)
    annual_overtime_cap_hours: Mapped[float] = mapped_column(Numeric(6, 2), default=270)


# ---------------------------------------------------------------- Work patterns (WP-4 3.1, ADR-024)
# Resolution order on a date: employment -> project of the employment's primary assignment -> legal entity -> unknown.
# The tenant's work_calendars row (45 h) is an ASSUMPTION, never a known pattern (only the fallback divisor 9).
WORK_PATTERN_LEVELS = ("employment", "project", "legal_entity")
WORK_PATTERN_TYPES = ("weekly",)  # v1: weekly patterns only; rotations (10 weeks on / 2 off) count as unknown (S-4)
# How leave_records.days was produced (WP-4 3.1(f)); quality score and audit trail
LEAVE_DAY_BASES = ("source_days", "pattern_counted", "hours_divided", "default_9h")
DEFAULT_HOURS_PER_DAY = 9  # fallback divisor when no pattern is known (45 h / 5 days; WP-4 3.2 step 5)


class WorkPattern(Base, TenantMixin, TimestampMixin, SourceMixin):
    """Weekly work pattern catalogue (WP-4 3.1(a)): working ISO weekdays (1 = Mon .. 7 = Sun) and daily hours.
    Definitions are immutable once referenced: a changed pattern gets a new code (re-import rejects a change)."""

    __tablename__ = "work_patterns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_work_pattern_code"),
        CheckConstraint("days_per_week BETWEEN 1 AND 7", name="ck_work_pattern_days"),
        CheckConstraint("daily_hours > 0 AND daily_hours <= 24", name="ck_work_pattern_daily_hours"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    days_per_week: Mapped[int] = mapped_column(Integer)
    working_weekdays: Mapped[list] = mapped_column(JSON)  # sorted ISO weekdays, len == days_per_week
    daily_hours: Mapped[float] = mapped_column(Numeric(4, 2))
    weekly_hours: Mapped[float] = mapped_column(Numeric(5, 2))
    country: Mapped[str] = mapped_column(String(2), default="TR")  # public holiday calendar
    pattern_type: Mapped[str] = mapped_column(String(10), default="weekly")  # WORK_PATTERN_TYPES


class WorkPatternAssignment(Base, TenantMixin, TimestampMixin, EffectiveDatedMixin, SourceMixin):
    """Effective-dated binding of a pattern to an employment, a project or a legal entity (WP-4 3.1(b)); exactly one
    target column matches `level`. Overlapping intervals on the same target are rejected in code."""

    __tablename__ = "work_pattern_assignments"
    __table_args__ = (
        CheckConstraint(
            "(level = 'employment' AND employment_id IS NOT NULL AND project_id IS NULL AND legal_entity_id IS NULL) OR "
            "(level = 'project' AND project_id IS NOT NULL AND employment_id IS NULL AND legal_entity_id IS NULL) OR "
            "(level = 'legal_entity' AND legal_entity_id IS NOT NULL AND employment_id IS NULL AND project_id IS NULL)",
            name="ck_wpa_target"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_wpa_dates"),
        Index("ix_wpa_tenant_level", "tenant_id", "level"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    work_pattern_id: Mapped[int] = mapped_column(ForeignKey("work_patterns.id"), index=True)
    level: Mapped[str] = mapped_column(String(20))  # WORK_PATTERN_LEVELS
    employment_id: Mapped[int | None] = mapped_column(ForeignKey("employment_relationships.id", ondelete="CASCADE"),
                                                      index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"))


class PublicHoliday(Base, TimestampMixin):
    """Country public-holiday calendar (WP-4 3.1(c)), shared by all tenants: fraction 1.0 = full day, 0.5 = eve
    (arife) half day (L-2 safe default). A country-year without any row = calendar missing (holiday_calendar_missing).
    Source must be the official calendar (TR: Law 2429 - current text to be confirmed)."""

    __tablename__ = "public_holidays"
    __table_args__ = (
        UniqueConstraint("country", "holiday_date", name="uq_public_holiday"),
        CheckConstraint("fraction > 0 AND fraction <= 1", name="ck_public_holiday_fraction"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(2))
    holiday_date: Mapped[date] = mapped_column(Date)
    fraction: Mapped[float] = mapped_column(Numeric(3, 2), default=1)
    name: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(120), default="manual")


class Shift(Base, TenantMixin, TimestampMixin):
    __tablename__ = "shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    crosses_midnight: Mapped[bool] = mapped_column(Boolean, default=False)
    planned_hours: Mapped[float] = mapped_column(Numeric(4, 2))


class AttendanceEvent(Base, TenantMixin):
    """Raw badge/turnstile event. Presence evidence only - NOT productive time, NOT pay entitlement."""

    __tablename__ = "attendance_events"
    __table_args__ = (UniqueConstraint("tenant_id", "dedup_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[str] = mapped_column(String(3))  # in|out
    device_id: Mapped[str | None] = mapped_column(String(60))
    dedup_key: Mapped[str] = mapped_column(String(64))  # hash(person, device, ts truncated to minute, direction)
    source_system: Mapped[str] = mapped_column(String(50), default="turnstile")


class Timesheet(Base, TenantMixin, TimestampMixin, SourceMixin):
    """Approved daily time record per employment and project.
    worked_hours = paid working time; productive_hours = hours booked on work packages (may be NULL = unknown)."""

    __tablename__ = "timesheets"
    __table_args__ = (
        # natural key of the timesheet import (WP-6 data 3.2.1); NULLS NOT DISTINCT (ADR-022): a row without a project
        # is still one row per employment x day, so a re-import can never duplicate it
        UniqueConstraint("employment_id", "work_date", "project_id", name="uq_timesheet_day",
                         postgresql_nulls_not_distinct=True),
        CheckConstraint("worked_hours >= 0 AND overtime_hours >= 0 AND planned_hours >= 0", name="ck_ts_nonneg"),
        CheckConstraint("period_days >= 1", name="ck_ts_period_days"),
        Index("ix_ts_tenant_date", "tenant_id", "work_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employment_id: Mapped[int] = mapped_column(ForeignKey("employment_relationships.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    work_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    planned_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    worked_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    overtime_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    night_hours: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    productive_hours: Mapped[float | None] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(15), default="approved")
    # ADR-022 (WP-6 data 3.2.1): day type as given by the source (NULL = not supplied; TIMESHEET_DAY_TYPES) and the
    # number of calendar days the row covers (1 = daily row; > 1 = period total starting at work_date)
    day_type: Mapped[str | None] = mapped_column(String(20))
    period_days: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))


# ADR-022: timesheet day types (validated in code, no DB CHECK - LEAVE_TYPES pattern). A 'leave' day never creates a
# leave record (D-6 / WP-6 data 3.2.4); leave comes only from the leave feed.
TIMESHEET_DAY_TYPES = ("work", "weekly_rest", "public_holiday", "leave", "unknown")
TIMESHEET_STATUSES = ("approved", "draft")
# audit_logs.action of an in-place timesheet correction by a re-import with mode = replace (ADR-022)
TIMESHEET_CORRECT_ACTION = "timesheet.correct"


class TimesheetMonthly(Base):
    """Monthly pre-aggregate of APPROVED timesheet rows per (tenant, month, employment, project) - B-21.
    Read only for windows made of whole calendar months; months listed in timesheet_summary_dirty are never read
    from here (calculators fall back to the raw table), so a stale summary can never produce a wrong KPI."""

    __tablename__ = "timesheet_monthly"
    __table_args__ = (
        Index("ix_tsm_tenant_month_cover", "tenant_id", "month",
              postgresql_include=["employment_id", "project_id", "n", "planned", "worked", "overtime", "productive",
                                  "n_productive", "planned_productive", "night"]),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    month: Mapped[date] = mapped_column(Date)
    employment_id: Mapped[int] = mapped_column(ForeignKey("employment_relationships.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(Integer)
    n: Mapped[int] = mapped_column(Integer)
    planned: Mapped[float] = mapped_column(Numeric(14, 2))
    worked: Mapped[float] = mapped_column(Numeric(14, 2))
    overtime: Mapped[float] = mapped_column(Numeric(14, 2))
    productive: Mapped[float] = mapped_column(Numeric(14, 2))
    n_productive: Mapped[int] = mapped_column(Integer)
    planned_productive: Mapped[float] = mapped_column(Numeric(14, 2))
    night: Mapped[float] = mapped_column(Numeric(14, 2))


class TimesheetSummaryDirty(Base):
    """Earliest month per tenant whose summary no longer matches timesheets. Written by DB triggers on timesheets
    (insert/update/delete, incl. COPY), cleared by app.analytics.summary.refresh()."""

    __tablename__ = "timesheet_summary_dirty"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    from_month: Mapped[date] = mapped_column(Date)


# Leave type value set - a shared contract (ADR-019): import mapping (timekeeping.classify_leave), KPI formula
# parameters (engine.PARAM_RULES), calculators and reports. No DB CHECK: String(20) column, validated in code.
LEAVE_TYPES = frozenset({"annual", "sick", "work_injury", "unpaid", "unexcused", "public_holiday", "parental", "other"})
# "Certified rest" family (WP-1 3.4): the same doctor's report can be re-issued as either type (SGK review), so a
# re-import that changes between them is a reclassification of the SAME record; overlaps inside it are errors.
SICK_NOTE_FAMILY = frozenset({"sick", "work_injury"})
# Long-absence types of KPI-056 (WP-1 review E / WP-4 condition 3: one constant, later versions inherit it).
LONG_ABSENCE_TYPES = ("sick", "parental", "unpaid", "work_injury")
# audit_logs.action of an in-place leave reclassification (ADR-019); the group-level direction counters and the
# closed-period note are read from these rows (details: from, to, direction, legal_entity_id, start/end date, batch).
LEAVE_RECLASSIFY_ACTION = "leave.reclassify"


class LeaveRecord(Base, TenantMixin, TimestampMixin, SourceMixin):
    """Absence/leave. counts_as_absence follows the tenant's absenteeism definition (KPI-003).
    source_record_id: the source's leave/report number when the feed has one (re-import identity key, ADR-019),
    otherwise the employee's external id (legacy rows)."""

    __tablename__ = "leave_records"
    __table_args__ = (Index("ix_leave_tenant_dates", "tenant_id", "start_date", "end_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employment_id: Mapped[int] = mapped_column(ForeignKey("employment_relationships.id"), index=True)
    leave_type: Mapped[str] = mapped_column(String(20))  # one of LEAVE_TYPES
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[float] = mapped_column(Numeric(6, 2))
    # WP-4 3.1(f) / ADR-024: leave WORKING days (half days allowed), how they were produced (LEAVE_DAY_BASES) and the
    # pattern used for the conversion (resolved at the leave start). NULL on rows imported before ADR-024.
    days: Mapped[float | None] = mapped_column(Numeric(6, 2))
    day_basis: Mapped[str | None] = mapped_column(String(20))
    work_pattern_id: Mapped[int | None] = mapped_column(ForeignKey("work_patterns.id"))


# ---------------------------------------------------------------- Cost (Module F)


# ADR-027 / WP-6 data 3.7.1: cost item codes (validated in code, no DB CHECK - LEAVE_TYPES pattern). unused_leave_pay
# (4857 md. 59 leave pay on termination) and severance (kidem / ihbar) are the two items the spec adds (E.3 #9 check).
COST_TYPES = ("gross_wage", "employer_ss", "overtime", "meal", "transport", "accommodation", "benefit",
              "unused_leave_pay", "severance")
# audit_logs.action of an in-place cost correction by a re-import with mode = replace (ADR-027). The audit row names
# the changed fields only - never an amount (audit.read does not imply cost.read).
COST_CORRECT_ACTION = "cost.correct"


class PayrollCost(Base, TenantMixin, TimestampMixin, SourceMixin):
    """One employee x month x cost item (x project). Imported rows (ADR-027): natural key (employment, period_month,
    cost_type, project) for normal rows; is_correction rows are separate (negative amounts allowed) and never replace
    a key row."""

    __tablename__ = "payroll_costs"
    __table_args__ = (Index("ix_cost_tenant_period", "tenant_id", "period_month"),
                      Index("ix_cost_employment_period", "employment_id", "period_month"))
    id: Mapped[int] = mapped_column(primary_key=True)
    employment_id: Mapped[int] = mapped_column(ForeignKey("employment_relationships.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"))
    period_month: Mapped[date] = mapped_column(Date)  # first day of month
    cost_type: Mapped[str] = mapped_column(String(30))  # gross_wage|employer_ss|overtime|meal|transport|accommodation|benefit
    amount: Mapped[float] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class FxRate(Base, TimestampMixin):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("rate_date", "from_ccy", "to_ccy", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date)
    from_ccy: Mapped[str] = mapped_column(String(3))
    to_ccy: Mapped[str] = mapped_column(String(3))
    rate: Mapped[float] = mapped_column(Numeric(18, 8))
    source: Mapped[str] = mapped_column(String(40), default="TCMB")


# ---------------------------------------------------------------- Productivity (Module C)


class WorkPackage(Base, TenantMixin, TimestampMixin):
    __tablename__ = "work_packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20))
    occupation_group: Mapped[str] = mapped_column(String(30))
    planned_quantity: Mapped[float | None] = mapped_column(Numeric(18, 3))


class ProductionStandard(Base, TenantMixin, TimestampMixin, EffectiveDatedMixin):
    """Standard labour hours per accepted output unit. KPI-005 is only computed where one exists."""

    __tablename__ = "production_standards"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_package_id: Mapped[int] = mapped_column(ForeignKey("work_packages.id"), index=True)
    std_hours_per_unit: Mapped[float] = mapped_column(Numeric(10, 4))
    source: Mapped[str] = mapped_column(String(100), default="company standard")


class WorkOutput(Base, TenantMixin, TimestampMixin, SourceMixin):
    __tablename__ = "work_outputs"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_package_id: Mapped[int] = mapped_column(ForeignKey("work_packages.id"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    output_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[float] = mapped_column(Numeric(18, 3))
    accepted_quantity: Mapped[float | None] = mapped_column(Numeric(18, 3))  # passed quality inspection
    rework_quantity: Mapped[float] = mapped_column(Numeric(18, 3), default=0)
    labor_hours: Mapped[float] = mapped_column(Numeric(10, 2))
    rework_hours: Mapped[float] = mapped_column(Numeric(10, 2), default=0)


class DowntimeEvent(Base, TenantMixin, TimestampMixin):
    __tablename__ = "downtime_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"))
    event_date: Mapped[date] = mapped_column(Date)
    cause: Mapped[str] = mapped_column(String(20))  # equipment|material|weather|permit|design|other
    lost_hours: Mapped[float] = mapped_column(Numeric(10, 2))
    affected_headcount: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- Competency & training (Module H)


class Competency(Base, TenantMixin, TimestampMixin):
    __tablename__ = "competencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30))  # technical|hse|legal_certificate|soft
    validity_months: Mapped[int | None] = mapped_column(Integer)


class OccupationCompetency(Base, TenantMixin):
    __tablename__ = "occupation_competencies"
    __table_args__ = (UniqueConstraint("occupation_id", "competency_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    occupation_id: Mapped[int] = mapped_column(ForeignKey("occupations.id"))
    competency_id: Mapped[int] = mapped_column(ForeignKey("competencies.id"))
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)


class EmployeeCompetency(Base, TenantMixin, TimestampMixin, EffectiveDatedMixin):
    __tablename__ = "employee_competencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    competency_id: Mapped[int] = mapped_column(ForeignKey("competencies.id"))
    level: Mapped[int] = mapped_column(Integer, default=1)


# ADR-027 / WP-6 data 3.3.1 (validated in code, no DB CHECK). legal_basis values are informative until Agent 18 /
# Agent 20 confirm durations (L-14); is_statutory_hse = category 'hse' AND legal_basis 'ISG_%' (derived, not stored).
TRAINING_CATEGORIES = ("hse", "technical", "compliance", "soft")
TRAINING_LEGAL_BASES = ("ISG_TEMEL", "ISG_ISE_BASLAMA", "ISG_YENILEME", "ISG_GOREV_DEGISIKLIGI", "ISG_KAZA_SONRASI",
                        "ISG_UZUN_AYRILIK", "MYK_BELGE", "OPERATOR_BELGE", "YUKSEKTE_CALISMA", "DIGER_YASAL")
TRAINING_DELIVERY = ("classroom", "online", "on_the_job")
TRAINING_CORRECT_ACTION = "training.correct"


class TrainingRecord(Base, TenantMixin, TimestampMixin, SourceMixin):
    """A completed training of one employment. Imported rows (ADR-027): natural key (employment, course_code,
    completed_on); only status 'completed' is stored."""

    __tablename__ = "training_records"
    __table_args__ = (Index("ix_training_tenant_date", "tenant_id", "completed_on"),
                      Index("ix_training_employment_course", "employment_id", "course_code", "completed_on"))
    id: Mapped[int] = mapped_column(primary_key=True)
    employment_id: Mapped[int] = mapped_column(ForeignKey("employment_relationships.id"), index=True)
    course_code: Mapped[str] = mapped_column(String(40))
    course_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(20))  # hse|technical|compliance|soft
    hours: Mapped[float] = mapped_column(Numeric(6, 2))
    completed_on: Mapped[date] = mapped_column(Date)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    legal_basis: Mapped[str | None] = mapped_column(String(30))
    start_date: Mapped[date | None] = mapped_column(Date)
    delivery: Mapped[str | None] = mapped_column(String(20))


# ---------------------------------------------------------------- HSE (Module G)


# Platform classification of a safety case (WP-6 HSE 3.2). Severity order = "most severe outcome" rule (3.1) and the
# direction of a reclassification (reclassified_up / reclassified_down, shared with the leave import - WP-1 cond. 5).
SAFETY_EVENT_TYPES = ("fatality", "lost_time_injury", "restricted_work", "medical_treatment", "first_aid", "near_miss")
SAFETY_SEVERITY = {"near_miss": 0, "first_aid": 1, "medical_treatment": 2, "restricted_work": 3, "lost_time_injury": 4,
                   "fatality": 5}
# Who was affected (WP-6 HSE 3.3; HR Director term decision, SPRINT1_REVIEW section 3). NULL = legacy / near miss.
WORKER_CATEGORIES = ("own_employee", "subcontractor_worker", "temporary_agency_worker", "third_party")
SAFETY_RECLASSIFY_ACTION = "safety.reclassify"


class SafetyEvent(Base, TenantMixin, TimestampMixin, SourceMixin):
    """Case-level HSE record (ADR-021: one row = the outcome of ONE affected person, without identity) WITHOUT
    diagnosis, injury detail, free text or person identity - special-category data stays in the HSE system of record.
    Imported rows carry source_record_id = "<event_ref>#<case_no>" (re-import key, unique per source system)."""

    __tablename__ = "safety_events"
    __table_args__ = (
        Index("uq_safety_case_source", "tenant_id", "source_system", "source_record_id", unique=True,
              postgresql_where=text("source_record_id IS NOT NULL")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    event_date: Mapped[date] = mapped_column(Date)
    event_type: Mapped[str] = mapped_column(String(30))  # SAFETY_EVENT_TYPES
    # calendar days away, event day excluded (WP-6 HSE 3.5); NULL = not yet known (open case) - empty is not 0
    lost_days: Mapped[int | None] = mapped_column(Integer)
    # ADR-021 / WP-6 HSE 3.3 (all optional: legacy rows and the demo seed have none of them)
    event_ref: Mapped[str | None] = mapped_column(String(100))
    case_no: Mapped[int | None] = mapped_column(Integer)
    worker_category: Mapped[str | None] = mapped_column(String(30))  # WORKER_CATEGORIES
    reported_date: Mapped[date | None] = mapped_column(Date)
    restricted_days: Mapped[int | None] = mapped_column(Integer)
    case_status: Mapped[str | None] = mapped_column(String(10))  # open | closed
    sgk_notification: Mapped[str | None] = mapped_column(String(20))  # notified | pending | not_required
    sgk_notification_date: Mapped[date | None] = mapped_column(Date)
    high_consequence: Mapped[bool | None] = mapped_column(Boolean)
    # WP-2 R2.5 / example L: a fatality later reclassified keeps a marker so the alert band still says so
    reclassified_from_fatality: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


# ---------------------------------------------------------------- Planning (Module I)


class WorkforcePlan(Base, TenantMixin, TimestampMixin):
    __tablename__ = "workforce_plans"
    __table_args__ = (Index("ix_wfp_tenant_period", "tenant_id", "period_month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    occupation_id: Mapped[int | None] = mapped_column(ForeignKey("occupations.id"))
    period_month: Mapped[date] = mapped_column(Date)
    planned_headcount: Mapped[int] = mapped_column(Integer)
    planned_hours: Mapped[float | None] = mapped_column(Numeric(12, 2))
    plan_version: Mapped[int] = mapped_column(Integer, default=1)


class Requisition(Base, TenantMixin, TimestampMixin):
    """Approved position request -> accepted hire. Basis of KPI-011 time-to-hire."""

    __tablename__ = "requisitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    occupation_id: Mapped[int | None] = mapped_column(ForeignKey("occupations.id"))
    approved_on: Mapped[date] = mapped_column(Date)
    offer_accepted_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(15), default="open")


# ---------------------------------------------------------------- Surveys (Module L) - aggregates only


class SurveyCampaign(Base, TenantMixin, TimestampMixin):
    __tablename__ = "survey_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    min_group_size: Mapped[int] = mapped_column(Integer, default=10)


class SurveyAggregate(Base, TenantMixin):
    """Only aggregated scores are stored; groups below min_group_size are rejected at write time."""

    __tablename__ = "survey_aggregates"
    __table_args__ = (CheckConstraint("respondents >= 10", name="ck_survey_min_group"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("survey_campaigns.id"), index=True)
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"))
    dimension: Mapped[str] = mapped_column(String(40))  # engagement|workload|management_comm|conditions|...
    respondents: Mapped[int] = mapped_column(Integer)
    mean_score: Mapped[float] = mapped_column(Numeric(5, 2))
    favorable_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    breakdown: Mapped[dict | None] = mapped_column(JSON)
