"""Report CLI: turns metrics runs and the model's text into a short PDF.

    python report.py schema
    python report.py build --id <analysis id> [--root analyses]
    python report.py build --results a.json [b.json ...] --text report.json --out report.pdf

The model writes only the text (`report.json`). The results table, the setup
line, the cross-project comparison and the reliability summary come from the
metrics run outputs, one per project, so no number in the PDF is typed by the
model. With `--id`, every project part of the analysis is collected from its
folder.

Exit codes: 0 when the PDF is written, 2 on invalid input (nothing is written).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from metrics_contracts import CheckStatus, ReliabilityLevel, SpecError
from metrics_registry import REGISTRY
from report_contracts import MAX_BULLETS, REPORT_FIELDS, FieldKind, ReportText
from workspace import DEFAULT_ROOT, FILES, META_FILE, analysis_folder, list_parts, read_json

EXIT_OK = 0
EXIT_INVALID_REPORT = 2

EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "report_text.json"
PROXY_NOTE = "Wikipedia pageviews are a proxy for attention, not for sales or purchase intent."
LEVELS_NOTE = (
    "Reliability: high and medium results can be relied on; low is a weak signal; "
    "invalid means the metric could not be measured."
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def report_schema() -> dict[str, Any]:
    return {
        "language": (
            "English only, for a reader who is not an analyst. The PDF font cannot show Cyrillic, Greek or CJK "
            "letters: translate or transliterate names."
        ),
        "fields": [
            {
                "name": f.name,
                "kind": f.kind,
                "required": f.required,
                "max_chars": f.max_chars,
                **({"max_items": MAX_BULLETS} if f.kind == FieldKind.BULLETS else {}),
                "describes": f.describes,
            }
            for f in REPORT_FIELDS
        ],
        "added_by_the_tool": [
            "results table (subject, metric title, interpretation, reliability)",
            "a plain-language explanation of each metric used and of the reliability levels",
            "setup line per project (project, period, subjects, baseline)",
            "with several projects: a comparison table of the metrics comparable across projects",
            "reliability summary and the checks that did not pass",
            "generation date and data source note",
        ],
        "example": json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_report_text(raw: Any, projects: int = 1) -> tuple[ReportText | None, list[SpecError]]:
    """Validate the model's text for a report over `projects` projects. Returns the text, or every error found."""
    if not isinstance(raw, dict):
        return None, [SpecError("text", "invalid_type", "The report text must be a JSON object.")]

    errors: list[SpecError] = []
    known = {f.name for f in REPORT_FIELDS}
    for name in raw:
        if name not in known:
            errors.append(SpecError(name, "unknown_field", f"Unknown field. Allowed: {', '.join(sorted(known))}."))

    values: dict[str, Any] = {}
    for f in REPORT_FIELDS:
        value = raw.get(f.name)
        if value is None:
            if f.required:
                errors.append(SpecError(f.name, "missing_field", f'Add "{f.name}": {f.describes}'))
            continue
        if f.kind == FieldKind.BULLETS:
            values[f.name] = _parse_bullets(f.name, value, f.max_chars, f.required, errors)
        else:
            values[f.name] = _parse_string(f.name, value, f.max_chars, errors)
    if projects > 1 and not raw.get("comparison"):
        errors.append(
            SpecError(
                "comparison",
                "missing_field",
                f'The analysis covers {projects} projects. Add "comparison": how they differ, '
                "using only metrics comparable across projects.",
            )
        )

    if errors:
        return None, errors
    return ReportText(**values), []


