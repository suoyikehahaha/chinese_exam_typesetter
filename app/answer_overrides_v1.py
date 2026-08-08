"""Apply editable paragraph and selected-text overrides to answer blocks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document

from .inline_formatting_v3 import FONT_NAMES, _format_range
from .paragraph_formatting_v1 import _apply, _find_exact


def apply_answer_overrides_v1(
    docx_path: str | Path,
    raw_exam: dict[str, Any],
) -> None:
    """Apply workbench controls after the Guangzhou answer base style."""

    target = Path(docx_path)
    document = Document(target)
    for block in raw_exam.get("blocks", []):
        if not str(block.get("type", "")).startswith("answer_"):
            continue
        lines = _answer_lines(block)
        spec = block.get("format")
        if spec:
            for text in lines:
                paragraph = _find_exact(document, text)
                if paragraph is not None:
                    _apply(paragraph, spec)
        for entry in block.get("inline_formats", []):
            line = int(entry.get("line", entry.get("target_index", 0)))
            if line < 0 or line >= len(lines):
                continue
            paragraph = _find_exact(document, lines[line])
            if paragraph is None:
                continue
            _format_range(
                paragraph,
                int(entry.get("start", 0)),
                int(entry.get("end", 0)),
                FONT_NAMES.get(
                    str(entry.get("font", "")),
                    str(entry.get("font", "")),
                ),
                float(entry.get("size_pt", 10.5)),
                bool(entry.get("bold", False)),
            )
    document.save(target)


def _answer_lines(block: dict[str, Any]) -> list[str]:
    kind = block.get("type")
    if kind == "answer_section":
        return [str(block.get("text", ""))]
    if kind == "answer_subsection":
        return [f"{block.get('name', '')}{block.get('meta', '')}"]
    if kind in {"answer_question", "answer_text"}:
        values: list[str] = []
        if block.get("header"):
            values.append(str(block["header"]))
        values.extend(
            str(entry.get("text", "")) for entry in block.get("paragraphs", [])
        )
        return values
    return []


__all__ = ["apply_answer_overrides_v1"]
