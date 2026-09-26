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
from reliability import (
    GENERIC_CHECKS,
    baseline_has_views,
    signal_vs_noise,
    spike_dominance,
    trend_signal_vs_noise,
)

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
    explainer=(
        "How many people read about the subject on a typical day: niche (under 50), moderate (50 to 500), "
        "significant (500 to 5,000) or mass (over 5,000)."
    ),
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
    explainer=(
        "Whether more or fewer people read about the subject than in an earlier period of the same length, "
        "as a percentage change."
    ),
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
# trend
# ---------------------------------------------------------------------------


def _trend_compute(data: MetricData) -> dict[str, float]:
    series = data.bundle(CURRENT).series
    return metrics_calc.trend(metrics_calc.resample_weekly(series.dates, series.views))


def _trend_interpret(value: dict[str, float], data: MetricData, reliability: Reliability) -> str:
    slope, r2 = value["slope_pct_per_month"], value["r2"]
    return (
        f"Interest is {metrics_calc.trend_direction(slope)} at about {slope:+.1f}% a month; "
        f"the trend is {metrics_calc.trend_consistency(r2)} (R² = {r2:.2f})."
    )


TREND = MetricDefinition(
    id="trend",
    title="Trend",
    answers="Is the direction of interest steady over the period?",
    explainer=(
        "Whether interest moved steadily up or down across the period, week by week, "
        "or had no clear direction."
    ),
    use_when="Checking that growth or decline is sustained and not caused by a single event.",
    do_not_use_when="The period is shorter than 12 weeks.",
    interpretation_guide={
        "direction": "slope > +1%/month rising, < -1%/month falling, otherwise flat",
        "consistency": "r2 >= 0.6 consistent, 0.3 .. 0.6 moderate, < 0.3 no clear trend",
    },
    limitations="A straight line; it does not capture seasonality or turning points.",
    inputs=[SUBJECTS_INPUT, PERIOD_INPUT],
    min_period=PeriodLength(12, PeriodUnit.WEEKS),
    recommended_period="12 months",
    min_subjects=1,
    scope=MetricScope.PER_SUBJECT,
    unit="%/month",
    output={"value": {"slope_pct_per_month": "float", "r2": "float [0, 1]"}, "unit": "%/month"},
    data=[CURRENT],
    compute=_trend_compute,
    # spike_dominance stays in: a spike near either end of the period tilts the line.
    checks=[*GENERIC_CHECKS, trend_signal_vs_noise],
    interpret=_trend_interpret,
)


# ---------------------------------------------------------------------------
# volatility
# ---------------------------------------------------------------------------


def _volatility_compute(data: MetricData) -> dict[str, float | list[str] | None]:
    series = data.bundle(CURRENT).series
    return metrics_calc.volatility(series.dates, series.views)


def _volatility_interpret(value: dict, data: MetricData, reliability: Reliability) -> str:
    sentence = f"Interest is {metrics_calc.volatility_band(value['cv'])} (CV = {value['cv']:.2f})."
    if value["spike_dates"]:
        sentence += f" Strong spikes on {', '.join(value['spike_dates'])}."
    return sentence


VOLATILITY = MetricDefinition(
    id="volatility",
    title="Volatility",
    answers="Is interest stable or driven by news and one-off events?",
    explainer=(
        "Whether interest is steady from day to day or comes in bursts caused by news and one-off events. "
        "Bursty interest makes the other numbers less dependable."
    ),
    use_when="Checking whether demand is steady before trusting growth or volume numbers.",
    do_not_use_when="Volume is very low: small numbers look volatile by nature.",
    interpretation_guide={
        "cv": "< 0.3 stable, 0.3 .. 0.7 moderate, > 0.7 volatile",
        "spike_ratio": "> 5 means at least one strong spike",
    },
    limitations="Weekly seasonality (weekday vs weekend) adds some volatility to every subject.",
    inputs=[SUBJECTS_INPUT, PERIOD_INPUT],
    min_period=PeriodLength(28, PeriodUnit.DAYS),
    recommended_period="6-12 months",
    min_subjects=1,
    scope=MetricScope.PER_SUBJECT,
    unit="ratio",
    output={"value": {"cv": "float", "spike_ratio": "float", "spike_dates": "list[str]"}, "unit": "ratio"},
    data=[CURRENT],
    compute=_volatility_compute,
    # No spike_dominance: spikes are what this metric measures.
    checks=[check for check in GENERIC_CHECKS if check is not spike_dominance],
    interpret=_volatility_interpret,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verb(label: str, singular: str) -> str:
    """'Meal kits get', 'Food delivery gets': a plural-looking label drops the -s."""
    word = label.split()[-1].lower()
    plural = word.endswith("s") and not word.endswith("ss")
    return singular[:-1] if plural else singular


REGISTRY: dict[str, MetricDefinition] = {d.id: d for d in [INTEREST_VOLUME, GROWTH_RATE, TREND, VOLATILITY]}
