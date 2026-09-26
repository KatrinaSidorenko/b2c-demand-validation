"""Reliability checks for metric results and their aggregation into a level.

Generic checks work on any metric: they look at every bundle the metric
resolved. Metric-specific checks are added here by their metric steps.
Thresholds are named constants so they can be tuned in one place.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Callable
from urllib.parse import unquote

import metrics_calc
from metrics_contracts import (
    PROJECT_KINDS,
    CheckStatus,
    DataKind,
    DataRequirement,
    MetricData,
    Reliability,
    ReliabilityCheck,
    ReliabilityLevel,
)
from resolver_contracts import SeriesBundle, TimeSeries
from wiki_contracts import Access, ApiError, Granularity

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

# signal_vs_noise: |z| of the change on weekly sums below this is noise
SIGNAL_MIN_ABS_Z = 2.0
SIGNAL_MIN_WEEKS = 2

# trend_signal_vs_noise: R² of the weekly trend line below this is no clear trend
TREND_MIN_R2 = 0.3

# seasonal_consistency: mean correlation between the monthly profiles of the years
SEASONAL_CONSISTENCY_WARN_BELOW = 0.5
SEASONAL_CONSISTENCY_FAIL_BELOW = 0.0

# platform checks: a failure of these accesses invalidates the mix; mobile app data is sparse
REQUIRED_ACCESSES = frozenset({Access.DESKTOP, Access.MOBILE_WEB})

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
# Comparison checks (current period vs baseline)
# ---------------------------------------------------------------------------


def baseline_has_views(data: MetricData) -> ReliabilityCheck:
    """Fail when the baseline has no views: a change against zero is undefined."""
    baseline = _bundle_of_kind(data, DataKind.BASELINE_SERIES)
    if baseline is None:
        return ReliabilityCheck("baseline_has_views", CheckStatus.FAIL, "No baseline series resolved")
    total = sum(baseline.series.views)
    if total == 0:
        detail = "The baseline has no data, so the change is undefined"
        return ReliabilityCheck("baseline_has_views", CheckStatus.FAIL, detail)
    return ReliabilityCheck("baseline_has_views", CheckStatus.PASS, f"baseline {total:,} views")


def signal_vs_noise(data: MetricData) -> ReliabilityCheck:
    """Warn when the change between the periods is within normal week-to-week noise.

    Compares weekly sums (full weeks only) rather than days, to reduce the
    day-to-day correlation of the series.
    """
    current = _bundle_of_kind(data, DataKind.CURRENT)
    baseline = _bundle_of_kind(data, DataKind.BASELINE_SERIES)
    if current is None or baseline is None:
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, "Not applicable: needs both periods")
    if sum(baseline.series.views) == 0:
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, "Not tested: the baseline has no views")
    weeks_c = metrics_calc.resample_weekly(current.series.dates, current.series.views)
    weeks_b = metrics_calc.resample_weekly(baseline.series.dates, baseline.series.views)
    if min(len(weeks_c), len(weeks_b)) < SIGNAL_MIN_WEEKS:
        detail = f"Not tested: fewer than {SIGNAL_MIN_WEEKS} full weeks in a period"
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, detail)
    z = metrics_calc.mean_diff_z(weeks_c, weeks_b)
    if abs(z) < SIGNAL_MIN_ABS_Z:
        return ReliabilityCheck(
            "signal_vs_noise", CheckStatus.WARN, f"z = {z:.1f} on weekly sums: change is within normal noise"
        )
    return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, f"z = {z:.1f} on weekly sums")


# ---------------------------------------------------------------------------
# Relative checks (subject vs the whole project)
# ---------------------------------------------------------------------------


def subject_min_volume(data: MetricData) -> ReliabilityCheck:
    """min_volume on the subject series only: the project series is always large."""
    return min_volume(_select(data, lambda r: r.kind not in PROJECT_KINDS))


def subject_spike_dominance(data: MetricData) -> ReliabilityCheck:
    """spike_dominance on the subject series only."""
    return spike_dominance(_select(data, lambda r: r.kind not in PROJECT_KINDS))


def relative_signal_vs_noise(data: MetricData) -> ReliabilityCheck:
    """signal_vs_noise on the weekly ratio subject / project instead of the subject's weekly sums."""
    bundles = {kind: _bundle_of_kind(data, kind) for kind in DataKind}
    subject_c, project_c = bundles[DataKind.CURRENT], bundles[DataKind.PROJECT]
    subject_b, project_b = bundles[DataKind.BASELINE_SERIES], bundles[DataKind.PROJECT_BASELINE]
    if None in (subject_c, project_c, subject_b, project_b):
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, "Not applicable: needs both periods")
    if sum(subject_b.series.views) == 0:
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, "Not tested: the baseline has no views")
    ratio_c = metrics_calc.weekly_ratio(subject_c.series.dates, subject_c.series.views, project_c.series.views)
    ratio_b = metrics_calc.weekly_ratio(subject_b.series.dates, subject_b.series.views, project_b.series.views)
    if min(len(ratio_c), len(ratio_b)) < SIGNAL_MIN_WEEKS:
        detail = f"Not tested: fewer than {SIGNAL_MIN_WEEKS} full weeks in a period"
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, detail)
    z = metrics_calc.mean_diff_z(ratio_c, ratio_b)
    if abs(z) < SIGNAL_MIN_ABS_Z:
        return ReliabilityCheck(
            "signal_vs_noise", CheckStatus.WARN, f"z = {z:.1f} on weekly ratio: change is within normal noise"
        )
    return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, f"z = {z:.1f} on weekly ratio")


