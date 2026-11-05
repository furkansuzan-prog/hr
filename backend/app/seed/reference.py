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


_META_FIELDS = ("family", "module", "name", "description", "purpose", "unit", "direction", "frequency", "data_sources",
                "data_owner_role", "scope_levels", "inclusion_rules", "exclusion_rules", "missing_data_behavior",
                "standard_refs", "target_value", "warning_threshold", "critical_threshold", "status", "validated",
                "sensitive")


def sync_kpi_catalog(db: Session, *, apply: bool = False) -> list[dict]:
    """Bring an EXISTING database in line with the code catalog (seed only inserts missing definitions).
    Formula versions are immutable (DB trigger), so a changed calculator/parameters becomes a NEW version.
    Returns the list of changes; writes only when apply=True."""
    changes: list[dict] = []
    for item in KPI_CATALOG:
        d = db.execute(select(KpiDefinition).where(KpiDefinition.code == item["code"])).scalar()
        if d is None:
            changes.append({"code": item["code"], "change": "missing (run seed)"})
            continue
        meta = {k: item[k] for k in _META_FIELDS if getattr(d, k) != item[k]
                and not (isinstance(item[k], int | float) and getattr(d, k) is not None and float(getattr(d, k)) == item[k])}
        cur = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == d.id,
                                                         KpiFormulaVersion.is_current.is_(True))).scalar()
        f = item["formula"]
        formula_changed = cur is None or cur.calculator_key != f["calculator_key"] or (cur.parameters or {}) != f["parameters"]
        if not meta and not formula_changed:
            continue
        changes.append({"code": d.code, "metadata": sorted(meta), "new_formula_version": formula_changed})
        if not apply:
            continue
        for k, v in meta.items():
            setattr(d, k, v)
        if formula_changed:
            nxt = (cur.version + 1) if cur else 1
            if cur:
                cur.is_current = False
            db.add(KpiFormulaVersion(kpi_id=d.id, version=nxt, is_current=True, change_note="catalog sync", **f))
    if apply:
        db.commit()
    return changes


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


if __name__ == "__main__":  # python -m app.seed.reference [--apply]
    import json
    import sys

    from app.core.db import SessionLocal

    with SessionLocal() as _db:
        if "--apply" in sys.argv:
            seed_reference(_db)  # insert definitions/frameworks that do not exist yet (new KPIs)
        print(json.dumps(sync_kpi_catalog(_db, apply="--apply" in sys.argv), indent=1, default=str))
