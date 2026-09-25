"""Reliability checks for metric results and their aggregation into a level.

Generic checks work on any metric: they look at every bundle the metric
resolved. Metric-specific checks are added here by their metric steps.
Thresholds are named constants so they can be tuned in one place.
"""

from __future__ import annotations

import calendar
from datetime import date
from urllib.parse import unquote

from metrics_contracts import (
    CheckStatus,
    DataRequirement,
    MetricData,
    Reliability,
    ReliabilityCheck,
    ReliabilityLevel,
)
from resolver_contracts import SeriesBundle, TimeSeries
from wiki_contracts import ApiError, Granularity

# data_coverage: returned points / expected points
COVERAGE_WARN_BELOW = 0.95
COVERAGE_FAIL_BELOW = 0.80

# min_volume: average daily views
MIN_VOLUME_WARN_BELOW = 50
MIN_VOLUME_FAIL_BELOW = 10

# spike_dominance: share of the period's views in the top days
SPIKE_TOP_DAYS = 3
SPIKE_WARN_ABOVE = 0.30

# data_freshness: the period ends this close to today (the API lags a day or two)
FRESHNESS_WARN_WITHIN_DAYS = 2

# aggregation: this many warns make a result "low"
LOW_LEVEL_MIN_WARNS = 2


# ---------------------------------------------------------------------------
# Generic checks
# ---------------------------------------------------------------------------


def data_coverage(data: MetricData) -> ReliabilityCheck:
    """Returned points / expected points, for every resolved bundle."""
    statuses, parts = [], []
    for name, bundle in _named_bundles(data):
        cov = bundle.coverage
        ratio = cov.returned_points / cov.expected_points if cov.expected_points else 0.0
        statuses.append(_grade_below(ratio, COVERAGE_WARN_BELOW, COVERAGE_FAIL_BELOW))
        parts.append(f"{name}{cov.returned_points}/{cov.expected_points} {_unit(bundle.series)}")
    return _combine("data_coverage", statuses, parts)


def min_volume(data: MetricData) -> ReliabilityCheck:
    """Average daily views, for every resolved bundle."""
    statuses, parts = [], []
    for name, bundle in _named_bundles(data):
        avg = _avg_daily_views(bundle.series)
        statuses.append(_grade_below(avg, MIN_VOLUME_WARN_BELOW, MIN_VOLUME_FAIL_BELOW))
        parts.append(f"{name}{avg:.0f} avg daily views")
    return _combine("min_volume", statuses, parts)


def spike_dominance(data: MetricData) -> ReliabilityCheck:
    """Warn when the top days hold too large a share of the period's views."""
    statuses, parts = [], []
    for name, bundle in _named_bundles(data):
        series = bundle.series
        total = sum(series.views)
        if series.granularity != Granularity.DAILY:
            statuses.append(CheckStatus.PASS)
            parts.append(f"{name}not applicable to {series.granularity} series")
            continue
        if total == 0:
            statuses.append(CheckStatus.PASS)
            parts.append(f"{name}no views")
            continue
        top = sorted(zip(series.views, series.dates), reverse=True)[:SPIKE_TOP_DAYS]
        share = sum(views for views, _ in top) / total
        if share > SPIKE_WARN_ABOVE:
            statuses.append(CheckStatus.WARN)
            parts.append(f"{name}top {SPIKE_TOP_DAYS} days = {share:.0%} of views ({top[0][1]} spike)")
        else:
            statuses.append(CheckStatus.PASS)
            parts.append(f"{name}top {SPIKE_TOP_DAYS} days = {share:.0%} of views")
    return _combine("spike_dominance", statuses, parts)


def fetch_errors(data: MetricData) -> ReliabilityCheck:
    """Fail when a whole input series is missing, warn when only some articles failed."""
    statuses, parts = [], []
    for name, bundle in _named_bundles(data):
        if not bundle.errors:
            continue
        # A project bundle has no articles: any error means the series is missing.
        series_missing = not bundle.subject.articles or not bundle.per_article
        statuses.append(CheckStatus.FAIL if series_missing else CheckStatus.WARN)
        failed = "; ".join(_describe_error(error) for error in bundle.errors)
        scope = "series missing" if series_missing else "some articles failed"
        parts.append(f"{name}{scope}: {failed}")
    if not statuses:
        return ReliabilityCheck("fetch_errors", CheckStatus.PASS, "No errors")
    return _combine("fetch_errors", statuses, parts)


