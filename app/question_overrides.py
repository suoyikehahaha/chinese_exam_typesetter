"""逐题版式覆盖，作用于生成后的 DOCX。"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt


QUESTION_PREFIX = re.compile(r"^(\d{1,2})．")
OPTION_PREFIX = re.compile(r"^[A-D]．")
ALIGNMENTS = {
    "左对齐": WD_ALIGN_PARAGRAPH.LEFT,
    "居中": WD_ALIGN_PARAGRAPH.CENTER,
    "右对齐": WD_ALIGN_PARAGRAPH.RIGHT,
    "两端对齐": WD_ALIGN_PARAGRAPH.JUSTIFY,
}
FONT_NAMES = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
}


def question_formats(raw_exam: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """读取题号到格式覆盖参数的映射。"""

    result: dict[int, dict[str, Any]] = {}
    for block in raw_exam.get("blocks", []):
        question = block.get("question")
        if question and question.get("format"):
            result[int(question["number"])] = dict(question["format"])
    return result


def apply_question_overrides(docx_path: str | Path, raw_exam: dict[str, Any]) -> None:
    """将逐题字体、缩进、行距和分页参数应用到 DOCX。"""

    formats = question_formats(raw_exam)
    if not formats:
        return
    target = Path(docx_path)
    document = Document(target)
    active_question: int | None = None

    for paragraph in document.paragraphs:
        match = QUESTION_PREFIX.match(paragraph.text)
        if match:
            active_question = int(match.group(1))
            spec = formats.get(active_question)
            if spec:
                _apply_paragraph(paragraph, spec)
            continue
        if active_question is not None and OPTION_PREFIX.match(paragraph.text):
            spec = formats.get(active_question)
            if spec:
                _apply_options(paragraph, spec)
            continue
        if paragraph.style.name.startswith("Exam_") and paragraph.style.name in {
            "Exam_section_title",
            "Exam_subsection",
            "Exam_instruction",
            "Exam_material_title",
            "Exam_material_body",
        }:
            active_question = None

    document.save(target)


def _apply_paragraph(paragraph: Any, spec: dict[str, Any]) -> None:
    font_name = FONT_NAMES.get(str(spec.get("font", "")), str(spec.get("font", "")))
    size = _float(spec.get("size_pt"), 10.5)
    if font_name:
        for run in paragraph.runs:
            _apply_run_font(run, font_name, size)
    paragraph.paragraph_format.first_line_indent = Pt(
        size * _float(spec.get("first_line_indent_chars"), 0)
    )
    paragraph.paragraph_format.line_spacing = _float(spec.get("line_spacing"), 1.25)
    paragraph.paragraph_format.space_before = Pt(_float(spec.get("space_before_pt"), 0))
    paragraph.paragraph_format.space_after = Pt(_float(spec.get("space_after_pt"), 0))
    paragraph.paragraph_format.keep_with_next = bool(spec.get("keep_with_next", False))
    paragraph.paragraph_format.page_break_before = bool(spec.get("page_break_before", False))
    alignment = str(spec.get("alignment", "左对齐"))
    if alignment in ALIGNMENTS:
        paragraph.alignment = ALIGNMENTS[alignment]


def _apply_options(paragraph: Any, spec: dict[str, Any]) -> None:
    font_name = FONT_NAMES.get(str(spec.get("option_font", "宋体")), "SimSun")
    size = _float(spec.get("option_size_pt"), 10.5)
    for run in paragraph.runs:
        _apply_run_font(run, font_name, size)
    paragraph.paragraph_format.left_indent = Pt(
        size * _float(spec.get("option_left_indent_chars"), 1.5)
    )
    paragraph.paragraph_format.first_line_indent = Pt(
        -size * _float(spec.get("option_hanging_indent_chars"), 1.7)
    )


def _apply_run_font(run: Any, font_name: str, size: float) -> None:
    run.font.name = font_name
    run.font.size = Pt(size)
    rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    for key in ("ascii", "hAnsi", "eastAsia", "cs"):
        rfonts.set(qn(f"w:{key}"), font_name)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
