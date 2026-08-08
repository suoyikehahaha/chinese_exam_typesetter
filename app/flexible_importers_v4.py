"""灵活导入器第四版，识别断句文本和题内分题。"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .flexible_importers_v3 import import_exam as import_exam_v3
from .flexible_importers_v3 import parse_plain_lines, save_exam


SUBQUESTION_RE = re.compile(r"^[（(](\d+)[）)]\s*(.*)$")
SEGMENTATION_MARKERS_RE = re.compile(r"A.+B.+C")


def import_exam(path: str | Path) -> dict[str, Any]:
    """导入试题并提升断句、小题和默写结构。"""

    source = Path(path)
    if source.suffix.lower() == ".pdf":
        raise ValueError("当前版本不再提供 PDF 导入，请使用 DOCX、TXT、Markdown 或 JSON。")
    result = import_exam_v3(source)
    _normalize_question_details(result)
    return result


def _normalize_question_details(result: dict[str, Any]) -> None:
    for block in result.get("blocks", []):
        question = block.get("question")
        if not question:
            continue
        embedded = question.get("embedded_segments", [])
        remaining: list[list[dict[str, str]]] = []
        subquestions = list(question.get("subquestions", []))
        for segments in embedded:
            text = "".join(str(item.get("text", "")) for item in segments).strip()
            sub_match = SUBQUESTION_RE.match(text)
            if sub_match:
                subquestions.append(sub_match.group(2).strip())
            elif (
                question.get("number") == 10
                and SEGMENTATION_MARKERS_RE.search(text)
            ):
                question["segmentation_text"] = text
            else:
                remaining.append(segments)
        question["embedded_segments"] = remaining
        if subquestions:
            question["subquestions"] = subquestions


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
