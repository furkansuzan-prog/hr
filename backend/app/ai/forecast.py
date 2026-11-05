"""Explainable statistical forecasting (ADR-011). Pure functions - no DB."""
from dataclasses import dataclass

import numpy as np

Z80, Z95 = 1.2816, 1.96


class InsufficientData(ValueError):
    pass


@dataclass
class Forecast:
    model: str
    points: list[dict]  # {"step", "mean", "lo80", "hi80", "lo95", "hi95"}
    backtest_mape: float | None
    training_points: int
    explanation: dict


def _fit_trend(y: np.ndarray) -> tuple[float, float]:
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def _seasonal_indices(y: np.ndarray, slope: float, intercept: float, period: int = 12) -> np.ndarray:
    x = np.arange(len(y), dtype=float)
    resid = y - (slope * x + intercept)
    idx = np.array([resid[i::period].mean() for i in range(period)])
    return idx - idx.mean()


def _fit_predict(y: np.ndarray, horizon: int) -> tuple[np.ndarray, np.ndarray, str, dict]:
    slope, intercept = _fit_trend(y)
    seasonal = len(y) >= 24
    x = np.arange(len(y), dtype=float)
    fitted = slope * x + intercept
    s_idx = np.zeros(12)
    if seasonal:
        s_idx = _seasonal_indices(y, slope, intercept)
        fitted = fitted + np.array([s_idx[i % 12] for i in range(len(y))])
    fx = np.arange(len(y), len(y) + horizon, dtype=float)
    pred = slope * fx + intercept + (np.array([s_idx[int(i) % 12] for i in fx]) if seasonal else 0)
    model = "linear_trend+seasonal12" if seasonal else "linear_trend"
    return pred, y - fitted, model, {"slope_per_month": round(slope, 4), "intercept": round(intercept, 3),
                                      "seasonal_indices": [round(float(v), 3) for v in s_idx] if seasonal else None}


def forecast(series: list[float], horizon: int = 3, min_points: int = 12) -> Forecast:
    y = np.array([v for v in series if v is not None], dtype=float)
    if len(y) < min_points:
        raise InsufficientData(f"Forecast needs at least {min_points} monthly observations; got {len(y)}")
    pred, resid, model, expl = _fit_predict(y, horizon)
    dof = max(1, len(y) - 2)
    sigma = float(np.sqrt((resid**2).sum() / dof))
    points = []
    for h, m in enumerate(pred, start=1):
        se = sigma * np.sqrt(1 + h / len(y))  # widen with horizon
        points.append({"step": h, "mean": round(float(m), 2), "lo80": round(float(m - Z80 * se), 2),
                       "hi80": round(float(m + Z80 * se), 2), "lo95": round(float(m - Z95 * se), 2),
                       "hi95": round(float(m + Z95 * se), 2)})
    # back-test: hold out the last 3 points, refit on the rest
    mape = None
    hold = 3
    if len(y) - hold >= min_points:
        bt_pred, _, _, _ = _fit_predict(y[:-hold], hold)
        actual = y[-hold:]
        nz = actual != 0
        if nz.any():
            mape = round(float(np.mean(np.abs((actual[nz] - bt_pred[nz]) / actual[nz])) * 100), 2)
    expl.update({"residual_sigma": round(sigma, 3), "method": "OLS trend; intervals from residual sigma",
                 "backtest": f"last {hold} months held out" if mape is not None else "not enough data"})
    return Forecast(model, points, mape, len(y), expl)


def robust_anomalies(series: list[tuple[str, float | None]], threshold: float = 3.5) -> list[dict]:
    """Modified z-score (median / MAD). Flags points far from the series' typical level."""
    vals = np.array([v for _, v in series if v is not None], dtype=float)
    if len(vals) < 6:
        return []
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med)))
    if mad == 0:
        return []
    out = []
    for label, v in series:
        if v is None:
            continue
        z = 0.6745 * (v - med) / mad
        if abs(z) >= threshold:
            out.append({"period": label, "value": v, "median": round(med, 3), "modified_z": round(z, 2),
                        "direction": "high" if z > 0 else "low"})
    return out
