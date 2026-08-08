"""Flexible importer v6 preserving source underline and emphasis marks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document

from .flexible_importers_v5 import import_exam as import_exam_v5
from .flexible_importers_v5 import parse_plain_lines, save_exam


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import an exam and attach character-decoration metadata."""

    source = Path(path)
    result = import_exam_v5(source)
    if source.suffix.lower() == ".docx":
        result.setdefault("metadata", {})["source_decorations"] = (
            _collect_source_decorations(Document(source))
        )
    return result


def _collect_source_decorations(document: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    occurrences: dict[str, int] = {}
    for paragraph in document.paragraphs:
        raw_text = paragraph.text
        text = raw_text.strip()
        if not text:
            continue
        leading = len(raw_text) - len(raw_text.lstrip())
        ranges: list[dict[str, Any]] = []
        cursor = 0
        paragraph_style_bold = bool(
            getattr(getattr(paragraph, "style", None), "font", None)
            and getattr(paragraph.style.font, "bold", False)
        )
        for run in paragraph.runs:
            start = cursor - leading
            end = start + len(run.text)
            cursor += len(run.text)
            if end <= 0 or start >= len(text):
                continue
            underline_nodes = run._r.xpath("./w:rPr/w:u")
            emphasis_nodes = run._r.xpath("./w:rPr/w:em")
            underline = [node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") or "single" for node in underline_nodes]
            emphasis = [node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") or "underDot" for node in emphasis_nodes]
            bold = bool(run.bold) or (run.bold is None and paragraph_style_bold)
            if not underline and not emphasis and not bold:
                continue
            mark: dict[str, Any] = {
                "start": max(0, start),
                "end": min(len(text), end),
            }
            if underline and underline[0] not in {"none", "0", "false"}:
                mark["underline"] = str(underline[0])
            if emphasis and emphasis[0] not in {"none", "0", "false"}:
                mark["emphasis"] = str(emphasis[0])
            if bold:
                mark["bold"] = True
            if len(mark) > 2:
                ranges.append(mark)
        occurrence = occurrences.get(text, 0)
        occurrences[text] = occurrence + 1
        if ranges:
            collected.append(
                {
                    "text": text,
                    "occurrence": occurrence,
                    "ranges": ranges,
                }
            )
    return collected


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
