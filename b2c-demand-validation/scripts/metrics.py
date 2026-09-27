"""Metrics CLI: the catalog of metrics and the runner for an analysis spec.

    python metrics.py catalog [--format short|full]
    python metrics.py run --spec spec.json

The model reads the catalog, writes a spec, and runs it. The runner validates
the spec before fetching anything, resolves the data each metric declares,
computes the values, runs the reliability checks and prints a JSON report.

Exit codes: 0 on success, 2 on an invalid spec.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from data_resolver import DataResolver
from metrics_contracts import (
    AnalysisSpec,
    Baseline,
    CheckStatus,
    DataKind,
    DataRequirement,
    MetricData,
    MetricDefinition,
    MetricResult,
    MetricScope,
    ReliabilityCheck,
    ReliabilityLevel,
    SpecError,
    baseline_period,
)
from metrics_registry import REGISTRY
from reliability import aggregate
from resolver_contracts import Period, SeriesBundle, SeriesRequest, Subject
from wiki_client import WikiPageviewsClient
from wiki_contracts import PAGEVIEWS_MIN_DATE
from workspace import SPEC_FILE_PATTERN, part_key

EXIT_OK = 0
EXIT_INVALID_SPEC = 2


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def catalog_full(registry: dict[str, MetricDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "id": d.id,
            "title": d.title,
            "answers": d.answers,
            "explainer": d.explainer,
            "use_when": d.use_when,
            "do_not_use_when": d.do_not_use_when,
            "inputs": [{k: v for k, v in asdict(p).items() if v is not None} for p in d.inputs],
            "min_period": d.min_period.short(),
            "recommended_period": d.recommended_period,
            "min_subjects": d.min_subjects,
            "scope": d.scope,
            "cross_project_comparable": d.cross_project is not None,
            "output": d.output,
            "interpretation_guide": d.interpretation_guide,
            "limitations": d.limitations,
        }
        for d in registry.values()
    ]


def catalog_short(registry: dict[str, MetricDefinition]) -> str:
    """One line per metric: id, the question it answers, what it needs, and whether projects compare."""
    if not registry:
        return "No metrics registered yet."
    rows = []
    for d in registry.values():
        needs = [f">= {d.min_subjects} subjects" if d.min_subjects > 1 else "subjects"]
        needs.append(f"period (>= {d.min_period.label()})")
        extra = [p for p in d.inputs if p.name not in ("subjects", "period")]
        needs += [p.name if p.required else f"{p.name} (optional)" for p in extra]
        across = "comparable across projects" if d.cross_project else "within one project only"
        rows.append((d.id, d.answers, f"{', '.join(needs)} — {across}"))
    id_width = max(len(r[0]) for r in rows)
    answers_width = max(len(r[1]) for r in rows)
    return "\n".join(f"{i:<{id_width}} — {a:<{answers_width}} — needs: {n}" for i, a, n in rows)


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------


def parse_spec(
    raw: Any,
    registry: dict[str, MetricDefinition],
    today: date | None = None,
) -> tuple[AnalysisSpec | None, list[SpecError]]:
    """Validate the raw spec. Returns the spec, or every error found."""
    if not isinstance(raw, dict):
        return None, [SpecError("spec", "invalid_type", "The spec must be a JSON object.")]

    errors: list[SpecError] = []
    project = _parse_project(raw.get("project"), errors)
    subjects = _parse_subjects(raw.get("subjects"), errors)
    period = _parse_period(raw.get("period"), errors, today or date.today())
    baseline = _parse_baseline(raw.get("baseline"), errors)
    metric_ids = _parse_metrics(raw.get("metrics"), registry, errors)
    if baseline is None:
        baseline = _default_baseline(metric_ids, registry)

    # Per-metric constraints, only once the inputs they depend on are valid.
    for metric_id in metric_ids:
        definition = registry[metric_id]
        if period is not None and not definition.min_period.is_covered_by(period):
            errors.append(
                SpecError(
                    "period",
                    "period_too_short",
                    f"{metric_id!r} needs at least {definition.min_period.label()}; "
                    f"the period has {definition.min_period.measure(period)}.",
                )
            )
        if subjects is not None and len(subjects) < definition.min_subjects:
            errors.append(
                SpecError(
                    "subjects",
                    "not_enough_subjects",
                    f"{metric_id!r} needs at least {definition.min_subjects} subjects; got {len(subjects)}.",
                )
            )

    if subjects is not None and any(registry[m].scope == MetricScope.CROSS_SUBJECT for m in metric_ids):
        errors += _shared_articles(subjects)

    uses_baseline = any(r.kind == DataKind.BASELINE_SERIES for m in metric_ids for r in registry[m].data)
    if uses_baseline and period is not None and baseline is not None:
        compared = baseline_period(period, baseline)
        if compared.start < PAGEVIEWS_MIN_DATE:
            errors.append(
                SpecError(
                    "baseline",
                    "baseline_before_data",
                    f"The {baseline.value} baseline starts on {compared.start}, but pageviews data starts on "
                    f"{PAGEVIEWS_MIN_DATE}. Move the period later so that the baseline starts on or after that date.",
                )
            )

    if errors:
        return None, errors
    assert project is not None and subjects is not None and period is not None
    return AnalysisSpec(project=project, subjects=subjects, period=period, metrics=metric_ids, baseline=baseline), []


def _parse_project(value: Any, errors: list[SpecError]) -> str | None:
    if value is None:
        errors.append(SpecError("project", "missing_field", 'Add "project", e.g. "en.wikipedia".'))
    elif not isinstance(value, str) or not value.strip():
        errors.append(SpecError("project", "invalid_type", 'Use a non-empty string, e.g. "en.wikipedia".'))
    else:
        return value.strip()
    return None


def _parse_subjects(value: Any, errors: list[SpecError]) -> list[Subject] | None:
    example = '[{"label": "Meal kits", "articles": ["Meal_kit"]}]'
    if value is None:
        errors.append(SpecError("subjects", "missing_field", f'Add "subjects", e.g. {example}.'))
        return None
    if not isinstance(value, list) or not value:
        errors.append(SpecError("subjects", "invalid_type", f"Use a non-empty list, e.g. {example}."))
        return None

    subjects: list[Subject] = []
    seen: set[str] = set()
    count = len(errors)
    for i, item in enumerate(value):
        field = f"subjects[{i}]"
        if not isinstance(item, dict):
            errors.append(SpecError(field, "invalid_type", f"Each subject is an object, e.g. {example[1:-1]}."))
            continue
        label, articles = item.get("label"), item.get("articles")
        if not isinstance(label, str) or not label.strip():
            errors.append(SpecError(f"{field}.label", "missing_field", "Give the subject a non-empty label."))
            continue
        label = label.strip()
        if label in seen:
            errors.append(SpecError(f"{field}.label", "duplicate_subject", f"Label {label!r} is used twice."))
            continue
        seen.add(label)
        if not isinstance(articles, list) or not articles:
            errors.append(
                SpecError(
                    f"{field}.articles",
                    "empty_articles",
                    f"Subject {label!r} needs at least one article title, e.g. [\"Meal_kit\"].",
                )
            )
            continue
        if not all(isinstance(a, str) and a.strip() for a in articles):
            errors.append(SpecError(f"{field}.articles", "invalid_type", "Article titles are non-empty strings."))
            continue
        subjects.append(Subject(label=label, articles=[a.strip() for a in articles]))
    return subjects if len(errors) == count else None


def _shared_articles(subjects: list[Subject]) -> list[SpecError]:
    """Cross-subject metrics compare subjects, so an article in two subjects would be counted twice."""
    errors: list[SpecError] = []
    owner: dict[str, str] = {}
    for i, subject in enumerate(subjects):
        for article in subject.articles:
            key = article.replace(" ", "_")
            if key in owner and owner[key] != subject.label:
                errors.append(
                    SpecError(
                        f"subjects[{i}].articles",
                        "shared_article",
                        f"{article!r} is in both {owner[key]!r} and {subject.label!r}; its views would be "
                        "counted twice. Keep it in one subject.",
                    )
                )
            owner.setdefault(key, subject.label)
    return errors


def _parse_period(value: Any, errors: list[SpecError], today: date) -> Period | None:
    example = '{"start": "2025-09-01", "end": "2026-08-31"}'
    if value is None:
        errors.append(SpecError("period", "missing_field", f'Add "period", e.g. {example}.'))
        return None
    if not isinstance(value, dict):
        errors.append(SpecError("period", "invalid_type", f"Use an object, e.g. {example}."))
        return None

    days: dict[str, date] = {}
    for key in ("start", "end"):
        raw = value.get(key)
        try:
            days[key] = date.fromisoformat(raw) if isinstance(raw, str) and len(raw) == 10 else None
        except ValueError:
            days[key] = None
        if days[key] is None:
            errors.append(SpecError(f"period.{key}", "invalid_date", f"Use an ISO date YYYY-MM-DD; got {raw!r}."))
    start, end = days["start"], days["end"]
    if start is None or end is None:
        return None
    if start > end:
        errors.append(SpecError("period", "period_reversed", f"start {start} is after end {end}."))
        return None
    if start < date.fromisoformat(PAGEVIEWS_MIN_DATE):
        errors.append(
            SpecError("period.start", "period_before_data", f"Pageviews data starts on {PAGEVIEWS_MIN_DATE}.")
        )
        return None
    if end >= today:
        errors.append(
            SpecError(
                "period.end",
                "period_in_future",
                f"end {end} has no data yet; end the period before today ({today}).",
            )
        )
        return None
    return Period(start=start.isoformat(), end=end.isoformat())


def _parse_baseline(value: Any, errors: list[SpecError]) -> Baseline | None:
    if value is None:
        return None
    try:
        return Baseline(value)
    except ValueError:
        allowed = ", ".join(repr(b.value) for b in Baseline)
        errors.append(SpecError("baseline", "invalid_baseline", f"Use one of {allowed}; got {value!r}."))
        return None


def _default_baseline(metric_ids: list[str], registry: dict[str, MetricDefinition]) -> Baseline | None:
    """The default of the first metric that takes a baseline input, if any."""
    for metric_id in metric_ids:
        for param in registry[metric_id].inputs:
            if param.name == "baseline" and param.default is not None:
                return Baseline(param.default)
    return None


def _parse_metrics(value: Any, registry: dict[str, MetricDefinition], errors: list[SpecError]) -> list[str]:
    """Return the known metric ids; unknown and duplicate ids become errors."""
    if value is None:
        errors.append(SpecError("metrics", "missing_field", 'Add "metrics": a list of ids from the catalog.'))
        return []
    if not isinstance(value, list) or not value:
        errors.append(SpecError("metrics", "invalid_type", "Use a non-empty list of ids from the catalog."))
        return []

    known: list[str] = []
    for i, metric_id in enumerate(value):
        field = f"metrics[{i}]"
        if metric_id in known:
            errors.append(SpecError(field, "duplicate_metric", f"{metric_id!r} is listed twice."))
        elif isinstance(metric_id, str) and metric_id in registry:
            known.append(metric_id)
        else:
            errors.append(SpecError(field, "unknown_metric", _unknown_metric_detail(metric_id, registry)))
    return known


def _unknown_metric_detail(metric_id: Any, registry: dict[str, MetricDefinition]) -> str:
    detail = f"Unknown metric {metric_id!r}."
    close = difflib.get_close_matches(str(metric_id), registry, n=1)
    if close:
        return f"{detail} Did you mean {close[0]!r}?"
    available = ", ".join(repr(m) for m in registry) or "none yet"
    return f"{detail} Available metrics: {available}."


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class MetricsRunner:
    """Resolves the data each metric declares, computes it and grades it."""

    def __init__(self, resolver: DataResolver, registry: dict[str, MetricDefinition]) -> None:
        self.resolver = resolver
        self.registry = registry
        # Bundles fetched during this run, so metrics sharing a series fetch it once.
        self._fetched: dict[tuple[DataRequirement, str], SeriesBundle] = {}

    def run(self, spec: AnalysisSpec) -> list[MetricResult]:
        results = []
        for metric_id in spec.metrics:
            definition = self.registry[metric_id]
            if definition.scope == MetricScope.PER_SUBJECT:
                groups = [[subject] for subject in spec.subjects]
            else:
                groups = [spec.subjects]
            for subjects in groups:
                bundles = {
                    requirement: {s.label: self._resolve(spec, requirement, s) for s in subjects}
                    for requirement in definition.data
                }
                results.append(_evaluate(definition, MetricData(spec=spec, subjects=subjects, bundles=bundles)))
        return results

    def _resolve(self, spec: AnalysisSpec, requirement: DataRequirement, subject: Subject) -> SeriesBundle:
        key = (requirement, subject.label)
        if key not in self._fetched:
            self._fetched[key] = self._fetch(spec, requirement, subject)
        return self._fetched[key]

    def _fetch(self, spec: AnalysisSpec, requirement: DataRequirement, subject: Subject) -> SeriesBundle:
        # Metric steps add a branch here for each new DataKind they introduce.
        if requirement.kind == DataKind.CURRENT:
            return self.resolver.get_subject_series(
                SeriesRequest(
                    project=spec.project,
                    subject=subject,
                    period=spec.period,
                    access=requirement.access,
                    granularity=requirement.granularity,
                )
            )
        if requirement.kind == DataKind.BASELINE_SERIES:
            return self.resolver.get_subject_series(
                SeriesRequest(
                    project=spec.project,
                    subject=subject,
                    period=baseline_period(spec.period, spec.baseline or Baseline.PREVIOUS_PERIOD),
                    access=requirement.access,
                    granularity=requirement.granularity,
                )
            )
        raise NotImplementedError(f"The runner cannot resolve data kind {requirement.kind!r} yet")


def _evaluate(definition: MetricDefinition, data: MetricData) -> MetricResult:
    """Run the checks first; compute and interpret only if the data is usable."""
    checks = [check(data) for check in definition.checks]
    reliability = aggregate(checks)
    value = None
    if reliability.level != ReliabilityLevel.INVALID:
        try:
            value = definition.compute(data)
        except Exception as exc:  # a compute bug must not hide the other results
            checks.append(ReliabilityCheck("compute", CheckStatus.FAIL, f"{type(exc).__name__}: {exc}"))
            reliability = aggregate(checks)

    if reliability.level == ReliabilityLevel.INVALID:
        failed = "; ".join(f"{c.name}: {c.detail}" for c in checks if c.status == CheckStatus.FAIL)
        interpretation = f"Not computed, the data is not reliable enough ({failed})."
        value = None
    else:
        interpretation = definition.interpret(value, data, reliability)

    subject = data.subjects[0].label if definition.scope == MetricScope.PER_SUBJECT else None
    return MetricResult(
        metric=definition.id,
        subject=subject,
        value=value,
        unit=definition.unit,
        interpretation=interpretation,
        reliability=reliability,
    )


def build_report(spec: AnalysisSpec, results: list[MetricResult]) -> dict[str, Any]:
    summary: dict[str, list[str]] = {level.value: [] for level in ReliabilityLevel}
    for result in results:
        name = f"{result.metric}/{result.subject}" if result.subject is not None else result.metric
        summary[result.reliability.level].append(name)
    return {
        "status": "ok",
        "spec": {
            "project": spec.project,
            "subjects": [s.label for s in spec.subjects],
            "period": asdict(spec.period),
            "baseline": spec.baseline,
            "baseline_period": asdict(baseline_period(spec.period, spec.baseline)) if spec.baseline else None,
        },
        "results": [asdict(result) for result in results],
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_spec(path: str) -> tuple[Any, list[SpecError]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return None, [SpecError("spec", "file_not_found", f"No spec file at {path!r}.")]
    except json.JSONDecodeError as exc:
        return None, [SpecError("spec", "invalid_json", f"The spec is not valid JSON: {exc}.")]


def check_part(path: str, raw: Any) -> list[SpecError]:
    """A spec at a part's path (`spec.<key>.json`) must be for that part's project."""
    match = SPEC_FILE_PATTERN.match(Path(path).name)
    project = raw.get("project") if isinstance(raw, dict) else None
    if match is None or not isinstance(project, str) or part_key(project.strip()) == match["key"]:
        return []
    return [
        SpecError(
            "project",
            "project_mismatch",
            f"This file is the spec of the {'.'.join(match['key'].rsplit('-', 1))} part, but its project is "
            f"{project!r}. Write each project's spec to its own path from workspace.py show.",
        )
    ]


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="list the available metrics")
    catalog.add_argument("--format", choices=["short", "full"], default="full")
    run = commands.add_parser("run", help="run the metrics of a spec and print the report")
    run.add_argument("--spec", required=True, help="path to the spec JSON file")
    args = parser.parse_args(argv)

    if args.command == "catalog":
        if args.format == "short":
            print(catalog_short(REGISTRY))
        else:
            _print_json(catalog_full(REGISTRY))
        return EXIT_OK

    raw, errors = _load_spec(args.spec)
    spec = None
    if not errors:
        errors = check_part(args.spec, raw)
    if not errors:
        spec, errors = parse_spec(raw, REGISTRY)
    if errors or spec is None:
        _print_json({"status": "invalid_spec", "errors": [asdict(e) for e in errors]})
        return EXIT_INVALID_SPEC

    runner = MetricsRunner(DataResolver(WikiPageviewsClient()), REGISTRY)
    _print_json(build_report(spec, runner.run(spec)))
    return EXIT_OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
