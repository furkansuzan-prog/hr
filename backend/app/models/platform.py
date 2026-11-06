"""KPI catalog/engine, sustainability frameworks, risk & actions, data integration, security, audit, AI."""
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin

# ---------------------------------------------------------------- KPI (section 5)


class KpiDefinition(Base, TimestampMixin):
    """Global KPI catalog entry. Only status='active' AND validated=True may run in production (ADR-008)."""

    __tablename__ = "kpi_definitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    family: Mapped[str] = mapped_column(String(40))
    module: Mapped[str] = mapped_column(String(5))
    name: Mapped[dict] = mapped_column(JSON)  # {"tr":..,"en":..,"ru":..}
    description: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(15))  # higher_better|lower_better|neutral
    frequency: Mapped[str] = mapped_column(String(15))
    data_sources: Mapped[list] = mapped_column(JSON)
    data_owner_role: Mapped[str] = mapped_column(String(40))
    scope_levels: Mapped[list] = mapped_column(JSON)
    inclusion_rules: Mapped[str] = mapped_column(Text)
    exclusion_rules: Mapped[str] = mapped_column(Text)
    missing_data_behavior: Mapped[str] = mapped_column(Text)
    standard_refs: Mapped[list] = mapped_column(JSON)
    target_value: Mapped[float | None] = mapped_column(Numeric(14, 4))
    warning_threshold: Mapped[float | None] = mapped_column(Numeric(14, 4))
    critical_threshold: Mapped[float | None] = mapped_column(Numeric(14, 4))
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft|active|retired
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)  # requires cost.read


