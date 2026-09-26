"""Contracts for the metrics layer.

Metric definitions (what the catalog shows and what the runner executes),
the analysis spec the model writes, and the results with their reliability.
This module has no I/O.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Callable

from resolver_contracts import Period, SeriesBundle, Subject
from wiki_contracts import Access, Granularity


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ReliabilityLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INVALID = "invalid"


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Baseline(StrEnum):
    PREVIOUS_PERIOD = "previous_period"
    YEAR_OVER_YEAR = "year_over_year"


class MetricScope(StrEnum):
    PER_SUBJECT = "per_subject"  # one result per subject
    CROSS_SUBJECT = "cross_subject"  # one result across all subjects


class DataKind(StrEnum):
    """Series kinds the runner knows how to resolve.

    Metric steps add kinds (baseline, project aggregate, ...) together with
    their handling in the runner.
    """

    CURRENT = "current"  # the subject series for `spec.period`
    BASELINE_SERIES = "baseline_series"  # the subject series for the baseline period of the spec


class PeriodUnit(StrEnum):
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"


# ---------------------------------------------------------------------------
# Spec (what the model writes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisSpec:
    project: str
    subjects: list[Subject]
    period: Period
    metrics: list[str]
    baseline: Baseline | None = None


# ---------------------------------------------------------------------------
# Metric definition (what each metric step fills)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodLength:
    """A minimum or recommended period length: 28 days, 12 weeks, 24 months."""

    amount: int
    unit: PeriodUnit

    def short(self) -> str:
        """Compact form for the full catalog: "28d", "12w", "24m"."""
        return f"{self.amount}{self.unit.value[0]}"

    def label(self) -> str:
        """Readable form: "28 days", "12 weeks", "24 months"."""
        return f"{self.amount} {self.unit.value}"

    def is_covered_by(self, period: Period) -> bool:
        start, end = date.fromisoformat(period.start), date.fromisoformat(period.end)
        if self.unit == PeriodUnit.MONTHS:
            return end >= _add_months(start, self.amount) - timedelta(days=1)
        days = self.amount * (7 if self.unit == PeriodUnit.WEEKS else 1)
        return (end - start).days + 1 >= days


@dataclass(frozen=True)
class InputParam:
    """A business input the model provides in the spec."""

    name: str
    type: str
    required: bool = True
    values: list[str] | None = None
    default: str | None = None


@dataclass(frozen=True)
class DataRequirement:
    """A series the runner must resolve before the metric can be computed."""

    kind: DataKind = DataKind.CURRENT
    access: Access = Access.ALL_ACCESS
    granularity: Granularity = Granularity.DAILY


@dataclass(frozen=True)
class MetricData:
    """Everything a metric sees: the spec and the resolved series.

    `subjects` holds one subject for per-subject metrics and all subjects for
    cross-subject ones. `bundles` maps each requirement to its bundles by
    subject label.
    """

    spec: AnalysisSpec
    subjects: list[Subject]
    bundles: dict[DataRequirement, dict[str, SeriesBundle]]

    def bundle(self, requirement: DataRequirement, subject: Subject | None = None) -> SeriesBundle:
        by_label = self.bundles[requirement]
        return by_label[(subject or self.subjects[0]).label]


@dataclass(frozen=True)
class ReliabilityCheck:
    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class Reliability:
    level: ReliabilityLevel
    usable_for_claims: bool
    checks: list[ReliabilityCheck]


# A check looks at the resolved data and returns one ReliabilityCheck.
Check = Callable[[MetricData], ReliabilityCheck]


@dataclass(frozen=True)
class MetricDefinition:
    # catalog text
    id: str
    title: str
    answers: str
    use_when: str
    do_not_use_when: str
    interpretation_guide: dict[str, str]
    limitations: str
    # inputs and constraints
    inputs: list[InputParam]
    min_period: PeriodLength
    recommended_period: str
    min_subjects: int
    scope: MetricScope
    unit: str
    output: dict[str, Any]
    # execution
    data: list[DataRequirement]
    compute: Callable[[MetricData], Any]
    checks: list[Check]
    # value, data, reliability -> interpretation sentence
    interpret: Callable[[Any, MetricData, Reliability], str]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricResult:
    metric: str
    subject: str | None  # None for cross-subject metrics
    value: Any  # None when reliability is invalid
    unit: str
    interpretation: str
    reliability: Reliability


@dataclass(frozen=True)
class SpecError:
    field: str
    code: str
    detail: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def baseline_period(period: Period, baseline: Baseline) -> Period:
    """The period that `period` is compared with.

    previous_period: the same number of days, ending the day before the start.
    year_over_year: the same dates one year earlier (29 Feb maps to 28 Feb).
    """
    start, end = date.fromisoformat(period.start), date.fromisoformat(period.end)
    if baseline == Baseline.YEAR_OVER_YEAR:
        start, end = _add_months(start, -12), _add_months(end, -12)
    else:
        start, end = start - (end - start) - timedelta(days=1), start - timedelta(days=1)
    return Period(start=start.isoformat(), end=end.isoformat())


def _add_months(day: date, months: int) -> date:
    """Shift by whole months, clamping to the last day of the target month."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
