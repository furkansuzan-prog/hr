"""Idempotent seeding of global reference data: permissions, role templates, KPI catalog, frameworks."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.catalog import FRAMEWORK_REQUIREMENTS, FRAMEWORKS, KPI_CATALOG
from app.models import (
    FrameworkRequirement,
    KpiDefinition,
    KpiFormulaVersion,
    Permission,
    Role,
    RolePermission,
    SustainabilityFramework,
)
from app.security.catalog import PERMISSIONS, ROLE_TEMPLATES


def seed_permissions_and_roles(db: Session, tenant_id: int) -> dict[str, int]:
    for code, desc in PERMISSIONS.items():
        if db.get(Permission, code) is None:
            db.add(Permission(code=code, description=desc))
    db.flush()
    ids = {}
    for code, (name, perms) in ROLE_TEMPLATES.items():
        role = db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.code == code)).scalar()
        if role is None:
            role = Role(tenant_id=tenant_id, code=code, name=name, is_system=True)
            db.add(role)
            db.flush()
            for p in sorted(perms):
                db.add(RolePermission(role_id=role.id, permission_code=p))
        ids[code] = role.id
    db.flush()
    return ids


def seed_kpi_catalog(db: Session) -> None:
    for item in KPI_CATALOG:
        f = item["formula"]
        fields = {k: v for k, v in item.items() if k != "formula"}
        d = db.execute(select(KpiDefinition).where(KpiDefinition.code == item["code"])).scalar()
        if d is None:
            d = KpiDefinition(**fields)
            db.add(d)
            db.flush()
            db.add(KpiFormulaVersion(kpi_id=d.id, version=1, is_current=True, change_note="initial catalog", **f))
    db.flush()


def seed_frameworks(db: Session) -> None:
    for fw in FRAMEWORKS:
        row = db.execute(select(SustainabilityFramework).where(SustainabilityFramework.code == fw["code"])).scalar()
        if row is None:
            row = SustainabilityFramework(code=fw["code"], name=fw["name"], version=fw["version"],
                                          obligation=fw["obligation"], applicability_note=fw["note"],
                                          version_verified=False)
            db.add(row)
            db.flush()
            for title, kpis in FRAMEWORK_REQUIREMENTS.get(fw["code"], []):
                db.add(FrameworkRequirement(framework_id=row.id, ref_code=title.split(" ")[0], title=title, kpi_codes=kpis))
    db.flush()


def seed_reference(db: Session) -> None:
    for code in PERMISSIONS:
        if db.get(Permission, code) is None:
            db.add(Permission(code=code, description=PERMISSIONS[code]))
    seed_kpi_catalog(db)
    seed_frameworks(db)
    db.commit()