class KpiFormulaVersion(Base):
    """Immutable. A formula change = new row with version+1; old results keep pointing to their version."""

    __tablename__ = "kpi_formula_versions"
    __table_args__ = (UniqueConstraint("kpi_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kpi_id: Mapped[int] = mapped_column(ForeignKey("kpi_definitions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    calculator_key: Mapped[str] = mapped_column(String(60))
    formula_text: Mapped[str] = mapped_column(Text)
    numerator_def: Mapped[str] = mapped_column(Text)
    denominator_def: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KpiCalculationRun(Base, TenantMixin):
    __tablename__ = "kpi_calculation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    triggered_by: Mapped[int | None] = mapped_column(BigInteger)
    trigger: Mapped[str] = mapped_column(String(20), default="on_demand")  # on_demand|scheduled|recalc
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    scope: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(15), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class KpiResult(Base, TenantMixin):
    __tablename__ = "kpi_results"
    __table_args__ = (Index("ix_kpires_lookup", "tenant_id", "kpi_id", "period_start", "scope_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("kpi_calculation_runs.id", ondelete="CASCADE"), index=True)
    kpi_id: Mapped[int] = mapped_column(ForeignKey("kpi_definitions.id"))
    formula_version_id: Mapped[int] = mapped_column(ForeignKey("kpi_formula_versions.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    scope_key: Mapped[str] = mapped_column(String(200))
    value: Mapped[float | None] = mapped_column(Numeric(18, 6))
    numerator: Mapped[float | None] = mapped_column(Numeric(18, 6))
    denominator: Mapped[float | None] = mapped_column(Numeric(18, 6))
    status: Mapped[str] = mapped_column(String(20))  # ok|not_computable|insufficient_data|suppressed
    quality_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    sample_size: Mapped[int | None] = mapped_column(Integer)
    breakdown: Mapped[dict | None] = mapped_column(JSON)
    lineage: Mapped[dict | None] = mapped_column(JSON)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PeriodClose(Base, TenantMixin):
    __tablename__ = "period_closes"
    __table_args__ = (UniqueConstraint("tenant_id", "period_month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_month: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="closed")  # closed|reopened
    closed_by: Mapped[int | None] = mapped_column(BigInteger)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reopen_reason: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- Sustainability frameworks (section 12)


class SustainabilityFramework(Base, TimestampMixin):
    __tablename__ = "sustainability_frameworks"
    __table_args__ = (UniqueConstraint("code", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(60))
    obligation: Mapped[str] = mapped_column(String(20))  # mandatory|voluntary|conditional
    applicability_note: Mapped[str] = mapped_column(Text)
    version_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class FrameworkRequirement(Base):
    __tablename__ = "framework_requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    framework_id: Mapped[int] = mapped_column(ForeignKey("sustainability_frameworks.id"), index=True)
    ref_code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    kpi_codes: Mapped[list] = mapped_column(JSON, default=list)


class SustainabilityEvidence(Base, TenantMixin, TimestampMixin):
    __tablename__ = "sustainability_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("framework_requirements.id"))
    period_year: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(15), default="submitted")  # submitted|verified|rejected
    verified_by: Mapped[int | None] = mapped_column(BigInteger)


# ---------------------------------------------------------------- Risk & actions (Modules K, M)


class RiskItem(Base, TenantMixin, TimestampMixin):
    __tablename__ = "risk_register"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))  # low|medium|high|critical
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(15), default="open")
    source_kpi_code: Mapped[str | None] = mapped_column(String(20))
    closure_evidence: Mapped[str | None] = mapped_column(Text)


class ImprovementAction(Base, TenantMixin, TimestampMixin):
    __tablename__ = "improvement_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int | None] = mapped_column(ForeignKey("risk_register.id"))
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    problem: Mapped[str] = mapped_column(Text)
    kpi_code: Mapped[str | None] = mapped_column(String(20))
    baseline_value: Mapped[float | None] = mapped_column(Numeric(18, 4))
    target_value: Mapped[float | None] = mapped_column(Numeric(18, 4))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    priority: Mapped[str] = mapped_column(String(5), default="P1")
    start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    steps_taken: Mapped[str | None] = mapped_column(Text)
    review_date: Mapped[date | None] = mapped_column(Date)
    result_value: Mapped[float | None] = mapped_column(Numeric(18, 4))
    effectiveness: Mapped[str | None] = mapped_column(String(20))  # effective|partially|not_effective|pending
    status: Mapped[str] = mapped_column(String(15), default="planned")


# ---------------------------------------------------------------- Data integration (section 9, Agent 08)


class DataSource(Base, TenantMixin, TimestampMixin):
    __tablename__ = "data_sources"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # file|hris|erp|time_attendance|payroll|hse|lms|api
    status: Mapped[str] = mapped_column(String(15), default="available")  # available|unavailable|disabled
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ImportBatch(Base, TenantMixin, TimestampMixin):
    __tablename__ = "import_batches"
    __table_args__ = (
        # idempotency: a given file can be committed only once per tenant and entity type
        Index(
            "uq_import_committed_file",
            "tenant_id",
            "entity_type",
            "file_sha256",
            unique=True,
            postgresql_where=text("status = 'committed'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    mapping: Mapped[dict | None] = mapped_column(JSON)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    committed_rows: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    # B-20 / ADR-020: optional coverage declaration sent with the upload (normalised by app.services.coverage)
    coverage: Mapped[dict | None] = mapped_column(JSON)
    # ADR-021 / WP-6 HSE V17: NAMES of the columns dropped on upload as prohibited (identity, health, free text).
    # Values are never stored; a mapping that points at a dropped column is rejected (rename the column).
    dropped_columns: Mapped[list | None] = mapped_column(JSON)
    # ADR-022: upload options of the batch (timesheet: mode skip|replace, approved_source) and validation findings
    # that belong to the whole file (observed planned_on_leave_days profile). No row data.
    options: Mapped[dict | None] = mapped_column(JSON)


# B-20 / ADR-020 (docs/hr/data/WP-6 section 3.4): which feed is complete for which employer x month.
FEED_TYPES = ("employment", "leave", "timesheet", "training", "cost", "safety", "output", "downtime")
COVERAGE_COMPLETENESS = ("complete", "partial", "none")  # no row = 'undeclared' (behaves like 'none')
COVERAGE_BASIS = ("import_batch", "connector_run", "manual_declaration")
COVERAGE_DECLARE_ACTION = "coverage.declare"
# safety-feed declaration attributes (WP-6 HSE 3.6 / review condition 1); includes_near_miss = false makes KPI-043
# uncovered for that cell (formula parameter required_feed_attributes, ADR-021). includes_subcontractor_cases is
# inert since decision D-10 (HSE covers own employees only): still accepted and stored for older clients, read by
# nothing.
SAFETY_COVERAGE_ATTRIBUTES = ("includes_subcontractor_cases", "includes_near_miss")


class DataFeedCoverage(Base, TenantMixin, TimestampMixin):
    """One row = declaration for one feed type x employer x (optional) project x month. A missing row means
    'undeclared' and counts like 'none' in the KPI coverage gate - a missing feed is never a real zero (audit #19).
    A later declaration replaces the earlier one; the history is in audit_logs (action coverage.declare)."""

    __tablename__ = "data_feed_coverage"
    __table_args__ = (
        Index("uq_data_feed_coverage", "tenant_id", "feed_type", "legal_entity_id", "project_id", "period_month",
              unique=True, postgresql_nulls_not_distinct=True),
        Index("ix_data_feed_coverage_month", "tenant_id", "period_month"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    feed_type: Mapped[str] = mapped_column(String(20))  # FEED_TYPES (validated in code, like LEAVE_TYPES)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))  # NULL = all projects of the employer
    period_month: Mapped[date] = mapped_column(Date)  # first day of the month
    completeness: Mapped[str] = mapped_column(String(10))  # COVERAGE_COMPLETENESS
    coverage_ratio: Mapped[float | None] = mapped_column(Numeric(4, 3))  # only for 'partial' (NULL -> weight 0.5)
    confirmed_empty: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    planned_on_leave_days: Mapped[str | None] = mapped_column(String(10))  # timesheet only: included|excluded|unknown
    attributes: Mapped[dict | None] = mapped_column(JSON)  # safety: includes_subcontractor_cases, includes_near_miss
    basis: Mapped[str] = mapped_column(String(20))  # COVERAGE_BASIS
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    declared_by: Mapped[int | None] = mapped_column(BigInteger)
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text)


class ImportStagingRow(Base):
    """Raw source row kept separate from transformed data (section 9 data-engineering rules)."""

    __tablename__ = "import_staging_rows"
    __table_args__ = (UniqueConstraint("batch_id", "row_number"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON)
    mapped: Mapped[dict | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)
    has_error: Mapped[bool] = mapped_column(Boolean, default=False)
    committed: Mapped[bool] = mapped_column(Boolean, default=False)


class DataQualityIssue(Base, TenantMixin, TimestampMixin):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(30))
    rule_code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # error|warning
    message: Mapped[str] = mapped_column(Text)
    row_number: Mapped[int | None] = mapped_column(Integer)
    record_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), default="open")


# ---------------------------------------------------------------- Security & audit (sections 10, 11)


class User(Base, TenantMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(300))  # NULL when federated via OIDC
    locale: Mapped[str] = mapped_column(String(5), default="tr")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # brute-force protection (SECURITY_PRIVACY G6): consecutive failures and temporary lock
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # token revocation: JWT claim `tv` must equal this value; logout-all increments it
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class Permission(Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str] = mapped_column(String(300))


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"))  # NULL = system role template
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permissions.code"), primary_key=True)


class UserRoleAssignment(Base, TimestampMixin):
    """Role granted within a scope (ADR-005). scope_type: tenant | legal_entity."""

    __tablename__ = "user_role_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    scope_type: Mapped[str] = mapped_column(String(20), default="tenant")
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_tenant_at", "tenant_id", "at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(Integer)
    user_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(10), default="allowed")  # allowed|denied|error
    request_id: Mapped[str | None] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(45))
    details: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- AI (section 7)


class AiAnalysisRun(Base, TenantMixin):
    __tablename__ = "ai_analysis_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(40))
    tools_used: Mapped[list] = mapped_column(JSON, default=list)
    scope: Mapped[dict] = mapped_column(JSON)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ForecastResult(Base, TenantMixin):
    __tablename__ = "forecast_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(String(40))
    scope: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(40))
    horizon_months: Mapped[int] = mapped_column(Integer)
    training_points: Mapped[int] = mapped_column(Integer)
    backtest_mape: Mapped[float | None] = mapped_column(Numeric(8, 3))
    points: Mapped[list] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
