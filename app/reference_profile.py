"""Explicit formatting requirements from the supplied 2026 national-II template.

The annotated reference defines thirteen roles. Its body is never imported as
instructions or copied into user exams. Existing presets keep their behavior.
"""

from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

PROFILE_KEY = "national_ii_2026"


def is_reference_profile(raw: dict) -> bool:
    return raw.get("metadata", {}).get("layout_preset") == PROFILE_KEY


def prepare_profile_exam(raw: dict) -> dict:
    """Adapt automatic semantic annotations; retain explicit user overrides."""
    if not is_reference_profile(raw):
        return raw
    result = deepcopy(raw)
    for block in result.get("blocks", []):
        if block.get("type") == "question":
            spec = block.get("question", {}).get("format", {})
            if any(key.startswith("option_") for key in spec):
                spec.setdefault("option_indent_origin", "paragraph")
        for spec in block.get("paragraph_formats", []):
            if spec.get("semantic_role") == "author":
                spec["font"] = "楷体"
    return result


def apply_reference_profile(path: str | Path, raw: dict) -> None:
    """Restore reference defaults after legacy passes, before user overrides."""
    if not is_reference_profile(raw) or raw.get("document_kind") == "answer":
        return
    document = Document(path)
    subsection_names = {
        str(block.get("name", "")) + str(block.get("meta", "")): len(str(block.get("name", "")))
        for block in raw.get("blocks", []) if block.get("type") == "subsection"
    }
    for paragraph in document.paragraphs:
        role = paragraph.style.name.removeprefix("Exam_")
        fmt = paragraph.paragraph_format
        if role == "section_title":
            _font(paragraph, "SimHei", 12)
        elif role == "material_author":
            _font(paragraph, "KaiTi", 10.5)
        elif role in {"objective_stem", "subjective_stem"}:
            fmt.left_indent = Pt(0)
            fmt.first_line_indent = Pt(0)
        elif role == "choice_option":
            # Word's 'text before' means the paragraph margin, not first-line x.
            fmt.left_indent = Pt(10.5 * 1.5)
            fmt.first_line_indent = Pt(-10.5 * 1.7)
        elif role == "exam_meta":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            fmt.left_indent = Pt(0)
            fmt.first_line_indent = Pt(21)
        elif role == "subsection":
            boundary = subsection_names.get(paragraph.text)
            if boundary is not None:
                position = 0
                for run in paragraph.runs:
                    run.bold = position < boundary
                    position += len(run.text)
    document.save(path)


def _font(paragraph, name: str, size: float) -> None:
    for run in paragraph.runs:
        run.font.name = name
        run.font.size = Pt(size)
        fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
        for key in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(qn(f"w:{key}"), name)
