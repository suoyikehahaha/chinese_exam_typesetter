"""Score accounting for the v0.3 exam workbench.

The calculator deliberately works on the editable dictionary model.  This
keeps score feedback available before a document is rendered and gives the UI
one deterministic definition of section subtotals and the fixed 150-point
target.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


TARGET_SCORE = Decimal("150")


@dataclass(frozen=True, slots=True)
class SectionScore:
    """Subtotal for one top-level exam section."""

    block_index: int | None
    title: str
    total: Decimal
    question_numbers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoreSummary:
    """Complete score state displayed by the editor."""

    total: Decimal
    target: Decimal
    missing_questions: tuple[str, ...]
    sections: tuple[SectionScore, ...]

    @property
    def difference(self) -> Decimal:
        """Return positive points remaining or negative points over target."""

        return self.target - self.total

    @property
    def complete(self) -> bool:
        """Whether every question has a score and the total is exactly 150."""

        return not self.missing_questions and self.total == self.target


def parse_score(value: Any) -> Decimal | None:
    """Parse an editor score without introducing binary floating-point drift."""

    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        score = Decimal(text)
    except InvalidOperation:
        return None
    if not score.is_finite() or score < 0:
        return None
    return score


def question_leaf_score(question: dict[str, Any]) -> Decimal | None:
    """Return the effective score of one numbered question.

    When explicit subquestion scores exist, they are the leaf values and their
    sum is used.  The parent question score remains a display declaration and
    is not counted a second time.
    """

    child_values = question.get("subquestion_scores")
    if isinstance(child_values, list) and child_values:
        parsed = [parse_score(value) for value in child_values]
        if all(value is not None for value in parsed):
            return sum((value for value in parsed if value is not None), Decimal("0"))
        return None
    return parse_score(question.get("score"))


def calculate_score_summary(raw_exam: dict[str, Any]) -> ScoreSummary:
    """Calculate question totals and top-level section subtotals."""

    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    missing: list[str] = []
    total = Decimal("0")

    for index, block in enumerate(raw_exam.get("blocks", [])):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "section_title":
            current = {
                "block_index": index,
                "title": str(block.get("text", "未命名大题")),
                "total": Decimal("0"),
                "numbers": [],
            }
            sections.append(current)
            continue
        if block.get("type") != "question":
            continue
        question = block.get("question", {})
        if not isinstance(question, dict):
            continue
        if current is None:
            current = {
                "block_index": None,
                "title": "未分组题目",
                "total": Decimal("0"),
                "numbers": [],
            }
            sections.append(current)
        number = str(question.get("number", "?"))
        score = question_leaf_score(question)
        if score is None:
            missing.append(number)
            current["numbers"].append(number)
            continue
        total += score
        current["total"] += score
        current["numbers"].append(number)

    result_sections = tuple(
        SectionScore(
            block_index=item["block_index"],
            title=item["title"],
            total=item["total"],
            question_numbers=tuple(item["numbers"]),
        )
        for item in sections
    )
    return ScoreSummary(total, TARGET_SCORE, tuple(missing), result_sections)


def format_score(value: Decimal) -> str:
    """Format a score without a redundant decimal suffix."""

    normalized = value.normalize()
    return format(normalized, "f")


__all__ = [
    "TARGET_SCORE",
    "ScoreSummary",
    "SectionScore",
    "calculate_score_summary",
    "format_score",
    "parse_score",
    "question_leaf_score",
]
