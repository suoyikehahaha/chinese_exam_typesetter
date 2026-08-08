"""Adaptive exam validation for real-world source variation."""

from __future__ import annotations

import re

from app.models import ExamDocument

from .exam_validator import ValidationIssue, validate_exam as validate_exam_v1


def validate_exam(exam: ExamDocument) -> list[ValidationIssue]:
    """Keep structural errors actionable and treat marker gaps as warnings."""

    issues = [
        issue
        for issue in validate_exam_v1(exam)
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
        expected = [
            chr(code)
            for code in range(positions[0], positions[-1] + 1)
        ]
        missing = [marker for marker in expected if marker not in markers]
        if missing:
            issues.append(
                ValidationIssue(
                    "warning",
                    "segmentation-marker-gap",
                    f"第 {question.number} 题断句标记缺少"
                    f"{'、'.join(missing)}，已按原文保留",
                )
            )
    return issues


__all__ = ["ValidationIssue", "validate_exam"]