# ---------------------------------------------------------------------------
# Trend checks
# ---------------------------------------------------------------------------


def trend_signal_vs_noise(data: MetricData) -> ReliabilityCheck:
    """Warn when a straight line explains little of the weekly series: no clear trend."""
    current = _bundle_of_kind(data, DataKind.CURRENT)
    if current is None:
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, "Not applicable: no current series")
    weekly = metrics_calc.resample_weekly(current.series.dates, current.series.views)
    if len(weekly) < 2:
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, "Not tested: fewer than 2 full weeks")
    if sum(weekly) == 0:
        return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, "Not tested: no views")
    # Rounded like the metric value, so the check and the interpretation agree.
    r2 = round(metrics_calc.linear_fit(weekly)[1], 2)
    if r2 < TREND_MIN_R2:
        return ReliabilityCheck(
            "signal_vs_noise", CheckStatus.WARN, f"R² = {r2:.2f} (< {TREND_MIN_R2}): no clear trend"
        )
    return ReliabilityCheck("signal_vs_noise", CheckStatus.PASS, f"R² = {r2:.2f} on weekly sums")


# ---------------------------------------------------------------------------
# Seasonality checks
# ---------------------------------------------------------------------------


def seasonal_consistency(data: MetricData) -> ReliabilityCheck:
    """Warn when the monthly pattern changes from year to year, fail when years contradict each other."""
    current = _bundle_of_kind(data, DataKind.CURRENT)
    if current is None:
        return ReliabilityCheck("seasonal_consistency", CheckStatus.PASS, "Not applicable: no current series")
    years = metrics_calc.seasonal_years(current.series.dates, current.series.views)
    r = metrics_calc.seasonal_consistency(years)
    if r is None:
        detail = "Not tested: fewer than 2 years with varying monthly views"
        return ReliabilityCheck("seasonal_consistency", CheckStatus.PASS, detail)
    detail = f"mean correlation between {len(years)} years = {r:.2f}"
    if r < SEASONAL_CONSISTENCY_FAIL_BELOW:
        return ReliabilityCheck("seasonal_consistency", CheckStatus.FAIL, f"{detail}: the years contradict each other")
    if r < SEASONAL_CONSISTENCY_WARN_BELOW:
        return ReliabilityCheck("seasonal_consistency", CheckStatus.WARN, f"{detail}: the pattern changes year to year")
    return ReliabilityCheck("seasonal_consistency", CheckStatus.PASS, detail)


