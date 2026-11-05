from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SourceMixin:
    """Provenance + optimistic versioning for important master records (ADR-007)."""

    source_system: Mapped[str] = mapped_column(String(50), default="manual")
    source_record_id: Mapped[str | None] = mapped_column(String(120))
    record_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)


class TenantMixin:
    @declared_attr
    def tenant_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), index=True)


class EffectiveDatedMixin:
    """Half-open validity interval [valid_from, valid_to). valid_to NULL = open-ended."""

    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
