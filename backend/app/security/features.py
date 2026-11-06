"""Product feature flags (D-9, ADR-025) and KPI permission rules shared by the engine, API, reports and assistant.

subcontractors (HRSWI_FEATURE_SUBCONTRACTORS, default false): the product is firm-based. When off,
  - access is NOT expanded through contractor_relationships: a legal-entity role assignment covers exactly that entity
    (tenant-wide grants are unchanged);
  - contractor-relationship management, the contractor tree, the subcontractor report template and the KPIs of the
    'contractor' family (KPI-050, KPI-051) are hidden (404 / left out of listings);
  - period-close authority does not depend on the main_contractor entity type (see engine.can_close_periods).
When on, every behaviour of ADR-005 / ADR-017 / D-1 / D-2 is unchanged.
"""
from app.core.config import get_settings
from app.security.catalog import HEALTH_DERIVED_KPIS

SUBCONTRACTOR_KPI_FAMILIES = frozenset({"contractor"})
SUBCONTRACTOR_REPORTS = frozenset({"subcontractor_workforce"})


def subcontractors_enabled() -> bool:
    return bool(get_settings().feature_subcontractors)


def feature_flags() -> dict[str, bool]:
    """Exposed by GET /auth/me so the frontend hides disabled screens."""
    return {"subcontractors": subcontractors_enabled()}


def flags_signature() -> str:
    return ",".join(f"{k}={int(v)}" for k, v in sorted(feature_flags().items()))


def kpi_hidden(kpi) -> bool:
    """A KPI that belongs to a disabled feature: not listed, not evaluable (404)."""
    return not subcontractors_enabled() and kpi.family in SUBCONTRACTOR_KPI_FAMILIES


def report_hidden(code: str) -> bool:
    return not subcontractors_enabled() and code in SUBCONTRACTOR_REPORTS


def is_health_kpi(kpi) -> bool:
    return kpi.code in HEALTH_DERIVED_KPIS


def kpi_permissions(kpi) -> list[str]:
    """Every permission a reader needs for the KPI; the effective scope is the intersection (AccessContext.scope_for)."""
    return (["kpi.read"] + (["cost.read"] if kpi.sensitive else [])
            + (["health.read"] if is_health_kpi(kpi) else []))


def missing_permissions(ctx, kpi) -> list[str]:
    return [p for p in kpi_permissions(kpi) if not ctx.has(p)]