# ---------------------------------------------------------------------------
# Platform checks (per-access series)
# ---------------------------------------------------------------------------


def platform_coverage(data: MetricData) -> ReliabilityCheck:
    """data_coverage on desktop and mobile web; mobile app data is sparse for many articles."""
    return data_coverage(_select(data, lambda r: r.access in REQUIRED_ACCESSES))


def platform_min_volume(data: MetricData) -> ReliabilityCheck:
    """min_volume on the combined series of all platforms: a small platform share is a result, not noise."""
    bundles = [bundle for by_label in data.bundles.values() for bundle in by_label.values()]
    first = bundles[0]
    views = [sum(views) for views in zip(*(bundle.series.views for bundle in bundles))]
    combined = SeriesBundle(
        subject=first.subject,
        series=TimeSeries(granularity=first.series.granularity, dates=first.series.dates, views=views),
        coverage=first.coverage,
    )
    requirement = DataRequirement(granularity=first.series.granularity)
    return min_volume(MetricData(spec=data.spec, subjects=data.subjects, bundles={requirement: {"": combined}}))


def platform_fetch_errors(data: MetricData) -> ReliabilityCheck:
    """fetch_errors on desktop and mobile web; a mobile app error only warns, the share then comes without it."""
    required = fetch_errors(_select(data, lambda r: r.access in REQUIRED_ACCESSES))
    app = fetch_errors(_select(data, lambda r: r.access not in REQUIRED_ACCESSES))
    if app.status == CheckStatus.PASS:
        return required
    statuses = [required.status, CheckStatus.WARN]
    parts = [] if required.status == CheckStatus.PASS else [required.detail]
    sparse = "app data is sparse, so the app share may be missing or understated"
    parts.append(f"{Access.MOBILE_APP}: {app.detail}; {sparse}")
    return _combine("fetch_errors", statuses, parts)


# ---------------------------------------------------------------------------
# Share checks (cross-subject)
# ---------------------------------------------------------------------------


def share_min_volume(data: MetricData) -> ReliabilityCheck:
    """min_volume, but a low-volume subject only warns: its share is noise-level, the others still hold."""
    check = min_volume(data)
    if check.status != CheckStatus.FAIL:
        return check
    return ReliabilityCheck(check.name, CheckStatus.WARN, f"{check.detail}; low-volume shares are noise-level")


def subjects_have_views(data: MetricData) -> ReliabilityCheck:
    """Fail when no subject has any views: there is nothing to share."""
    total = sum(sum(bundle.series.views) for _, bundle in _named_bundles(data))
    if total == 0:
        return ReliabilityCheck("subjects_have_views", CheckStatus.FAIL, "No subject has any views")
    return ReliabilityCheck("subjects_have_views", CheckStatus.PASS, f"{total:,} views across all subjects")


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


def _bundle_of_kind(data: MetricData, kind: DataKind) -> SeriesBundle | None:
    """The bundle of the first subject for the first requirement of this kind."""
    for requirement, by_label in data.bundles.items():
        if requirement.kind == kind:
            return by_label.get(data.subjects[0].label)
    return None


def _select(data: MetricData, keep: Callable[[DataRequirement], bool]) -> MetricData:
    """The same data with only the bundles whose requirement passes `keep`."""
    bundles = {r: by_label for r, by_label in data.bundles.items() if keep(r)}
    return MetricData(spec=data.spec, subjects=data.subjects, bundles=bundles)


def _requirement_name(requirement: DataRequirement) -> str:
    """The kind ("current", "project baseline"), or only the access for a per-access current series."""
    name = requirement.kind.removesuffix("_series").replace("_", " ")
    if requirement.access != DataRequirement().access:
        return requirement.access if requirement.kind == DataKind.CURRENT else f"{name} {requirement.access}"
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
