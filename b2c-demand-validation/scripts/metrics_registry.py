"""Registry of every metric the runner and the catalog know about.

Each metric step adds its `MetricDefinition` here, keyed by its id.
"""

from __future__ import annotations

import metrics_calc
from metrics_contracts import (
    CheckStatus,
    DataRequirement,
    InputParam,
    MetricData,
    MetricDefinition,
    MetricScope,
    PeriodLength,
    PeriodUnit,
    Reliability,
)
from reliability import GENERIC_CHECKS, spike_dominance

CURRENT = DataRequirement()

SUBJECTS_INPUT = InputParam(name="subjects", type="list[Subject]")
PERIOD_INPUT = InputParam(name="period", type="Period")


# ---------------------------------------------------------------------------
# interest_volume
# ---------------------------------------------------------------------------


def _interest_volume_compute(data: MetricData) -> dict[str, float | int]:
    bundle = data.bundle(CURRENT)
    return metrics_calc.interest_volume(bundle.series.views, bundle.coverage.expected_points)


def _interest_volume_interpret(value: dict[str, float | int], data: MetricData, reliability: Reliability) -> str:
    label = data.subjects[0].label
    median = value["median_daily_views"]
    sentence = (
        f"{label} {_verb(label, 'gets')} about {median:,.0f} views a day "
        f"({value['total_views']:,} over the period): {metrics_calc.volume_band(median)} interest."
    )
    spiky = any(c.name == spike_dominance.__name__ and c.status == CheckStatus.WARN for c in reliability.checks)
    if spiky:
        sentence += f" The average ({value['avg_daily_views']:,.0f}/day) is inflated by spikes."
    return sentence


INTEREST_VOLUME = MetricDefinition(
    id="interest_volume",
    title="Interest volume",
    answers="How big is the interest in the subject?",
    use_when="Sizing a product area or checking that a subject has enough attention to analyse.",
    do_not_use_when=(
        "Comparing subjects across different language projects: each project has a different audience size."
    ),
    interpretation_guide={
        "niche": "median < 50/day",
        "moderate": "median 50 .. 500/day",
        "significant": "median 500 .. 5,000/day",
        "mass": "median > 5,000/day",
    },
    limitations="Pageviews are a proxy for interest, not purchase intent.",
    inputs=[SUBJECTS_INPUT, PERIOD_INPUT],
    min_period=PeriodLength(28, PeriodUnit.DAYS),
    recommended_period="12 months",
    min_subjects=1,
    scope=MetricScope.PER_SUBJECT,
    unit="views",
    output={
        "value": {"total_views": "int", "avg_daily_views": "float", "median_daily_views": "float"},
        "unit": "views",
    },
    data=[CURRENT],
    compute=_interest_volume_compute,
    checks=list(GENERIC_CHECKS),
    interpret=_interest_volume_interpret,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verb(label: str, singular: str) -> str:
    """'Meal kits get', 'Food delivery gets': a plural-looking label drops the -s."""
    word = label.split()[-1].lower()
    plural = word.endswith("s") and not word.endswith("ss")
    return singular[:-1] if plural else singular


REGISTRY: dict[str, MetricDefinition] = {d.id: d for d in [INTEREST_VOLUME]}
