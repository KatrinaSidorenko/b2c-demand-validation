"""Contracts for the PDF report: the text fields the model fills and their limits.

The numbers in the report never come from here: the report tool takes them from
the metrics run output. These fields hold only the model's wording.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

TITLE_MAX_CHARS = 80
PARAGRAPH_MAX_CHARS = 400
BULLET_MAX_CHARS = 300
MAX_BULLETS = 5


class FieldKind(StrEnum):
    LINE = "line"
    PARAGRAPH = "paragraph"
    BULLETS = "bullets"


@dataclass(frozen=True)
class TextField:
    name: str
    kind: FieldKind
    required: bool
    describes: str

    @property
    def max_chars(self) -> int:
        return {
            FieldKind.LINE: TITLE_MAX_CHARS,
            FieldKind.PARAGRAPH: PARAGRAPH_MAX_CHARS,
            FieldKind.BULLETS: BULLET_MAX_CHARS,
        }[self.kind]


REPORT_FIELDS: list[TextField] = [
    TextField("title", FieldKind.LINE, True, "Report title, e.g. 'Demand check: meal kits'."),
    TextField("problem", FieldKind.PARAGRAPH, True, "The user's question and the decision behind it."),
    TextField("answer", FieldKind.PARAGRAPH, True, "The short answer, with the reliability level."),
    TextField("analysis", FieldKind.BULLETS, True, "What the results show, built on each result's interpretation."),
    TextField("conclusions", FieldKind.BULLETS, True, "What the results mean for the user's goal."),
    TextField(
        "trust",
        FieldKind.BULLETS,
        True,
        "How far the results can be trusted: reliability levels, failed or warning checks, the data being a proxy.",
    ),
    TextField("recommendations", FieldKind.BULLETS, True, "Next steps for the user, each tied to the goal."),
    TextField("not_measured", FieldKind.BULLETS, False, "Parts of the question no available metric could measure."),
]


@dataclass(frozen=True)
class ReportText:
    title: str
    problem: str
    answer: str
    analysis: list[str]
    conclusions: list[str]
    trust: list[str]
    recommendations: list[str]
    not_measured: list[str] = field(default_factory=list)