def data_freshness(data: MetricData, today: date | None = None) -> ReliabilityCheck:
    """Warn when the period ends so recently that the last days may be incomplete."""
    today = today or date.today()
    days_ago = (today - date.fromisoformat(data.spec.period.end)).days
    if days_ago <= FRESHNESS_WARN_WITHIN_DAYS:
        detail = f"Period ends {_days_ago_text(days_ago)}; the latest days may be incomplete"
        return ReliabilityCheck("data_freshness", CheckStatus.WARN, detail)
    return ReliabilityCheck("data_freshness", CheckStatus.PASS, f"Period ended {days_ago} days ago")


GENERIC_CHECKS = [data_coverage, min_volume, spike_dominance, fetch_errors, data_freshness]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(checks: list[ReliabilityCheck]) -> Reliability:
    """Any fail -> invalid; 2+ warns -> low; 1 warn -> medium; none -> high."""
    warns = sum(check.status == CheckStatus.WARN for check in checks)
    if any(check.status == CheckStatus.FAIL for check in checks):
        level = ReliabilityLevel.INVALID
    elif warns >= LOW_LEVEL_MIN_WARNS:
        level = ReliabilityLevel.LOW
    elif warns == 1:
        level = ReliabilityLevel.MEDIUM
    else:
        level = ReliabilityLevel.HIGH
    return Reliability(level=level, usable_for_claims=level != ReliabilityLevel.INVALID, checks=list(checks))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _named_bundles(data: MetricData) -> list[tuple[str, SeriesBundle]]:
    """Every bundle with a detail prefix naming it, empty when there is only one.

    The prefix holds the subject label when the metric spans several subjects
    and the requirement kind when it resolves several series per subject.
    """
    many_subjects = len(data.subjects) > 1
    many_kinds = len(data.bundles) > 1
    named = []
    for requirement, by_label in data.bundles.items():
        for label, bundle in by_label.items():
            words = [label] if many_subjects else []
            if many_kinds:
                words.append(_requirement_name(requirement))
            named.append((f"{' '.join(words)}: " if words else "", bundle))
    return named


def _requirement_name(requirement: DataRequirement) -> str:
    name = str(requirement.kind)
    if requirement.access != DataRequirement().access:
        name += f" {requirement.access}"
    return name


def _grade_below(value: float, warn_below: float, fail_below: float) -> CheckStatus:
    if value < fail_below:
        return CheckStatus.FAIL
    if value < warn_below:
        return CheckStatus.WARN
    return CheckStatus.PASS


def _combine(name: str, statuses: list[CheckStatus], parts: list[str]) -> ReliabilityCheck:
    """The worst status wins; the detail lists every part."""
    if CheckStatus.FAIL in statuses:
        status = CheckStatus.FAIL
    elif CheckStatus.WARN in statuses:
        status = CheckStatus.WARN
    else:
        status = CheckStatus.PASS
    return ReliabilityCheck(name, status, ", ".join(parts) if parts else "No series resolved")


def _unit(series: TimeSeries) -> str:
    return {Granularity.MONTHLY: "months", Granularity.HOURLY: "hours"}.get(series.granularity, "days")


def _avg_daily_views(series: TimeSeries) -> float:
    if series.granularity == Granularity.MONTHLY:
        days = sum(calendar.monthrange(int(d[:4]), int(d[5:7]))[1] for d in series.dates)
    elif series.granularity == Granularity.HOURLY:
        days = len(series.dates) / 24
    else:
        days = len(series.dates)
    return sum(series.views) / days if days else 0.0


def _describe_error(error: ApiError) -> str:
    # Per-article URLs end in .../{article}/{granularity}/{start}/{end}.
    article = unquote(error.url.split("/")[-4]) if error.url and "/per-article/" in error.url else None
    code = f"HTTP {error.status_code}" if error.status_code else str(error.type)
    return f"{article + ' ' if article else ''}{code} {error.title}".strip()


def _days_ago_text(days_ago: int) -> str:
    if days_ago < 0:
        return f"in {-days_ago} days"
    if days_ago == 0:
        return "today"
    return f"{days_ago} day{'s' if days_ago > 1 else ''} ago"
