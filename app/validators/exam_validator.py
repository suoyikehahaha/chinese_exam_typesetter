"""结构、题号和分值校验。"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import ExamDocument


@dataclass(slots=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


def validate_exam(exam: ExamDocument) -> list[ValidationIssue]:
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
