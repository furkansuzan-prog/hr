"""Catalog sync brings existing databases in line with the code catalog without editing immutable formula rows."""
from sqlalchemy import select

from app.models import KpiDefinition, KpiFormulaVersion
from app.seed.reference import sync_kpi_catalog


def test_fresh_database_matches_catalog(db):
    assert sync_kpi_catalog(db) == []


def test_sync_repairs_drift_with_new_version(db):
    d = db.execute(select(KpiDefinition).where(KpiDefinition.code == "KPI-032")).scalar_one()
    cur = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == d.id,
                                                     KpiFormulaVersion.is_current.is_(True))).scalar_one()
    # simulate a database seeded before KPI-032 was activated: draft + an older current formula row
    d.status, d.validated = "draft", False
    cur.is_current = False
    db.add(KpiFormulaVersion(kpi_id=d.id, version=cur.version + 1, calculator_key="unimplemented", formula_text="x",
                             numerator_def="x", denominator_def="x", parameters={}, is_current=True))
    db.commit()
    changes = sync_kpi_catalog(db)
    assert changes and changes[0]["code"] == "KPI-032" and changes[0]["new_formula_version"]
    sync_kpi_catalog(db, apply=True)
    db.refresh(d)
    assert d.status == "active" and d.validated
    new = db.execute(select(KpiFormulaVersion).where(KpiFormulaVersion.kpi_id == d.id,
                                                     KpiFormulaVersion.is_current.is_(True))).scalar_one()
    assert new.calculator_key != "unimplemented" and new.version == cur.version + 2
    assert sync_kpi_catalog(db) == []
