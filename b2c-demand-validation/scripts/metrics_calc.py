"""Pure metric calculations on lists of numbers.

No I/O and no knowledge of specs or bundles: each function takes plain
numbers and returns a value. Every metric step adds its function here.
"""

from __future__ import annotations

from datetime import date, timedelta
from math import inf, sqrt
from statistics import correlation, mean, median, stdev

# interest_volume size bands, on median daily views
VOLUME_BANDS = [(50, "niche"), (500, "moderate"), (5000, "significant")]
VOLUME_TOP_BAND = "mass"

# trend: slope in %/month beyond which interest is rising or falling
TREND_FLAT_WITHIN = 1.0
# trend consistency bands, on R²: each band holds values below its upper bound
TREND_CONSISTENCY_BANDS = [(0.3, "not clear"), (0.6, "moderate")]
TREND_TOP_CONSISTENCY = "consistent"
WEEKS_PER_MONTH = 52 / 12

# volatility bands, on the coefficient of variation: each band holds values below its upper bound
VOLATILITY_BANDS = [(0.3, "stable"), (0.7, "moderately volatile")]
VOLATILITY_TOP_BAND = "volatile"
# volatility spike days: above median + this many median absolute deviations
SPIKE_MAD_MULTIPLIER = 5
SPIKE_MAX_DATES = 5

# growth_rate bands, on the ratio: each band holds values below its upper bound
GROWTH_BANDS = [(-0.2, "strong decline"), (-0.05, "decline"), (0.05, "flat"), (0.2, "growth")]
GROWTH_TOP_BAND = "strong growth"

# share_of_voice: the top two shares are a tie when they are this close
SHARE_TIE_WITHIN = 0.05

# seasonality: a month is a peak / low when its index is at least / at most this
SEASON_PEAK_FROM = 1.15
SEASON_LOW_TO = 0.85
# seasonality strength bands, on the amplitude: each band holds values below its upper bound
SEASONALITY_BANDS = [(1.3, "no real"), (2.0, "moderate")]
SEASONALITY_TOP_BAND = "strong"
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


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


def relative_interest(
    subject_current: list[int],
    project_current: list[int],
    subject_baseline: list[int],
    project_baseline: list[int],
) -> dict[str, float]:
    """Change of the subject's share of all project views, and of the project's own daily views.

    relative_change = share_current / share_baseline - 1, with share = subject total / project total.
    project_change compares average daily views, so periods of different length compare fairly.
    Raises ZeroDivisionError when the baseline subject or either project series has no views.
    """
    share_current = sum(subject_current) / sum(project_current)
    share_baseline = sum(subject_baseline) / sum(project_baseline)
    avg_current = sum(project_current) / len(project_current)
    avg_baseline = sum(project_baseline) / len(project_baseline)
    return {
        "relative_change": round(share_current / share_baseline - 1, 4),
        "project_change": round(avg_current / avg_baseline - 1, 4),
    }


def weekly_ratio(dates: list[str], views: list[int], denominator: list[int]) -> list[float]:
    """Monday-Sunday sums of `views` over those of `denominator`, for full weeks where the denominator has views."""
    weeks = zip(resample_weekly(dates, views), resample_weekly(dates, denominator))
    return [part / whole for part, whole in weeks if whole]


def seasonal_years(dates: list[str], views: list[int]) -> list[dict[int, int]]:
    """Split a monthly series into 12-month years, {month number: views}, counted back from the last month.

    Aligning to the end keeps the most recent data; leftover months at the start are dropped.
    """
    months = list(zip(dates, views))
    months = months[len(months) % 12 :]
    return [{int(d[5:7]): v for d, v in months[i : i + 12]} for i in range(0, len(months), 12)]


def seasonality(dates: list[str], views: list[int]) -> dict[str, list[float] | list[str] | float | None]:
    """Average monthly profile, Jan..Dec, with each year normalised by its own mean (1.0 = average month).

    Normalising per year removes the year-to-year trend. Years without views are skipped.
    amplitude is None when a month has no views at all.
    Raises ZeroDivisionError when no year has views.
    """
    years = [year for year in seasonal_years(dates, views) if sum(year.values())]
    if not years:
        raise ZeroDivisionError("no year of the period has views")
    index = [round(mean(year[m] / mean(year.values()) for year in years), 2) for m in range(1, 13)]
    return {
        "index": index,
        "peak_months": [MONTH_NAMES[m] for m, value in enumerate(index) if value >= SEASON_PEAK_FROM],
        "low_months": [MONTH_NAMES[m] for m, value in enumerate(index) if value <= SEASON_LOW_TO],
        "amplitude": round(max(index) / min(index), 2) if min(index) else None,
    }


def seasonality_band(amplitude: float | None) -> str:
    """no real < 1.3, moderate 1.3 .. 2, strong above (and when a month has no views)."""
    if amplitude is None:
        return SEASONALITY_TOP_BAND
    for upper, band in SEASONALITY_BANDS:
        if amplitude < upper:
            return band
    return SEASONALITY_TOP_BAND


def seasonal_consistency(years: list[dict[int, int]]) -> float | None:
    """Mean Pearson correlation between the monthly profiles of every pair of years.

    Years with no variation have no profile to correlate and are skipped; None when no pair is left.
    """
    profiles = [[year[m] for m in range(1, 13)] for year in years if len(set(year.values())) > 1]
    pairs = [correlation(a, b) for i, a in enumerate(profiles) for b in profiles[i + 1 :]]
    return round(mean(pairs), 2) if pairs else None


def share_of_voice(totals: dict[str, int]) -> dict[str, float]:
    """Each subject's share of the summed views, highest first.

    Raises ZeroDivisionError when no subject has views.
    """
    grand_total = sum(totals.values())
    shares = {label: round(total / grand_total, 4) for label, total in totals.items()}
    return dict(sorted(shares.items(), key=lambda item: item[1], reverse=True))


def is_share_tie(first: float, second: float) -> bool:
    """The top two shares are within 5 percentage points."""
    return round(first - second, 4) <= SHARE_TIE_WITHIN
