from datetime import date

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
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
    contract_type: Mapped[str] = mapped_column(String(20), default="permanent")  # permanent|fixed_term|project|seasonal
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)  # last day employed (inclusive)
    termination_category: Mapped[str | None] = mapped_column(String(30))
    termination_reason_code: Mapped[str | None] = mapped_column(String(40))


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
