"""Product feature flags (D-9, ADR-025) and KPI permission rules shared by the engine, API, reports and assistant.

subcontractors (HRSWI_FEATURE_SUBCONTRACTORS, default false): the product is firm-based. When off,
  - access is NOT expanded through contractor_relationships: a legal-entity role assignment covers exactly that entity
    (tenant-wide grants are unchanged);
  - contractor-relationship management, the contractor tree, the subcontractor report template and the KPIs of the
    'contractor' family (KPI-050, KPI-051) are hidden (404 / left out of listings);
  - period close / reopen is reserved for tenant-wide period.close holders, the main firm (D-16, ADR-028; see
    engine.can_close_periods);
  - nothing reads contractor_relationships (D-16): no firm-project check in the timesheet / HSE imports, no
    contractor-relationship creation or listing, and the stored main_contractor / subcontractor entity types are shown
    as 'employer' (public_entity_type) and cannot be assigned to new legal entities.
When on, every behaviour of ADR-005 / ADR-017 / D-1 / D-2 is unchanged.
"""
from app.core.config import get_settings
from app.security.catalog import HEALTH_DERIVED_KPIS

SUBCONTRACTOR_KPI_FAMILIES = frozenset({"contractor"})
SUBCONTRACTOR_REPORTS = frozenset({"subcontractor_workforce"})
CONTRACTOR_ENTITY_TYPES = frozenset({"main_contractor", "subcontractor"})


def subcontractors_enabled() -> bool:
    return bool(get_settings().feature_subcontractors)


def feature_flags() -> dict[str, bool]:
    """Exposed by GET /auth/me so the frontend hides disabled screens."""
    return {"subcontractors": subcontractors_enabled()}


def flags_signature() -> str:
    return ",".join(f"{k}={int(v)}" for k, v in sorted(feature_flags().items()))


def public_entity_type(entity_type: str) -> str:
    """D-16: with the subcontractor feature off no response says 'main contractor' / 'subcontractor' - every own firm
    is an 'employer' (group company); 'client' (project owner) is unchanged. Display only: stored values are kept."""
    if entity_type in CONTRACTOR_ENTITY_TYPES and not subcontractors_enabled():
        return "employer"
    return entity_type


def allowed_entity_types() -> tuple[str, ...]:
    """Entity types a new legal entity may get (data management)."""
    return (("client", "main_contractor", "subcontractor", "employer") if subcontractors_enabled()
            else ("client", "employer"))


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
