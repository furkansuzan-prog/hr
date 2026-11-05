from datetime import date

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EffectiveDatedMixin, SourceMixin, TenantMixin, TimestampMixin


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    default_currency: Mapped[str] = mapped_column(String(3), default="TRY")
    country: Mapped[str] = mapped_column(String(2), default="TR")
    default_locale: Mapped[str] = mapped_column(String(5), default="tr")
    # incremented on every data change affecting analytics; part of the KPI cache key (exact invalidation)
    data_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class LegalEntity(Base, TenantMixin, TimestampMixin, SourceMixin):
    """Legal person / employer. A main contractor and a subcontractor are both legal entities;
    their relation is modelled separately and is time-bound (ContractorRelationship)."""

    __tablename__ = "legal_entities"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    entity_type: Mapped[str] = mapped_column(String(30))  # client | main_contractor | subcontractor | employer
    country: Mapped[str] = mapped_column(String(2), default="TR")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(Base, TenantMixin, TimestampMixin, SourceMixin):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    owner_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    phase: Mapped[str | None] = mapped_column(String(40))  # mobilisation | construction | commissioning | closeout
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(2), default="TR")


class ContractorRelationship(Base, TenantMixin, TimestampMixin, EffectiveDatedMixin):
    """parent (main/upper contractor) -> child (subcontractor), optionally per project.
    data_sharing_granted controls whether parent users may see child's workforce data (ADR-005)."""

    __tablename__ = "contractor_relationships"
    __table_args__ = (CheckConstraint("parent_entity_id <> child_entity_id", name="ck_contractor_not_self"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    child_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    tier: Mapped[int] = mapped_column(Integer, default=1)
    scope_of_work: Mapped[str | None] = mapped_column(String(200))
    data_sharing_granted: Mapped[bool] = mapped_column(Boolean, default=False)


class ProjectLocation(Base, TenantMixin, TimestampMixin):
    __tablename__ = "project_locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(2), default="TR")


class Department(Base, TenantMixin, TimestampMixin, EffectiveDatedMixin):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))


class Occupation(Base, TenantMixin, TimestampMixin):
    """Occupation group drives which productivity method applies (Agent 04)."""

    __tablename__ = "occupations"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    occupation_group: Mapped[str] = mapped_column(String(30))  # engineering|construction|maintenance|office|hr|service
    isco_code: Mapped[str | None] = mapped_column(String(10))
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)


class Position(Base, TenantMixin, TimestampMixin):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    occupation_id: Mapped[int] = mapped_column(ForeignKey("occupations.id"))
    title: Mapped[str] = mapped_column(String(200))
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    budgeted_headcount: Mapped[int] = mapped_column(Integer, default=1)


class CostCenter(Base, TenantMixin, TimestampMixin):
    __tablename__ = "cost_centers"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    annual_budget: Mapped[float | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="TRY")
