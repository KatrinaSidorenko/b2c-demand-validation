"""Pure metric calculations on lists of numbers.

No I/O and no knowledge of specs or bundles: each function takes plain
numbers and returns a value. Every metric step adds its function here.
"""

from __future__ import annotations

from datetime import date, timedelta
from math import inf, sqrt
from statistics import mean, median, stdev

# interest_volume size bands, on median daily views
VOLUME_BANDS = [(50, "niche"), (500, "moderate"), (5000, "significant")]
VOLUME_TOP_BAND = "mass"

# trend: slope in %/month beyond which interest is rising or falling
TREND_FLAT_WITHIN = 1.0
# trend consistency bands, on R²: each band holds values below its upper bound
TREND_CONSISTENCY_BANDS = [(0.3, "not clear"), (0.6, "moderate")]
TREND_TOP_CONSISTENCY = "consistent"
WEEKS_PER_MONTH = 52 / 12

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


def resample_weekly(dates: list[str], views: list[int]) -> list[int]:
    """Monday-Sunday sums of a daily series; partial first and last weeks are dropped."""
    weeks: dict[date, list[int]] = {}
    for day, count in zip(dates, views):
        d = date.fromisoformat(day)
        weeks.setdefault(d - timedelta(days=d.weekday()), []).append(count)
    return [sum(days) for _, days in sorted(weeks.items()) if len(days) == 7]


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


def linear_fit(values: list[int]) -> tuple[float, float]:
    """Ordinary least squares of the values on their index: (slope per step, R²).

    R² is 0 when the values do not vary: there is no trend to explain.
    """
    n = len(values)
    x_mean, y_mean = (n - 1) / 2, mean(values)
    sxx = sum((x - x_mean) ** 2 for x in range(n))
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in enumerate(values))
    syy = sum((y - y_mean) ** 2 for y in values)
    slope = sxy / sxx
    r2 = sxy**2 / (sxx * syy) if syy else 0.0
    return slope, r2


def trend(weekly: list[int]) -> dict[str, float]:
    """Linear trend of weekly sums, as % of the average week per month, and its R².

    Raises ZeroDivisionError when the weeks have no views.
    """
    slope, r2 = linear_fit(weekly)
    return {
        "slope_pct_per_month": round(slope * WEEKS_PER_MONTH / mean(weekly) * 100, 2),
        "r2": round(r2, 2),
    }


def trend_direction(slope_pct_per_month: float) -> str:
    """rising above +1%/month, falling below -1%/month, flat in between."""
    if slope_pct_per_month > TREND_FLAT_WITHIN:
        return "rising"
    if slope_pct_per_month < -TREND_FLAT_WITHIN:
        return "falling"
    return "flat"


def trend_consistency(r2: float) -> str:
    """not clear < 0.3, moderate 0.3 .. 0.6, consistent from 0.6."""
    for upper, band in TREND_CONSISTENCY_BANDS:
        if r2 < upper:
            return band
    return TREND_TOP_CONSISTENCY
