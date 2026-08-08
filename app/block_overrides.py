"""章节、材料和提示等非题目内容块的格式覆盖。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt


FONT_NAMES = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
}
ALIGNMENTS = {
    "左对齐": WD_ALIGN_PARAGRAPH.LEFT,
    "居中": WD_ALIGN_PARAGRAPH.CENTER,
    "右对齐": WD_ALIGN_PARAGRAPH.RIGHT,
    "两端对齐": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


def apply_block_overrides(docx_path: str | Path, raw_exam: dict[str, Any]) -> None:
    """按内容文本匹配并应用非题目内容块的用户格式。"""

    specs: list[tuple[set[str], dict[str, Any]]] = []
    for block in raw_exam.get("blocks", []):
        if block.get("type") == "question" or not block.get("format"):
            continue
        texts = _block_texts(block)
        if texts:
            specs.append((texts, dict(block["format"])))
    if not specs:
        return

    target = Path(docx_path)
    document = Document(target)
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        for texts, spec in specs:
            if text in texts:
                _apply(paragraph, spec)
                break
    document.save(target)


def _block_texts(block: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for key in ("text", "name", "title", "author", "note", "source"):
        if block.get(key):
            values.append(str(block[key]).strip())
    values.extend(str(item).strip() for item in block.get("paragraphs", []))
    return {value for value in values if value}


def _apply(paragraph: Any, spec: dict[str, Any]) -> None:
    font = FONT_NAMES.get(str(spec.get("font", "")), str(spec.get("font", "")))
    size = _number(spec.get("size_pt"), 10.5)
    if font:
        for run in paragraph.runs:
            run.font.name = font
            run.font.size = Pt(size)
            rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
            for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                rfonts.set(qn(f"w:{key}"), font)
    paragraph.paragraph_format.first_line_indent = Pt(
        size * _number(spec.get("first_line_indent_chars"), 0)
    )
    paragraph.paragraph_format.line_spacing = _number(spec.get("line_spacing"), 1.25)
    paragraph.paragraph_format.space_before = Pt(
        _number(spec.get("space_before_pt"), 0)
    )
    paragraph.paragraph_format.space_after = Pt(
        _number(spec.get("space_after_pt"), 0)
    )
    paragraph.paragraph_format.keep_with_next = bool(spec.get("keep_with_next", False))
    paragraph.paragraph_format.page_break_before = bool(
        spec.get("page_break_before", False)
    )
    alignment = str(spec.get("alignment", "左对齐"))
    if alignment in ALIGNMENTS:
        paragraph.alignment = ALIGNMENTS[alignment]


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