def _parse_string(name: str, value: Any, max_chars: int, errors: list[SpecError]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(SpecError(name, "invalid_type", "Use a non-empty string."))
        return None
    value = value.strip()
    if len(value) > max_chars:
        errors.append(SpecError(name, "too_long", f"{len(value)} characters; keep it to {max_chars}."))
    if bad := _unrenderable(value):
        errors.append(
            SpecError(
                name,
                "non_latin_text",
                f"The PDF font cannot show {bad!r}. Write the report in English; translate or transliterate names.",
            )
        )
    return value


def _unrenderable(text: str) -> str | None:
    """The first character the built-in PDF font cannot show, or None.

    Helvetica covers the WinAnsi (cp1252) set only: no Cyrillic, Greek or CJK.
    """
    for char in text:
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            return char
    return None


def _parse_bullets(
    name: str, value: Any, max_chars: int, required: bool, errors: list[SpecError]
) -> list[str] | None:
    if not isinstance(value, list) or (required and not value):
        errors.append(SpecError(name, "invalid_type", "Use a non-empty list of strings."))
        return None
    if len(value) > MAX_BULLETS:
        errors.append(SpecError(name, "too_many_items", f"{len(value)} items; keep it to {MAX_BULLETS}."))
    return [b for i, item in enumerate(value) if (b := _parse_string(f"{name}[{i}]", item, max_chars, errors))]


def parse_results(raw: Any, field: str = "results") -> list[SpecError]:
    """Check that the results are a successful `metrics.py run` report."""
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        status = raw.get("status") if isinstance(raw, dict) else None
        return [
            SpecError(
                field,
                "not_a_metrics_report",
                f"Expected the output of a successful `metrics.py run` (status 'ok'); got status {status!r}.",
            )
        ]
    if not isinstance(raw.get("spec"), dict) or not isinstance(raw.get("results"), list) or not raw["results"]:
        return [SpecError(field, "not_a_metrics_report", "The metrics report has no spec or no results.")]
    # Labels are printed in the table, the setup line and the interpretations.
    return [
        SpecError(
            f"{field}.spec.subjects[{i}]",
            "non_latin_text",
            f"The PDF font cannot show {bad!r} in the label {label!r}. "
            "Relabel the subject in English in the project's spec and run metrics.py again.",
        )
        for i, label in enumerate(raw["spec"].get("subjects") or [])
        if isinstance(label, str) and (bad := _unrenderable(label))
    ]


def check_parts(reports: list[dict[str, Any]]) -> list[SpecError]:
    """Reports of several projects must be distinct projects over the same period and baseline."""
    errors: list[SpecError] = []
    first = reports[0]["spec"]
    seen: set[str] = set()
    for report in reports:
        spec = report["spec"]
        if spec["project"] in seen:
            errors.append(
                SpecError("results", "duplicate_project", f"{spec['project']} is given twice; pass each project once.")
            )
        seen.add(spec["project"])
        for key in ("period", "baseline"):
            if spec.get(key) != first.get(key):
                errors.append(
                    SpecError(
                        f"{spec['project']}.{key}",
                        f"{key}_mismatch",
                        f"{spec['project']} uses {key} {spec.get(key)!r} but {first['project']} uses "
                        f"{first.get(key)!r}. Use the same {key} in every project's spec and run them again.",
                    )
                )
    return errors


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

LEVEL_COLORS = {
    ReliabilityLevel.HIGH: colors.HexColor("#d7f0dd"),
    ReliabilityLevel.MEDIUM: colors.HexColor("#fdf1c7"),
    ReliabilityLevel.LOW: colors.HexColor("#fde0c2"),
    ReliabilityLevel.INVALID: colors.HexColor("#f6d0d0"),
}
ACCENT = colors.HexColor("#1f4e79")
MUTED = colors.HexColor("#555555")
CONTENT_WIDTH = A4[0] - 40 * mm


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontSize=10, leading=13, spaceBefore=0, spaceAfter=2)
    return {
        "title": ParagraphStyle("title", parent=base["Title"], alignment=0, fontSize=20, leading=24, spaceAfter=4),
        "meta": ParagraphStyle("meta", parent=body, fontSize=8.5, textColor=MUTED),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=12.5, textColor=ACCENT, spaceBefore=8, spaceAfter=3
        ),
        "body": body,
        "cell": ParagraphStyle("cell", parent=body, fontSize=9, leading=11.5),
        "cell_head": ParagraphStyle("cell_head", parent=body, fontSize=9, leading=11.5, fontName="Helvetica-Bold"),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def _bullets(items: list[str], style: ParagraphStyle) -> ListFlowable:
    return ListFlowable(
        [ListItem(_p(item, style), leftIndent=12) for item in items],
        bulletType="bullet",
        start="•",
        leftIndent=12,
        bulletFontSize=9,
    )


