"""Pure KPI rules - no database access. Every calculator funnels its numbers through these helpers so the
critical rules (zero denominator, missing vs. zero, small-sample suppression) live in exactly one place."""
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

OK = "ok"
NOT_COMPUTABLE = "not_computable"  # denominator is zero -> value undefined (never 0)
INSUFFICIENT = "insufficient_data"  # source data missing / coverage too low
SUPPRESSED = "suppressed"  # group too small to publish (privacy)

# ADR-017: why a computed value carries no colour (threshold_status = null). One mechanism for every rule.
SHORT_WINDOW = "short_window"  # window shorter than formula parameter threshold_min_days
LOW_EXPOSURE = "low_exposure"  # hours worked below formula parameter threshold_min_exposure_hours (WP-2 R1)
STATUS_REASONS = (SHORT_WINDOW, LOW_EXPOSURE, "unknown_category_share", "low_coverage")


@dataclass
class CalcResult:
    status: str
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    sample_size: int | None = None
    quality_score: float | None = None  # 0..100 share of required inputs that were present/valid
    breakdown: dict = field(default_factory=dict)
    lineage: dict = field(default_factory=dict)
    note: str | None = None
    status_reason: str | None = None  # set by a calculator when the value must not be coloured (STATUS_REASONS)

    def to_dict(self) -> dict:
        return asdict(self)


def ratio(
    numerator: float | None,
    denominator: float | None,
    *,
    scale: float = 100.0,
    sample_size: int | None = None,
    min_sample: int | None = None,
    has_source_data: bool = True,
) -> CalcResult:
    """numerator / denominator * scale with explicit handling of every degenerate case."""
    if not has_source_data or numerator is None or denominator is None:
        return CalcResult(INSUFFICIENT, None, numerator, denominator, sample_size,
                          note="Required source data is missing for this scope/period")
    if denominator == 0:
        return CalcResult(NOT_COMPUTABLE, None, numerator, denominator, sample_size,
                          note="Denominator is zero; the indicator is undefined (not zero)")
    if min_sample is not None and sample_size is not None and sample_size < min_sample:
        # numbers are withheld so that small groups cannot be re-identified
        return CalcResult(SUPPRESSED, None, None, None, None,
                          note=f"Group smaller than the minimum publishable size ({min_sample})")
    return CalcResult(OK, round(float(numerator) / float(denominator) * scale, 4), float(numerator),
                      float(denominator), sample_size)


def suppress_small_counts(breakdown: dict[str, float], total_sample: int | None, min_sample: int) -> dict:
    """Breakdown categories are counts of people; any category < min_sample is masked."""
    if total_sample is not None and total_sample < min_sample:
        return {}
    return {k: (v if v >= min_sample or v == 0 else None) for k, v in breakdown.items()}


def month_starts(start: date, end: date) -> list[date]:
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return out


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def overlap_days(a_start: date, a_end: date, b_start: date, b_end: date) -> int:
    s, e = max(a_start, b_start), min(a_end, b_end)
    return max(0, (e - s).days + 1)


def prorate(hours: float, rec_start: date, rec_end: date, p_start: date, p_end: date) -> float:
    """Allocate a multi-day record's hours to a period proportionally to calendar-day overlap."""
    total = (rec_end - rec_start).days + 1
    if total <= 0:
        return 0.0
    return float(hours) * overlap_days(rec_start, rec_end, p_start, p_end) / total


def pearson(xs: list[float], ys: list[float]) -> tuple[float | None, int]:
    pairs = [(x, y) for x, y in zip(xs, ys, strict=False) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx == 0 or syy == 0:
        return None, n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    return round(sxy / (sxx * syy) ** 0.5, 4), n
