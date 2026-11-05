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


def test_calculator_param_whitelist_matches_catalog_and_code():
    """Every catalog parameter is read by its calculator; keys a calculator ignores are rejected (HR sprint-1)."""
    import ast
    import inspect

    import pytest

    from app.analytics import calculators
    from app.analytics.catalog import KPI_CATALOG
    from app.analytics.engine import _ENGINE_PARAMS, CALCULATOR_PARAMS, KpiError, validate_parameters

    for k in KPI_CATALOG:
        f = k["formula"]
        validate_parameters(f["parameters"] or {}, f["calculator_key"])  # catalog itself must pass

    # whitelist == keys actually read via params.get / params[...] (one level of helper calls)
    funcs = {n: o for n, o in inspect.getmembers(calculators, inspect.isfunction)}

    def read_keys(fn, depth=0):
        out = set()
        for node in ast.walk(ast.parse(inspect.getsource(fn))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "params"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                out.add(node.args[0].value)
            if (depth < 2 and isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in funcs and funcs[node.func.id] is not fn
                    and any(isinstance(a, ast.Name) and a.id == "params" for a in node.args)):
                out |= read_keys(funcs[node.func.id], depth + 1)
        return out

    for key, fn in calculators.CALCULATORS.items():
        assert read_keys(fn) == CALCULATOR_PARAMS.get(key, set()), key

    with pytest.raises(KpiError, match="not used by calculator"):
        validate_parameters({"exclude_categories": ["voluntary"]}, "early_turnover_90")
    validate_parameters({"days": 30, **{p: v for p, v in (("annualize", True),)}}, "early_turnover_90")
    assert _ENGINE_PARAMS == {"annualize", "threshold_min_days"}