def _section(heading: str, content: list[Flowable], s: dict[str, ParagraphStyle]) -> list[Flowable]:
    # Keep a heading on the same page as the start of its content.
    return [KeepTogether([_p(heading, s["h2"]), content[0]]), *content[1:]]


def _answer_box(answer: str, s: dict[str, ParagraphStyle]) -> Table:
    box = Table([[_p(answer, s["body"])]], colWidths=[CONTENT_WIDTH])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eaf1f8")),
                ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return box


def _metric_title(metric_id: str) -> str:
    definition = REGISTRY.get(metric_id)
    return definition.title if definition else metric_id


def _metric_guide(results: list[dict[str, Any]]) -> list[str]:
    """One plain-language line per metric in the results, then the reliability levels."""
    lines = []
    for metric_id in dict.fromkeys(r["metric"] for r in results):
        definition = REGISTRY.get(metric_id)
        if definition:
            lines.append(f"{definition.title}: {definition.explainer}")
    return [*lines, LEVELS_NOTE]


TABLE_STYLE = [
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
]


def _results_table(reports: list[dict[str, Any]], s: dict[str, ParagraphStyle]) -> Table:
    """Every result; with several projects, a Project column comes first."""
    multi = len(reports) > 1
    head = ("Project",) * multi + ("Subject", "Metric", "Result", "Reliability")
    rows: list[list[Flowable]] = [[_p(h, s["cell_head"]) for h in head]]
    style = list(TABLE_STYLE)
    level_col = len(head) - 1
    for report in reports:
        for r in report["results"]:
            level = r["reliability"]["level"]
            rows.append(
                [
                    *[_p(report["spec"]["project"], s["cell"])] * multi,
                    _p(r.get("subject") or "all subjects", s["cell"]),
                    _p(_metric_title(r["metric"]), s["cell"]),
                    _p(r["interpretation"], s["cell"]),
                    _p(level, s["cell"]),
                ]
            )
            i = len(rows) - 1
            style.append(("BACKGROUND", (level_col, i), (level_col, i), LEVEL_COLORS.get(level, colors.white)))
    if multi:
        widths = [24 * mm, 26 * mm, 24 * mm, CONTENT_WIDTH - 94 * mm, 20 * mm]
    else:
        widths = [32 * mm, 30 * mm, CONTENT_WIDTH - 84 * mm, 22 * mm]
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(style))
    return table


def _comparison(reports: list[dict[str, Any]], s: dict[str, ParagraphStyle]) -> list[Flowable]:
    """Subjects x metrics comparable across projects, one column per project, and a note on the rest."""
    projects = [r["spec"]["project"] for r in reports]
    by_key = {
        (report["spec"]["project"], r.get("subject") or "all subjects", r["metric"]): r
        for report in reports
        for r in report["results"]
    }
    metric_ids = list(dict.fromkeys(r["metric"] for report in reports for r in report["results"]))
    subjects = list(dict.fromkeys(r.get("subject") or "all subjects" for report in reports for r in report["results"]))
    comparable = [m for m in metric_ids if (d := REGISTRY.get(m)) is not None and d.cross_project is not None]
    within = [_metric_title(m) for m in metric_ids if m not in comparable]

    flowables: list[Flowable] = []
    if comparable:
        rows: list[list[Flowable]] = [[_p(h, s["cell_head"]) for h in ("Subject", "Metric", *projects)]]
        style = list(TABLE_STYLE)
        for subject in subjects:
            for metric_id in comparable:
                cells = []
                for col, project in enumerate(projects, start=2):
                    r = by_key.get((project, subject, metric_id))
                    if r is None:
                        text, level = "not run", None
                    elif r["value"] is None:
                        text, level = "not measured", r["reliability"]["level"]
                    else:
                        level = r["reliability"]["level"]
                        text = f"{REGISTRY[metric_id].cross_project(r['value'])} · {level}"
                    cells.append(_p(text, s["cell"]))
                    if level is not None:
                        i = len(rows)
                        style.append(("BACKGROUND", (col, i), (col, i), LEVEL_COLORS.get(level, colors.white)))
                rows.append([_p(subject, s["cell"]), _p(_metric_title(metric_id), s["cell"]), *cells])
        column = (CONTENT_WIDTH - 54 * mm) / len(projects)
        table = Table(rows, colWidths=[28 * mm, 26 * mm, *[column] * len(projects)], repeatRows=1)
        table.setStyle(TableStyle(style))
        flowables.append(table)
    else:
        flowables.append(_p("None of the metrics used can be compared across projects.", s["meta"]))
    if within:
        flowables += [
            Spacer(1, 4),
            _p(
                f"Not compared across projects: {', '.join(within)}. Absolute view counts depend on each "
                "language's audience size, so compare them only within one project (see the Data table).",
                s["meta"],
            ),
        ]
    return flowables


