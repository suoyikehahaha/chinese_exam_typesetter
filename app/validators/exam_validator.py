"""结构、题号和分值校验。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.models import ExamDocument


@dataclass(slots=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


def _validate_base(exam: ExamDocument) -> list[ValidationIssue]:
    """返回试卷问题列表。"""

    issues: list[ValidationIssue] = []
    questions = [block.question for block in exam.blocks if block.type == "question" and block.question]
    numbers = [question.number for question in questions]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        issues.append(
            ValidationIssue("error", "question-number-sequence", f"题号应为 {expected}，实际为 {numbers}")
        )

    score_sum = sum(question.score or 0 for question in questions)
    if abs(score_sum - exam.metadata.total_score) > 0.001:
        issues.append(
            ValidationIssue(
                "warning",
                "score-total",
                f"题目分值合计 {score_sum:g}，卷首总分 {exam.metadata.total_score:g}",
            )
        )

    for question in questions:
        if question.kind == "objective" and len(question.options) != 4:
            issues.append(
                ValidationIssue(
                    "error",
                    "objective-option-count",
                    f"第 {question.number} 题为客观题，但选项数量为 {len(question.options)}",
                )
            )
        if question.segmentation_text:
            markers = [char for char in question.segmentation_text if char in "ABCDEFGH"]
            if markers != list("ABCDEFGH"):
                issues.append(
                    ValidationIssue(
                        "error",
                        "segmentation-markers",
                        f"第 {question.number} 题断句标记应按 A 至 H 连续出现",
                    )
                )
    return issues


def validate_exam(exam: ExamDocument) -> list[ValidationIssue]:
    """Validate flexible real-world exams and keep marker gaps non-blocking."""

    issues = [
        issue
        for issue in _validate_base(exam)
        if issue.code != "segmentation-markers"
    ]
    questions = [
        block.question
        for block in exam.blocks
        if block.type == "question" and block.question
    ]
    for question in questions:
        if not question.segmentation_text:
            continue
        markers = re.findall(r"[A-Z]", question.segmentation_text)
        if len(markers) < 2:
            issues.append(
                ValidationIssue(
                    "warning",
                    "segmentation-marker-count",
                    f"第 {question.number} 题断句标记较少，请核对原文",
                )
            )
            continue
        positions = [ord(marker) for marker in markers]
        if len(set(markers)) != len(markers) or positions != sorted(positions):
            issues.append(
                ValidationIssue(
                    "warning",
                    "segmentation-marker-order",
                    f"第 {question.number} 题断句标记顺序特殊，已按原文保留",
                )
            )
            continue
        expected = [chr(code) for code in range(positions[0], positions[-1] + 1)]
        missing = [marker for marker in expected if marker not in markers]
        if missing:
            issues.append(
                ValidationIssue(
                    "warning",
                    "segmentation-marker-gap",
                    f"第 {question.number} 题断句标记缺少{'、'.join(missing)}，已按原文保留",
                )
            )
    return issues


__all__ = ["ValidationIssue", "validate_exam"]
