"""Pure metric calculations on lists of numbers.

No I/O and no knowledge of specs or bundles: each function takes plain
numbers and returns a value. Every metric step adds its function here.
"""

from __future__ import annotations

from statistics import median

# interest_volume size bands, on median daily views
VOLUME_BANDS = [(50, "niche"), (500, "moderate"), (5000, "significant")]
VOLUME_TOP_BAND = "mass"


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