def _setup_line(spec: dict[str, Any]) -> str:
    period = spec["period"]
    parts = [
        f"Project {spec['project']}",
        f"period {period['start']} to {period['end']}",
        f"subjects: {', '.join(spec['subjects'])}",
    ]
    if spec.get("baseline"):
        parts.append(f"baseline {spec['baseline']}")
    return "Setup: " + "; ".join(parts) + "."


def _reliability_summary(reports: list[dict[str, Any]]) -> list[str]:
    counts = {level.value: 0 for level in ReliabilityLevel}
    lines = []
    multi = len(reports) > 1
    for report, r in ((report, r) for report in reports for r in report["results"]):
        counts[r["reliability"]["level"]] += 1
        name = f"{r.get('subject') or 'all subjects'} / {_metric_title(r['metric'])}"
        if multi:
            name = f"{report['spec']['project']}: {name}"
        for c in r["reliability"]["checks"]:
            if c["status"] != CheckStatus.PASS:
                # Details can name non-English article titles, which the PDF font cannot show.
                detail = "details in results.json" if _unrenderable(c["detail"]) else c["detail"]
                lines.append(f"{name}: {c['name']} {c['status']}: {detail}")
    summary = ", ".join(f"{n} {level}" for level, n in counts.items() if n)
    return [f"Reliability of the results: {summary}.", *lines]


