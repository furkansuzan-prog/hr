from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EffectiveDatedMixin, SourceMixin, TenantMixin, TimestampMixin

TERMINATION_CATEGORIES = (
    "voluntary",
    "employer_initiated",
    "contract_end",
    "project_end",
    "retirement",
    "death",
    "other",
    "unknown",  # reason not provided -> never assumed (Module E)
)
# ADR-023: 'unknown' = the source did not say (never silently 'permanent'); planned-end categories need a fixed-term type
CONTRACT_TYPES = ("permanent", "fixed_term", "project", "seasonal", "unknown")
PLANNED_END_CONTRACT_TYPES = ("fixed_term", "project", "seasonal")
PLANNED_END_CATEGORIES = ("contract_end", "project_end")
TERMINATION_REASON_SOURCES = ("sgk", "hris", "manual")


class Person(Base, TenantMixin, TimestampMixin, SourceMixin):
    """A natural person, independent of any employer. Data minimisation (KVKK):
    birth *year* only, national ID stored only as keyed HMAC, no health data here."""

    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("tenant_id", "person_code"),
        Index("ix_people_tenant_nid", "tenant_id", "national_id_hash"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    person_code: Mapped[str] = mapped_column(String(40))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    national_id_hash: Mapped[str | None] = mapped_column(String(64))
    birth_year: Mapped[int | None] = mapped_column(SmallInteger)
    gender: Mapped[str | None] = mapped_column(String(10))  # used only for aggregated diversity metrics
    nationality: Mapped[str | None] = mapped_column(String(2))


class PersonIdentifier(Base, TenantMixin, TimestampMixin):
    """Identity map: the same person may exist in several source systems with different ids."""

    __tablename__ = "person_identifiers"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    source_system: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(120))


class EmploymentRelationship(Base, TenantMixin, TimestampMixin, SourceMixin):
    """One employment spell of a person with one employer (legal entity).
    Employer changes create a NEW row - history is never overwritten (ADR-007)."""

    __tablename__ = "employment_relationships"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_employment_dates"),
        Index("ix_emp_tenant_entity_dates", "tenant_id", "legal_entity_id", "start_date", "end_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    employee_number: Mapped[str | None] = mapped_column(String(40))
    contract_type: Mapped[str] = mapped_column(String(20), default="permanent")  # CONTRACT_TYPES
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)  # last day employed (inclusive)
    termination_category: Mapped[str | None] = mapped_column(String(30))
    # SGK exit code (2 digits) - NEVER a health-implying / unmapped code (KVKK md.6, WP-5 L-8; ADR-023)
    termination_reason_code: Mapped[str | None] = mapped_column(String(40))
    termination_reason_source: Mapped[str | None] = mapped_column(String(10))  # TERMINATION_REASON_SOURCES
    planned_end_date: Mapped[date | None] = mapped_column(Date)  # contractual planned end (fixed-term / project)
    # WP-5 3.3: True = verified planned end, False = not a planned end, NULL = could not be evaluated (never excluded)
    planned_end_verified: Mapped[bool | None] = mapped_column(Boolean)


class TerminationCodeMap(Base, TenantMixin, TimestampMixin):
    """SGK exit code -> platform termination category (WP-5 3.1; ADR-023). Mapping is DATA, per tenant, verified line
    by line by the payroll / SGK practitioner: a row with verified_at NULL is never used for the category (the code
    falls back to the source category or 'unknown'). health_related / retain_code are honoured even when unverified
    (KVKK safe default): such a code is never stored on any table."""

    __tablename__ = "termination_code_map"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code_system", "code", "valid_from", name="uq_termination_code",
                         postgresql_nulls_not_distinct=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code_system: Mapped[str] = mapped_column(String(30), default="SGK_ISTEN_CIKIS")
    code: Mapped[str] = mapped_column(String(4))
    label_tr: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30))  # TERMINATION_CATEGORIES
    planned_end_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    health_related: Mapped[bool] = mapped_column(Boolean, default=False)  # also maternity-related codes (D-6)
    retain_code: Mapped[bool] = mapped_column(Boolean, default=True)  # False: category only (L-8 pending, e.g. 11)
    counter: Mapped[str | None] = mapped_column(String(40))  # entity x month counter (e.g. work_accident_death_exits)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    verified_by: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(300))


class TerminationCounter(Base, TenantMixin, TimestampMixin):
    """Legal entity x month counts of exits whose code is not stored (WP-5 review condition 3): e.g. code 11 (death from
    a work accident) -> work_accident_death_exits, for the HSE fatality consistency check. No person link."""

    __tablename__ = "termination_counters"
    __table_args__ = (UniqueConstraint("tenant_id", "legal_entity_id", "period_month", "counter",
                                       name="uq_termination_counter"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    period_month: Mapped[date] = mapped_column(Date)  # first day of the month of the exit (end_date)
    counter: Mapped[str] = mapped_column(String(40))
    count: Mapped[int] = mapped_column(default=0)


class Assignment(Base, TenantMixin, TimestampMixin, EffectiveDatedMixin):
    """Where/as-what an employment works during a period. Multiple concurrent assignments allowed."""

    __tablename__ = "assignments"
    __table_args__ = (Index("ix_assign_emp_dates", "employment_id", "valid_from", "valid_to"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employment_id: Mapped[int] = mapped_column(ForeignKey("employment_relationships.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    occupation_id: Mapped[int | None] = mapped_column(ForeignKey("occupations.id"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("project_locations.id"))
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"))
    fte: Mapped[float] = mapped_column(Numeric(4, 2), default=1)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)


class IdentityReviewItem(Base, TenantMixin, TimestampMixin):
    """Manual review queue for ambiguous identity matches during import (ADR-012)."""

    __tablename__ = "identity_review_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    candidate_person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    reason: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|merged|rejected
