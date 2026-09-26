"""Registry of every metric the runner and the catalog know about.

Each metric step adds its `MetricDefinition` here, keyed by its id.
"""

from __future__ import annotations

import metrics_calc
from metrics_contracts import (
    Baseline,
    CheckStatus,
    DataKind,
    DataRequirement,
    InputParam,
    MetricData,
    MetricDefinition,
    MetricScope,
    PeriodLength,
    PeriodUnit,
    Reliability,
)
from reliability import GENERIC_CHECKS, baseline_has_views, signal_vs_noise, spike_dominance

CURRENT = DataRequirement()
BASELINE = DataRequirement(kind=DataKind.BASELINE_SERIES)

SUBJECTS_INPUT = InputParam(name="subjects", type="list[Subject]")
PERIOD_INPUT = InputParam(name="period", type="Period")
BASELINE_INPUT = InputParam(
    name="baseline",
    type="enum",
    required=False,
    values=[b.value for b in Baseline],
    default=Baseline.PREVIOUS_PERIOD.value,
)


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
# growth_rate
# ---------------------------------------------------------------------------

BASELINE_TEXT = {
    Baseline.PREVIOUS_PERIOD: "the previous period",
    Baseline.YEAR_OVER_YEAR: "the same period last year",
}


def _growth_rate_compute(data: MetricData) -> float:
    current, baseline = data.bundle(CURRENT), data.bundle(BASELINE)
    return metrics_calc.growth_rate(
        current.series.views,
        current.coverage.expected_points,
        baseline.series.views,
        baseline.coverage.expected_points,
    )


def _growth_rate_interpret(value: float, data: MetricData, reliability: Reliability) -> str:
    versus = BASELINE_TEXT[data.spec.baseline or Baseline.PREVIOUS_PERIOD]
    band = metrics_calc.growth_band(value)
    if round(abs(value), 2) == 0:
        sentence = f"Interest held steady versus {versus} ({band})."
    else:
        sentence = f"Interest {'grew' if value > 0 else 'fell'} {abs(value):.0%} versus {versus} ({band})."
    noisy = any(c.name == signal_vs_noise.__name__ and c.status == CheckStatus.WARN for c in reliability.checks)
    if noisy:
        sentence += " The change is within normal fluctuation."
    return sentence


GROWTH_RATE = MetricDefinition(
    id="growth_rate",
    title="Growth rate",
    answers="Is interest in the subject growing or falling?",
    use_when="Deciding whether demand for a product area is expanding.",
    do_not_use_when="The subject had a one-off news event in either period; use trend and volatility first.",
    interpretation_guide={
        "strong_decline": "< -0.2",
        "decline": "-0.2 .. -0.05",
        "flat": "-0.05 .. +0.05",
        "growth": "+0.05 .. +0.2",
        "strong_growth": "> +0.2",
    },
    limitations=(
        "Includes changes in overall Wikipedia traffic; use relative_interest to exclude them. "
        "previous_period mixes in seasonality for periods shorter than a year."
    ),
    inputs=[SUBJECTS_INPUT, PERIOD_INPUT, BASELINE_INPUT],
    min_period=PeriodLength(28, PeriodUnit.DAYS),
    recommended_period="12 months with year_over_year",
    min_subjects=1,
    scope=MetricScope.PER_SUBJECT,
    unit="ratio",
    output={"value": "ratio", "range": "[-1, +inf)"},
    data=[CURRENT, BASELINE],
    compute=_growth_rate_compute,
    checks=[*GENERIC_CHECKS, baseline_has_views, signal_vs_noise],
    interpret=_growth_rate_interpret,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verb(label: str, singular: str) -> str:
    """'Meal kits get', 'Food delivery gets': a plural-looking label drops the -s."""
    word = label.split()[-1].lower()
    plural = word.endswith("s") and not word.endswith("ss")
    return singular[:-1] if plural else singular


REGISTRY: dict[str, MetricDefinition] = {d.id: d for d in [INTEREST_VOLUME, GROWTH_RATE]}
