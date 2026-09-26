"""Pure metric calculations on lists of numbers.

No I/O and no knowledge of specs or bundles: each function takes plain
numbers and returns a value. Every metric step adds its function here.
"""

from __future__ import annotations

from math import inf, sqrt
from statistics import mean, median, stdev

# interest_volume size bands, on median daily views
VOLUME_BANDS = [(50, "niche"), (500, "moderate"), (5000, "significant")]
VOLUME_TOP_BAND = "mass"

# volatility bands, on the coefficient of variation: each band holds values below its upper bound
VOLATILITY_BANDS = [(0.3, "stable"), (0.7, "moderately volatile")]
VOLATILITY_TOP_BAND = "volatile"
# volatility spike days: above median + this many median absolute deviations
SPIKE_MAD_MULTIPLIER = 5
SPIKE_MAX_DATES = 5

# growth_rate bands, on the ratio: each band holds values below its upper bound
GROWTH_BANDS = [(-0.2, "strong decline"), (-0.05, "decline"), (0.05, "flat"), (0.2, "growth")]
GROWTH_TOP_BAND = "strong growth"


def interest_volume(views: list[int], expected_days: int) -> dict[str, float | int]:
    """Total, average and median daily views of a daily series."""
    total = sum(views)
    return {
        "total_views": total,
        "avg_daily_views": round(total / expected_days, 1) if expected_days else 0.0,
        "median_daily_views": float(median(views)) if views else 0.0,
    }


def volume_band(median_daily_views: float) -> str:
    """niche < 50/day, moderate 50 .. 500, significant 500 .. 5,000, mass above."""
    for upper, band in VOLUME_BANDS:
        if median_daily_views < upper:
            return band
    return VOLUME_TOP_BAND


def growth_rate(current: list[int], current_days: int, baseline: list[int], baseline_days: int) -> float:
    """Average daily views of the current period over the baseline's, minus 1.

    Averages, not totals, so periods of different length compare fairly.
    Raises ZeroDivisionError when the baseline has no views.
    """
    avg_current = sum(current) / current_days
    avg_baseline = sum(baseline) / baseline_days
    return round(avg_current / avg_baseline - 1, 4)


def growth_band(value: float) -> str:
    """strong decline < -0.2, decline .. -0.05, flat .. +0.05, growth .. +0.2, strong growth above."""
    for upper, band in GROWTH_BANDS:
        if value < upper:
            return band
    return GROWTH_TOP_BAND


def weekly_sums(views: list[int]) -> list[int]:
    """Sums of consecutive 7-day blocks from the first day; a trailing partial week is dropped."""
    return [sum(views[i : i + 7]) for i in range(0, len(views) - 6, 7)]


def mean_diff_z(current: list[int], baseline: list[int]) -> float:
    """z of the difference in means of two samples: (mean_c - mean_b) / sqrt(sd_c²/n_c + sd_b²/n_b).

    Each sample needs at least 2 values. With no spread at all, any difference
    is infinitely significant and no difference is 0.
    """
    diff = mean(current) - mean(baseline)
    se = sqrt(stdev(current) ** 2 / len(current) + stdev(baseline) ** 2 / len(baseline))
    if se == 0:
        return 0.0 if diff == 0 else (inf if diff > 0 else -inf)
    return diff / se


def volatility(dates: list[str], views: list[int]) -> dict[str, float | list[str] | None]:
    """Coefficient of variation, max / median ratio and the strongest spike days of a daily series.

    cv is None when the series has no views, spike_ratio when the median is 0.
    Spike days are above median + 5 × MAD: at most 5, the biggest first.
    """
    avg, mid = mean(views), median(views)
    mad = median(abs(v - mid) for v in views)
    threshold = mid + SPIKE_MAD_MULTIPLIER * mad
    spikes = sorted(((v, d) for d, v in zip(dates, views) if v > threshold), reverse=True)
    return {
        "cv": round(stdev(views) / avg, 2) if avg else None,
        "spike_ratio": round(max(views) / mid, 1) if mid else None,
        "spike_dates": [d for _, d in spikes[:SPIKE_MAX_DATES]],
    }


def volatility_band(cv: float) -> str:
    """stable < 0.3, moderately volatile 0.3 .. 0.7, volatile above."""
    for upper, band in VOLATILITY_BANDS:
        if cv < upper:
            return band
    return VOLATILITY_TOP_BAND
