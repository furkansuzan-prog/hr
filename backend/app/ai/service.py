"""Forecasting / anomaly services (scope-checked) used by the API and the assistant."""
from datetime import date

from sqlalchemy.orm import Session

from app.ai.forecast import InsufficientData, forecast, robust_anomalies
from app.analytics import engine
from app.analytics.calculators import headcount_series
from app.analytics.domain import month_end, month_starts
from app.core.config import get_settings
from app.models import ForecastResult
from app.security.access import AccessContext


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def headcount_forecast(db: Session, ctx: AccessContext, *, as_of: date, entity_ids: set[int] | None = None,
                       project_id: int | None = None, horizon: int = 3, history_months: int = 24) -> dict:
    kpi = engine.get_definition(db, "KPI-016")
    scope = engine.resolve_scope(ctx, kpi, entity_ids, project_id, None)
    start = _add_months(date(as_of.year, as_of.month, 1), -history_months)
    months = month_starts(start, as_of)
    # only complete months
    months = [m for m in months if month_end(m) <= as_of]
    dates = [month_end(m) for m in months]
    series = headcount_series(db, scope, dates)
    # leading zero months (before first hire in scope) are not observations
    values = [series[d] for d in dates]
    while values and values[0] == 0:
        values.pop(0)
        dates.pop(0)
    history = [{"period": d.isoformat(), "value": series[d], "kind": "actual"} for d in dates]
    base = {"metric": "active_headcount", "scope": scope.as_dict(), "history": history}
    try:
        fc = forecast(values, horizon=horizon, min_points=get_settings().forecast_min_points)
    except InsufficientData as e:
        return {**base, "status": "insufficient_data", "note": str(e), "forecast": []}
    last = dates[-1]
    pts = []
    for p in fc.points:
        p = dict(p)
        p["period"] = month_end(_add_months(date(last.year, last.month, 1), p["step"])).isoformat()
        p["kind"] = "forecast"
        pts.append(p)
    rec = ForecastResult(tenant_id=ctx.tenant_id, metric="active_headcount", scope=scope.as_dict(), model=fc.model,
                         horizon_months=horizon, training_points=fc.training_points, backtest_mape=fc.backtest_mape,
                         points=pts, created_by=ctx.user_id)
    db.add(rec)
    db.commit()
    current = values[-1]
    return {**base, "status": "ok", "forecast_id": rec.id, "model": fc.model, "training_points": fc.training_points,
            "backtest_mape": fc.backtest_mape, "explanation": fc.explanation, "forecast": pts,
            "current": current, "delta_at_horizon": round(pts[-1]["mean"] - current, 1)}


def kpi_anomalies(db: Session, ctx: AccessContext, code: str, ps: date, pe: date, **kw) -> dict:
    series = engine.monthly_series(db, ctx, code, ps, pe, **kw)
    pairs = [(s["period_start"][:7], s["value"]) for s in series]
    return {"code": code, "series": pairs, "anomalies": robust_anomalies(pairs),
            "method": "modified z-score (median/MAD), |z| >= 3.5"}
