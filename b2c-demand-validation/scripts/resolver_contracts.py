"""Contracts for the data resolver layer.

Business-level inputs (subjects, periods) and the clean time series the
resolver returns to the metrics layer. This module has no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wiki_contracts import Access, Agent, ApiError, Granularity


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """A group of articles (e.g. synonyms) whose views are summed."""

    label: str
    articles: list[str]


@dataclass(frozen=True)
class Period:
    """Inclusive date range, ISO dates: "2026-08-01"."""

    start: str
    end: str


@dataclass(frozen=True)
class SeriesRequest:
    project: str
    subject: Subject
    period: Period
    access: Access = Access.ALL_ACCESS
    agent: Agent = Agent.USER
    granularity: Granularity = Granularity.DAILY


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeSeries:
    """One point per bucket of the period.

    `dates` are ISO strings: "2026-08-01" for daily, the first of the month
    ("2026-08-01") for monthly, "2026-08-01T13:00" for hourly.
    """

    granularity: Granularity
    dates: list[str]
    views: list[int]


@dataclass(frozen=True)
class Coverage:
    """Counts buckets (days, months or hours, matching the series granularity)."""

    expected_points: int
    returned_points: int
    missing_dates: list[str]


@dataclass(frozen=True)
class SeriesBundle:
    subject: Subject
    series: TimeSeries
    coverage: Coverage
    errors: list[ApiError] = field(default_factory=list)
    per_article: dict[str, TimeSeries] = field(default_factory=dict)
