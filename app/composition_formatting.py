"""Formatting pass for answer-aware composition boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from .material_role_formatting import apply_material_role_formatting


def apply_composition_formatting(
    docx_path: str | Path,
    raw_exam: dict[str, Any],
) -> None:
    """Keep q23 prompts and requirements in SimSun with a two-char indent."""

    target = Path(docx_path)
    apply_material_role_formatting(target, raw_exam)
    expected: set[str] = set()
    for block in raw_exam.get("blocks", []):
        question = block.get("question")
        if not question or int(question.get("number", 0)) != 23:
            continue
        expected.update(str(text).strip() for text in question.get("composition_prompt", []))
        expected.update(
            str(text).strip()
            for text in question.get("composition_requirements", [])
        )
    if not expected:
        return
    document = Document(target)
    for paragraph in document.paragraphs:
        if paragraph.text.strip() not in expected:
            continue
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        paragraph.paragraph_format.left_indent = Pt(0)
        paragraph.paragraph_format.first_line_indent = Pt(21)
        for run in paragraph.runs:
            run.font.name = "SimSun"
            run.font.size = Pt(10.5)
            fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
            for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                fonts.set(qn(f"w:{key}"), "SimSun")
    document.save(target)


__all__ = ["apply_composition_formatting"]
