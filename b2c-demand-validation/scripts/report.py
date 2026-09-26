"""Report CLI: turns a metrics run and the model's text into a short PDF.

    python report.py schema
    python report.py build --results results.json --text report.json --out report.pdf

The model writes only the text (`report.json`). The results table, the setup
line and the reliability summary come from the metrics run output
(`results.json`), so no number in the PDF is typed by the model.

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
from report_contracts import MAX_BULLETS, REPORT_FIELDS, FieldKind, ReportText

EXIT_OK = 0
EXIT_INVALID_REPORT = 2

EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "report_text.json"
PROXY_NOTE = "Wikipedia pageviews are a proxy for attention, not for sales or purchase intent."


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def report_schema() -> dict[str, Any]:
    return {
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
            "results table (subject, metric, interpretation, reliability)",
            "setup line (project, period, subjects, baseline)",
            "reliability summary and the checks that did not pass",
            "generation date and data source note",
        ],
        "example": json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_report_text(raw: Any) -> tuple[ReportText | None, list[SpecError]]:
    """Validate the model's text. Returns the text, or every error found."""
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
    return value


def _parse_bullets(
    name: str, value: Any, max_chars: int, required: bool, errors: list[SpecError]
) -> list[str] | None:
    if not isinstance(value, list) or (required and not value):
        errors.append(SpecError(name, "invalid_type", "Use a non-empty list of strings."))
        return None
    if len(value) > MAX_BULLETS:
        errors.append(SpecError(name, "too_many_items", f"{len(value)} items; keep it to {MAX_BULLETS}."))
    return [b for i, item in enumerate(value) if (b := _parse_string(f"{name}[{i}]", item, max_chars, errors))]


def parse_results(raw: Any) -> list[SpecError]:
    """Check that the results are a successful `metrics.py run` report."""
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        status = raw.get("status") if isinstance(raw, dict) else None
        return [
            SpecError(
                "results",
                "not_a_metrics_report",
                f"Expected the output of a successful `metrics.py run` (status 'ok'); got status {status!r}.",
            )
        ]
    if not isinstance(raw.get("spec"), dict) or not isinstance(raw.get("results"), list) or not raw["results"]:
        return [SpecError("results", "not_a_metrics_report", "The metrics report has no spec or no results.")]
    return []


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


def _results_table(results: list[dict[str, Any]], s: dict[str, ParagraphStyle]) -> Table:
    rows: list[list[Flowable]] = [[_p(h, s["cell_head"]) for h in ("Subject", "Metric", "Result", "Reliability")]]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i, r in enumerate(results, start=1):
        level = r["reliability"]["level"]
        rows.append(
            [
                _p(r.get("subject") or "all subjects", s["cell"]),
                _p(r["metric"], s["cell"]),
                _p(r["interpretation"], s["cell"]),
                _p(level, s["cell"]),
            ]
        )
        style.append(("BACKGROUND", (3, i), (3, i), LEVEL_COLORS.get(level, colors.white)))
    table = Table(rows, colWidths=[32 * mm, 30 * mm, CONTENT_WIDTH - 84 * mm, 22 * mm], repeatRows=1)
    table.setStyle(TableStyle(style))
    return table


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


def _reliability_summary(report: dict[str, Any]) -> list[str]:
    counts = {level.value: 0 for level in ReliabilityLevel}
    lines = []
    for r in report["results"]:
        counts[r["reliability"]["level"]] += 1
        name = f"{r.get('subject') or 'all subjects'} / {r['metric']}"
        for c in r["reliability"]["checks"]:
            if c["status"] != CheckStatus.PASS:
                lines.append(f"{name}: {c['name']} {c['status']}: {c['detail']}")
    summary = ", ".join(f"{n} {level}" for level, n in counts.items() if n)
    return [f"Reliability of the results: {summary}.", *lines]


def render_pdf(text: ReportText, report: dict[str, Any], out: Path, today: date) -> int:
    """Write the PDF and return its page count."""
    s = _styles()
    spec = report["spec"]
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
        _p(f"Generated {today.isoformat()} · Data source: Wikipedia pageviews ({spec['project']})", s["meta"]),
        Spacer(1, 6),
        *_section("Problem", [_p(text.problem, s["body"])], s),
        *_section("Answer", [_answer_box(text.answer, s)], s),
        *_section("Data", [_results_table(report["results"], s), Spacer(1, 4), _p(_setup_line(spec), s["meta"])], s),
        *_section("Analysis", [_bullets(text.analysis, s["body"])], s),
        *_section("Conclusions", [_bullets(text.conclusions, s["body"])], s),
        *_section(
            "Trust",
            [_bullets(text.trust, s["body"]), Spacer(1, 4), *[_p(line, s["meta"]) for line in _reliability_summary(report)]],
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the report text fields and an example")
    build = commands.add_parser("build", help="build the PDF report")
    build.add_argument("--results", required=True, help="path to the `metrics.py run` output JSON")
    build.add_argument("--text", required=True, help="path to the report text JSON")
    build.add_argument("--out", required=True, help="path of the PDF to write")
    args = parser.parse_args(argv)

    if args.command == "schema":
        _print_json(report_schema())
        return EXIT_OK

    report, errors = _load_json(args.results, "results")
    if not errors:
        errors = parse_results(report)
    raw_text, text_errors = _load_json(args.text, "text")
    text = None
    if not text_errors:
        text, text_errors = parse_report_text(raw_text)
    errors += text_errors
    if errors or text is None:
        _print_json({"status": "invalid_report", "errors": [asdict(e) for e in errors]})
        return EXIT_INVALID_REPORT

    out = Path(args.out)
    pages = render_pdf(text, report, out, date.today())
    _print_json({"status": "ok", "path": str(out.resolve()), "pages": pages})
    return EXIT_OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