def render_pdf(text: ReportText, reports: list[dict[str, Any]], out: Path, today: date) -> int:
    """Write the PDF over one report per project and return its page count."""
    s = _styles()
    results = [r for report in reports for r in report["results"]]
    projects = ", ".join(report["spec"]["project"] for report in reports)
    pages = [0]

    def footer(canvas: Any, doc: Any) -> None:
        pages[0] = doc.page
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 12 * mm, PROXY_NOTE)
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {doc.page}")
        canvas.restoreState()

    story: list[Flowable] = [
        _p(text.title, s["title"]),
        _p(f"Generated {today.isoformat()} · Data source: Wikipedia pageviews ({projects})", s["meta"]),
        Spacer(1, 6),
        *_section("Problem", [_p(text.problem, s["body"])], s),
        *_section("Answer", [_answer_box(text.answer, s)], s),
        *_section(
            "Data",
            [
                _results_table(reports, s),
                Spacer(1, 4),
                *[_p(_setup_line(report["spec"]), s["meta"]) for report in reports],
                Spacer(1, 4),
                _p("How to read the metrics", s["cell_head"]),
                *[_p(line, s["meta"]) for line in _metric_guide(results)],
            ],
            s,
        ),
    ]
    if len(reports) > 1:
        comparison = _comparison(reports, s)
        if text.comparison:
            comparison += [Spacer(1, 4), _bullets(text.comparison, s["body"])]
        story += _section("Comparison across projects", comparison, s)
    story += [
        *_section("Analysis", [_bullets(text.analysis, s["body"])], s),
        *_section("Conclusions", [_bullets(text.conclusions, s["body"])], s),
        *_section(
            "Trust",
            [_bullets(text.trust, s["body"]), Spacer(1, 4), *[_p(line, s["meta"]) for line in _reliability_summary(reports)]],
            s,
        ),
        *_section("Recommendations", [_bullets(text.recommendations, s["body"])], s),
    ]
    if text.not_measured:
        story += _section("Not measured yet", [_bullets(text.not_measured, s["body"])], s)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=text.title,
        author="b2c-demand-validation",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return pages[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_json(path: str, field: str) -> tuple[Any, list[SpecError]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig")), []
    except FileNotFoundError:
        return None, [SpecError(field, "file_not_found", f"No file at {path!r}.")]
    except json.JSONDecodeError as exc:
        return None, [SpecError(field, "invalid_json", f"Not valid JSON: {exc}.")]


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _result_sources(args: argparse.Namespace) -> tuple[list[tuple[str, str]], list[SpecError]]:
    """`(field, path)` of each project's results; with --id also fills in --text and --out."""
    if args.results:
        errors = [
            SpecError(name, "missing_field", f"Give --{name} with --results, or build by --id.")
            for name in ("text", "out")
            if not getattr(args, name)
        ]
        return [(f"results[{i}]", path) for i, path in enumerate(args.results)], errors

    root = Path(args.root)
    folder = analysis_folder(root, args.id)
    if folder is None:
        return [], [SpecError("id", "unknown_id", f"No analysis {args.id!r} in {root}.")]
    args.text = args.text or str(folder / FILES["text"])
    args.out = args.out or str(folder / FILES["pdf"])
    sources, errors = [], []
    parts = list_parts(folder, read_json(folder / META_FILE))
    for part in parts:
        spec, results = part["files"]["spec"], part["files"]["results"]
        field = f"results.{part['project'] or part['key']}"
        if not results.is_file():
            todo = f"run `metrics.py run --spec {spec} > {results}`" if spec.is_file() else f"write its spec to {spec}"
            errors.append(SpecError(field, "part_not_run", f"No results for {part['project']} yet: {todo}."))
            continue
        sources.append((field, str(results)))
    if not parts:
        errors.append(SpecError("id", "no_parts", "The analysis has no projects; add one with workspace.py part."))
    return sources, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the report text fields and an example")
    build = commands.add_parser("build", help="build the PDF report")
    source = build.add_mutually_exclusive_group(required=True)
    source.add_argument("--id", help="analysis id: collect every project's results from its folder")
    source.add_argument("--results", nargs="+", help="paths to `metrics.py run` outputs, one per project")
    build.add_argument("--root", default=DEFAULT_ROOT, help="folder that holds all analyses (with --id)")
    build.add_argument("--text", help="path to the report text JSON (default with --id: the analysis's)")
    build.add_argument("--out", help="path of the PDF to write (default with --id: the analysis's)")
    args = parser.parse_args(argv)

    if args.command == "schema":
        _print_json(report_schema())
        return EXIT_OK

    sources, errors = _result_sources(args)
    reports = []
    for field, path in sources:
        report, load_errors = _load_json(path, field)
        load_errors = load_errors or parse_results(report, field)
        errors += load_errors
        if not load_errors:
            reports.append(report)
    if reports and not errors:
        errors = check_parts(reports)
    text = None
    if args.text:
        raw_text, text_errors = _load_json(args.text, "text")
        if not text_errors:
            text, text_errors = parse_report_text(raw_text, projects=max(len(sources), 1))
        errors += text_errors
    if errors or text is None:
        _print_json({"status": "invalid_report", "errors": [asdict(e) for e in errors]})
        return EXIT_INVALID_REPORT

    out = Path(args.out)
    pages = render_pdf(text, reports, out, date.today())
    _print_json({"status": "ok", "path": str(out.resolve()), "pages": pages})
    return EXIT_OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
