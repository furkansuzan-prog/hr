"""Unit tests for pure rules (no DB): KPI arithmetic, forecasting, timekeeping."""
import math
from datetime import UTC, date, datetime

import pytest

from app.ai.forecast import InsufficientData, forecast, robust_anomalies
from app.analytics.domain import (
    INSUFFICIENT,
    NOT_COMPUTABLE,
    OK,
    SUPPRESSED,
    month_starts,
    pearson,
    prorate,
    ratio,
    suppress_small_counts,
)
from app.services.timekeeping import PresenceInterval, dedup_key, pair_events, split_by_day


# --- scenario 9: zero denominator is "not computable", never 0
def test_zero_denominator_is_not_computable():
    r = ratio(0, 0)
    assert r.status == NOT_COMPUTABLE and r.value is None


def test_missing_data_differs_from_true_zero():
    assert ratio(None, 100).status == INSUFFICIENT
    assert ratio(0, 100, has_source_data=False).status == INSUFFICIENT
    real_zero = ratio(0, 100)
    assert real_zero.status == OK and real_zero.value == 0.0


def test_small_sample_is_suppressed_and_hides_numbers():
    r = ratio(1, 4, sample_size=4, min_sample=5)
    assert r.status == SUPPRESSED and r.value is None and r.numerator is None


def test_breakdown_small_counts_masked():
    assert suppress_small_counts({"voluntary": 7, "retirement": 2, "death": 0}, 50, 5) == {
        "voluntary": 7, "retirement": None, "death": 0}
    assert suppress_small_counts({"voluntary": 7}, 3, 5) == {}


def test_ratio_value():
    assert ratio(2, 27.5).value == pytest.approx(7.2727, abs=1e-4)


def test_prorate_and_months():
    assert prorate(90, date(2025, 1, 30), date(2025, 2, 8), date(2025, 2, 1), date(2025, 2, 28)) == pytest.approx(72)
    assert month_starts(date(2025, 11, 15), date(2026, 2, 1)) == [date(2025, 11, 1), date(2025, 12, 1),
                                                                   date(2026, 1, 1), date(2026, 2, 1)]


def test_pearson_requires_three_points():
    assert pearson([1, 2], [2, 4]) == (None, 2)
    r, n = pearson([1, 2, 3, 4], [2, 4, 6, 8.5])
    assert n == 4 and r > 0.99


# --- scenario 18: insufficient data for forecasting
def test_forecast_refuses_short_series():
    with pytest.raises(InsufficientData):
        forecast([100, 101, 102], min_points=12)


# --- scenario 17: seasonality is detected and the peak month is preserved
def test_forecast_seasonal_model_and_intervals():
    series = [1000 + 5 * i + 150 * math.sin(2 * math.pi * (i % 12 - 3) / 12) for i in range(36)]
    fc = forecast(series, horizon=6)
    assert fc.model == "linear_trend+seasonal12"
    assert fc.backtest_mape is not None and fc.backtest_mape < 5
    for p in fc.points:
        assert p["lo95"] <= p["lo80"] <= p["mean"] <= p["hi80"] <= p["hi95"]
    # interval widens with horizon
    w = [p["hi95"] - p["lo95"] for p in fc.points]
    assert w == sorted(w)
    idx = fc.explanation["seasonal_indices"]
    assert idx.index(max(idx)) == 6  # sin peak at (i%12-3)=3 -> i%12 = 6


def test_forecast_linear_without_enough_seasons():
    fc = forecast([float(100 + i) for i in range(14)], horizon=2)
    assert fc.model == "linear_trend"
    assert fc.points[0]["mean"] == pytest.approx(114, abs=0.01)


def test_robust_anomaly():
    s = [(f"2025-{m:02d}", 3.0 + (0.1 if m % 2 else -0.1)) for m in range(1, 12)] + [("2025-12", 9.0)]
    a = robust_anomalies(s)
    assert len(a) == 1 and a[0]["period"] == "2025-12" and a[0]["direction"] == "high"


# --- scenario 5: duplicate badge reads collapse to one event
def test_dedup_key_same_minute():
    t1 = datetime(2025, 1, 6, 7, 59, 10, tzinfo=UTC)
    t2 = datetime(2025, 1, 6, 7, 59, 55, tzinfo=UTC)
    assert dedup_key(1, "G1", t1, "in") == dedup_key(1, "G1", t2, "in")
    assert dedup_key(1, "G1", t1, "in") != dedup_key(1, "G1", t1, "out")


# --- scenario 16: shift crossing midnight / night work
def test_night_shift_split_across_days():
    iv = PresenceInterval(datetime(2025, 1, 6, 22, 0, tzinfo=UTC), datetime(2025, 1, 7, 6, 30, tzinfo=UTC))
    days = split_by_day(iv)
    assert days[date(2025, 1, 6)]["hours"] == pytest.approx(2.0)
    assert days[date(2025, 1, 7)]["hours"] == pytest.approx(6.5)
    assert days[date(2025, 1, 6)]["night_hours"] == pytest.approx(2.0)
    assert days[date(2025, 1, 7)]["night_hours"] == pytest.approx(6.0)  # 00:00-06:00; 06:00-06:30 is day time


def test_unpaired_events_are_reported_not_imputed():
    ev = [{"event_time": datetime(2025, 1, 6, 8, tzinfo=UTC), "direction": "in"},
          {"event_time": datetime(2025, 1, 6, 9, tzinfo=UTC), "direction": "in"},
          {"event_time": datetime(2025, 1, 6, 17, tzinfo=UTC), "direction": "out"},
          {"event_time": datetime(2025, 1, 7, 18, tzinfo=UTC), "direction": "out"}]
    res = pair_events(ev)
    assert len(res.intervals) == 1 and res.intervals[0].start.hour == 9
    assert len(res.unmatched) == 2
