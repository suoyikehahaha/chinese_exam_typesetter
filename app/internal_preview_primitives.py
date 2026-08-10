"""Lightweight DOCX-independent page preview.

It mirrors the semantic layout rules used by ``DocxRenderer`` and produces
PNG pages directly.  The preview keeps the application usable on computers
without Word, WPS or LibreOffice.  Word/WPS remain optional high-fidelity
helpers for legacy conversion and later verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from .config import load_layout
from .page_target import adjusted_layout, get_target_pages


_FONT_FILES = {
    "SimSun": (r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simsun.ttf"),
    "SimHei": (r"C:\Windows\Fonts\simhei.ttf",),
    "KaiTi": (r"C:\Windows\Fonts\simkai.ttf", r"C:\Windows\Fonts\simkai.ttf"),
    "FangSong": (r"C:\Windows\Fonts\simfang.ttf", r"C:\Windows\Fonts\simfang.ttf"),
}


@dataclass(frozen=True, slots=True)
class InternalPreviewResult:
    """Rendered preview pages and block-to-page positions."""

    pages: tuple[Path, ...]
    locators: dict[int, tuple[int, float]]
    target_pages: int
    actual_pages: int


def render_internal_preview(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
) -> InternalPreviewResult:
    """Render all semantic blocks into scrollable A4 PNG pages."""

    base_layout = load_layout(layout_path)
    layout = adjusted_layout(base_layout, raw_exam)
    page_width = 794
    page_height = 1123
    px_per_mm = page_width / 210.0
    margins = layout["page"]
    left = int(float(margins["margin_left_mm"]) * px_per_mm)
    right = page_width - int(float(margins["margin_right_mm"]) * px_per_mm)
    top = int(float(margins["margin_top_mm"]) * px_per_mm)
    bottom = page_height - int(float(margins["margin_bottom_mm"]) * px_per_mm)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    pages: list[Path] = []
    locators: dict[int, tuple[int, float]] = {}
    page: Image.Image | None = None
    draw: ImageDraw.ImageDraw | None = None
    y = top

    def new_page() -> None:
        nonlocal page, draw, y
        if page is not None:
            target = output / f"page-{len(pages) + 1}.png"
            page.save(target, "PNG")
            pages.append(target)
        page = Image.new("RGB", (page_width, page_height), "white")
        draw = ImageDraw.Draw(page)
        y = top

    def ensure_height(height: int) -> None:
        nonlocal y
        if page is None:
            new_page()
        if y + height > bottom:
            new_page()

    def add_entry(
        block_index: int,
        text: str,
        role: str,
        *,
        align: str | None = None,
        indent_chars: float | None = None,
        bold: bool | None = None,
        after: float = 0,
        color: str = "#111111",
    ) -> None:
        nonlocal y
        if draw is None:
            new_page()
        spec = layout.get("styles", {}).get(role, layout.get("styles", {}).get("material_body", {}))
        font_name = str(spec.get("font", "SimSun"))
        size = float(spec.get("size_pt", 10.5))
        font = _font(font_name, size, bool(spec.get("bold", False) if bold is None else bold))
        line_spacing = float(spec.get("line_spacing", layout.get("defaults", {}).get("body_line_spacing", 1.25)))
        line_height = max(14, int(font.size * line_spacing + 3))
        indent = float(spec.get("first_line_indent_chars", 0) if indent_chars is None else indent_chars)
        indent_px = int(size * indent * 1.2)
        alignment = align or str(spec.get("alignment", "left"))
        lines = _wrap_text(draw, text, font, right - left - indent_px)
        if not lines:
            lines = [""]
        if page is None:
            new_page()
        if block_index not in locators:
            locators[block_index] = (len(pages), max(0.0, min(1.0, (y - top) / max(1, bottom - top))))
        for line_index, line in enumerate(lines):
            ensure_height(line_height)
            x = left + (indent_px if line_index == 0 else 0)
            width = draw.textlength(line, font=font)
            if alignment in {"center", "居中"}:
                x = left + (right - left - width) / 2
            elif alignment in {"right", "右对齐"}:
                x = right - width
            elif alignment in {"justify", "两端对齐"}:
                x = left + (indent_px if line_index == 0 else 0)
            draw.text((x, y), line, fill=color, font=font)
            y += line_height
        y += int(float(spec.get("space_after_pt", 0)) * 1.3 + after)

    def add_mixed(block_index: int, segments: Iterable[dict[str, Any]]) -> None:
        text = "".join(str(item.get("text", "")) for item in segments)
        # The internal preview keeps the label/body distinction visible by
        # drawing the whole line in the body font.  The editable DOCX retains
        # the precise run-level fonts.
        add_entry(block_index, text, "embedded_body", indent_chars=2)

    new_page()
    metadata = raw_exam.get("metadata", {})
    add_entry(-1, str(metadata.get("exam_name", "高中语文试卷")), "exam_name", align="center", after=4)
    add_entry(-1, str(metadata.get("subject_name", "语　文")), "subject_name", align="center", after=4)
    if str(metadata.get("meta_text", "")).strip():
        add_entry(-1, str(metadata.get("meta_text", "")), "exam_meta", align="center", after=2)
    notices = [str(item) for item in metadata.get("notices", []) if str(item).strip()]
    if notices:
        add_entry(-1, "注意事项：", "notice_title", after=1)
        for index, notice in enumerate(notices, start=1):
            add_entry(-1, f"{index}．{notice}", "notice_body", indent_chars=2)

    for index, block in enumerate(raw_exam.get("blocks", [])):
        block_type = str(block.get("type", ""))
        if block_type == "section_title":
            add_entry(index, str(block.get("text", "")), "section_title", after=3)
        elif block_type == "subsection":
            add_entry(index, f"{block.get('name', '')}{block.get('meta', '')}", "subsection", after=2)
        elif block_type == "instruction":
            add_entry(index, str(block.get("text", "")), "instruction", indent_chars=2)
        elif block_type in {"material", "poetry"}:
            _render_material(block, index, add_entry)
        elif block_type == "question":
            _render_question(block.get("question", {}), index, add_entry, add_mixed, layout)
        elif block_type == "page_break":
            new_page()

    if page is not None:
        target = output / f"page-{len(pages) + 1}.png"
        page.save(target, "PNG")
        pages.append(target)
    actual = len(pages)
    return InternalPreviewResult(tuple(pages), locators, get_target_pages(raw_exam), actual)


def _render_material(block: dict[str, Any], index: int, add_entry: Any) -> None:
    if block.get("title"):
        add_entry(index, str(block["title"]), "material_title", align="center", after=1)
    if block.get("author"):
        add_entry(index, str(block["author"]), "material_author", align="center", after=1)
    roles = [str(item) for item in block.get("paragraph_roles", [])]
    for paragraph_index, value in enumerate(block.get("paragraphs", [])):
        text = str(value)
        role = roles[paragraph_index] if paragraph_index < len(roles) else "body"
        if re.match(r"^材料[一二三四五六七八九十0-9：:]", text):
            add_entry(index, text, "material_title", align="left", after=1)
        elif role in {"source", "publication_note"} or re.match(r"^\s*[（(].*(?:摘|选|译|发表于|有删改|有改动).*[）)]\s*$", text):
            add_entry(index, text, "material_source", align="right")
        elif role == "author":
            add_entry(index, text, "material_author", align="center")
        else:
            add_entry(index, text, "material_body", indent_chars=2)
    if block.get("note"):
        add_entry(index, str(block["note"]), "material_note", indent_chars=2)
    if block.get("source"):
        add_entry(index, str(block["source"]), "material_source", align="right")


def _render_question(question: dict[str, Any], index: int, add_entry: Any, add_mixed: Any, layout: dict[str, Any]) -> None:
    kind = str(question.get("kind", "subjective"))
    role = "objective_stem" if kind == "objective" else "subjective_stem"
    score = question.get("score")
    suffix = ""
    if score is not None:
        value = float(score)
        suffix = f"（{int(value) if value.is_integer() else value}分）"
    add_entry(index, f"{question.get('number', '')}．{question.get('stem', '')}{suffix}", role, indent_chars=0 if kind == "objective" else 1.5, after=1)
    for option in question.get("options", []):
        add_entry(index, str(option), "choice_option", indent_chars=1.5)
    for segments in question.get("embedded_segments", []):
        add_mixed(index, segments)
    segmentation = question.get("segmentation_text")
    if segmentation:
        add_entry(index, str(segmentation), "segmentation_text", indent_chars=2)
    for subquestion_index, text in enumerate(question.get("subquestions", []), start=1):
        add_entry(index, f"（{subquestion_index}）{text}", "subquestion", indent_chars=2)
    for text in question.get("composition_material", []):
        add_entry(index, str(text), "composition_material", indent_chars=2)
    for text in question.get("composition_prompt", []):
        add_entry(index, str(text), "composition_prompt", indent_chars=2)
    for text in question.get("composition_requirements", []):
        add_entry(index, str(text), "composition_requirements", indent_chars=2)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    result: list[str] = []
    for raw_line in str(text).splitlines() or [""]:
        if not raw_line:
            result.append("")
            continue
        current = ""
        for character in raw_line:
            candidate = current + character
            if current and draw.textlength(candidate, font=font) > width:
                result.append(current)
                current = character
            else:
                current = candidate
        result.append(current)
    return result


def _font(name: str, size_pt: float, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    normalized = {"宋体": "SimSun", "黑体": "SimHei", "楷体": "KaiTi", "仿宋": "FangSong"}.get(name, name)
    candidates = list(_FONT_FILES.get(normalized, ()))
    if bold and normalized == "SimSun":
        candidates.insert(0, r"C:\Windows\Fonts\simsunb.ttf")
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), max(8, int(round(size_pt * 1.33))))
            except OSError:
                continue
    return ImageFont.load_default()


__all__ = ["InternalPreviewResult", "render_internal_preview"]
