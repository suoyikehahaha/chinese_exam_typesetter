"""Semantic internal preview with per-question format overrides."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from docx import Document
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

from .config import load_layout
from .font_resolver import load_preview_font as _font
from .chinese_line_break import wrap_plain_text as _wrap_text
from .page_layout import adjusted_layout
from .preview_cancellation import check_cancelled
from .chinese_line_break import wrap_plain_text, wrap_rich_text
from .font_resolver import font_substitution_notice
from .paragraph_identity import paragraph_identity
from .reference_profile import is_reference_profile
from .source_document import source_document
from .preview_formatting import preview_format
from .paragraph_formatting import _option_spec
from .answer_typesetting import answer_titles


@dataclass(frozen=True, slots=True)
class InternalPreviewResult:
    pages: tuple[Path, ...]
    locators: dict[int, tuple[int, float]]
    target_pages: int
    actual_pages: int
    warnings: tuple[str, ...] = ()


_MATERIAL_LABEL_RE = re.compile(
    r"^\s*材料(?:[一二三四五六七八九十百]+|\d+)\s*[：:]?\s*$"
)
_NATIVE_DRAWING_RE = re.compile(r"^\[\[(?:NATIVE_(?:DRAWING|TABLE)|ANSWER_TABLE):\d+\]\]$")
# Word-compatible CJK line boxes include additional ascent/descent leading that
# Pillow's glyph-size metric omits. The factor is verified against the bundled
# national and Guangzhou reference documents.
_WORD_LINE_BOX_FACTOR = 1.14


@dataclass
class _SourceVisuals:
    """Source-level character marks and drawing images used by the preview."""

    decorations: dict[str, list[dict[str, Any]]]
    images: dict[str, list[list[Image.Image]]]
    decoration_cursor: dict[str, int]
    image_cursor: dict[str, int]
    warnings: set[str] = field(default_factory=set)

    def marks_for(self, text: str) -> list[dict[str, Any]]:
        if "\n" in text:
            marks, offset = [], 0
            for line in text.splitlines(keepends=True):
                marks.extend({**mark, "start":mark["start"]+offset, "end":mark["end"]+offset} for mark in self.marks_for(line.rstrip("\r\n")))
                offset += len(line)
            return marks
        key = paragraph_identity(text)
        entries = self.decorations.get(key, [])
        position = self.decoration_cursor.get(key, 0)
        if position >= len(entries):
            return []
        self.decoration_cursor[key] = position + 1
        return list(entries[position].get("ranges", []))

    def images_for(self, text: str) -> list[Image.Image]:
        key = paragraph_identity(text)
        groups = self.images.get(key, [])
        position = self.image_cursor.get(key, 0)
        if position >= len(groups):
            return []
        self.image_cursor[key] = position + 1
        return groups[position]


def _load_source_visuals(raw_exam: dict[str, Any]) -> _SourceVisuals:
    """Load original run marks and embedded images when the source DOCX exists."""

    metadata = raw_exam.get("metadata", {})
    decorations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in metadata.get("source_decorations", []):
        if isinstance(entry, dict) and str(entry.get("text", "")).strip():
            bucket = decorations[paragraph_identity(entry["text"])]
            at = int(entry.get("occurrence", len(bucket)))
            bucket.extend({} for _ in range(at + 1 - len(bucket)))
            bucket[at] = entry
    images: dict[str, list[list[Image.Image]]] = defaultdict(list)
    result = _SourceVisuals(dict(decorations), images, {}, {})
    document = source_document(metadata, answer=raw_exam.get("document_kind") == "answer")
    if document is None:
        if metadata.get("native_objects"):
            raise ValueError("图片或表格缺少原文，请恢复原文件的位置，或重新导入。")
        return result
    specs = {entry.get("source_paragraph_index"): entry for entry in metadata.get("native_objects", ())
             if entry.get("kind") == "drawing"}
    for index, paragraph in enumerate(document.paragraphs):
        spec = specs.get(index, {})
        blips = paragraph._p.xpath(".//a:blip | .//*[local-name()='imagedata']")
        if not (blips or spec):
            continue
        key = paragraph_identity(spec.get("marker") or spec.get("target_text") or paragraph.text)
        if not key:
            continue
        group = []
        for blip in blips:
            part = document.part.related_parts.get(blip.get(qn("r:embed")) or blip.get(qn("r:id")))
            if part is not None:
                try:
                    with Image.open(BytesIO(part.blob)) as source:
                        group.append(source.convert("RGBA"))
                except (OSError, ValueError):
                    result.warnings.add(f"原文第 {index + 1} 段含特殊图片，请在导出的 Word 中核对。")
                    placeholder = Image.new("RGBA", (270, 34), "#F2F5F8")
                    ImageDraw.Draw(placeholder).text((6, 6), "原文图片 · 请在 Word 中核对", font=_font("SimSun", 10.5), fill="#46566A")
                    group.append(placeholder)
        if not group and spec:
            result.warnings.add(f"原文第 {index + 1} 段包含 Word 绘图，形状和位置请在导出文档中核对。")
            if spec.get("marker"):
                placeholder = Image.new("RGBA", (260, 32), "#F2F5F8")
                ImageDraw.Draw(placeholder).text((5, 5), "原文绘图 · 请在 Word 中核对", font=_font("SimSun", 10.5), fill="#46566A")
                group.append(placeholder)
        if group:
            bucket = images[key]
            at = int(spec.get("occurrence", len(bucket)))
            bucket.extend([] for _ in range(at + 1 - len(bucket)))
            bucket[at] = group
    return result


def _mark_active(ranges: list[dict[str, Any]], position: int, key: str) -> bool:
    for mark in reversed(ranges):
        if key in mark and int(mark.get("start", 0)) <= position < int(mark.get("end", 0)):
            return bool(mark[key])
    return False


def _draw_rich_line(
    draw: ImageDraw.ImageDraw,
    line: str,
    start: int,
    y: int,
    x: float,
    font: ImageFont.ImageFont,
    bold_font: ImageFont.ImageFont,
    ranges: list[dict[str, Any]],
    font_at: Any = None,
) -> None:
    """Draw a line with source bold, underline and emphasis marks."""

    cursor = x
    position = start
    segment: list[str] = []
    segment_bold = False
    segment_underline = False
    segment_emphasis = False
    segment_font = font

    def flush() -> None:
        nonlocal cursor, segment
        if not segment:
            return
        value = "".join(segment)
        chosen = segment_font
        draw.text((cursor, y), value, fill="#111111", font=chosen, stroke_width=getattr(chosen, "preview_stroke_width", 0))
        width = draw.textlength(value, font=chosen)
        if segment_underline:
            baseline = y + max(2, int(getattr(chosen, "size", 12))) + 1
            draw.line((cursor, baseline, cursor + width, baseline), fill="#111111", width=1)
        if segment_emphasis:
            dot_y = y + max(2, int(getattr(chosen, "size", 12))) + (5 if segment_underline else 3)
            for index in range(len(value)):
                char_width = draw.textlength(value[index], font=chosen)
                draw.ellipse((cursor + char_width * 0.35 - 1, dot_y - 1, cursor + char_width * 0.35 + 1, dot_y + 1), fill="#111111")
                cursor += char_width
            segment = []
            return
        cursor += width
        segment = []

    for character in line:
        bold = _mark_active(ranges, position, "bold")
        underline = _mark_active(ranges, position, "underline")
        emphasis = _mark_active(ranges, position, "emphasis")
        chosen = font_at(position) if font_at else (bold_font if bold else font)
        if segment and (chosen, bold, underline, emphasis) != (segment_font, segment_bold, segment_underline, segment_emphasis):
            flush()
        if not segment:
            segment_bold, segment_underline, segment_emphasis = bold, underline, emphasis
            segment_font = chosen
        segment.append(character)
        position += 1
    flush()


def render_internal_preview(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
    *, visual_loader=None,
) -> InternalPreviewResult:
    """Render all blocks while honoring explicit editor format overrides."""

    layout = adjusted_layout(load_layout(layout_path), raw_exam)
    width, height = 794, 1123
    px_per_mm = width / 210.0
    page_spec = layout["page"]
    left = int(float(page_spec["margin_left_mm"]) * px_per_mm)
    right = width - int(float(page_spec["margin_right_mm"]) * px_per_mm)
    top = int(float(page_spec["margin_top_mm"]) * px_per_mm)
    bottom = height - int(float(page_spec["margin_bottom_mm"]) * px_per_mm)
    footer_distance_mm = float(page_spec.get("footer_distance_mm", 10))
    footer_y = height - int(footer_distance_mm * px_per_mm)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    pages: list[Path] = []
    locators: dict[int, tuple[int, float]] = {}
    font_notices: set[str] = set()
    page: Image.Image | None = None
    draw: ImageDraw.ImageDraw | None = None
    y = top
    source_visuals = (visual_loader or _load_source_visuals)(raw_exam)
    font_notices.update(source_visuals.warnings)

    def finalize_page(page_number: int) -> None:
        """Paint preview-only margin guides and a centered page number."""

        if page is None or draw is None:
            return
        footer_font = _font("SimSun", 9.0)
        label = str(page_number)
        box = draw.textbbox((0, 0), label, font=footer_font)
        label_width = box[2] - box[0]
        label_height = box[3] - box[1]
        draw.text(
            ((width - label_width) / 2, footer_y - label_height),
            label,
            fill="#59636E",
            font=footer_font,
        )

    def new_page() -> None:
        nonlocal page, draw, y
        if page is not None:
            finalize_page(len(pages) + 1)
            target = output / f"page-{len(pages) + 1}.png"
            page.save(target, "PNG", compress_level=1)
            pages.append(target)
        page = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(page)
        y = top

    def draw_source_images(images: list[Image.Image]) -> None:
        nonlocal y
        check_cancelled()
        for source_image in images:
            image = source_image.convert("RGBA")
            max_width = max(1, right - left)
            max_height = max(1, bottom - top)
            scale = min(
                1.0,
                max_width / max(1, image.width),
                max_height / max(1, image.height),
            )
            target_width = max(1, int(image.width * scale))
            target_height = max(1, int(image.height * scale))
            resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            if y + target_height > bottom:
                new_page()
            x = left + (right - left - target_width) / 2
            if page is not None:
                page.paste(resized, (int(x), int(y)), resized)
            y += target_height + 4

    def add_entry(
        block_index: int,
        text: str,
        role: str,
        *,
        override: dict[str, Any] | None = None,
        indent_chars: float | None = None,
        align: str | None = None,
        after: float = 0,
        measure: bool = False,
    ) -> float | None:
        nonlocal y
        spec = dict(layout.get("styles", {}).get(role, layout.get("styles", {}).get("material_body", {})))
        check_cancelled()
        if override:
            spec.update({key: value for key, value in override.items() if value is not None})
        font_name = str(spec.get("font", "SimSun"))
        size_pt = float(spec.get("size_pt", 10.5))
        semantic_bold = bool(spec.get("bold", False))
        font = _font(font_name, size_pt, semantic_bold)
        notice = font_substitution_notice(font_name, font)
        if notice:
            font_notices.add(notice)
        line_spacing = float(spec.get("line_spacing", layout.get("defaults", {}).get("body_line_spacing", 1.25)))
        line_height = max(14, int((font.size * line_spacing + 3) * _WORD_LINE_BOX_FACTOR))
        if is_reference_profile(raw_exam) and role in {"exam_meta", "objective_stem", "subjective_stem", "choice_option"}:
            indent_chars = None
            align = None
        indent = float(spec.get("first_line_indent_chars", 0) if indent_chars is None else indent_chars)
        if override and "first_line_indent_chars" in override:
            indent = float(override["first_line_indent_chars"])
        if "special_indent" in spec:
            special = str(spec["special_indent"])
            indent = float(spec.get("special_indent_chars", 0)) * (1 if special in {"首行", "first_line"} else -1 if special in {"悬挂", "hanging"} else 0)
        point_px = px_per_mm * 25.4 / 72
        indent_px = int(size_pt * indent * point_px)
        text_left = left + int(float(spec.get("left_indent_chars", 0)) * size_pt * point_px)
        if indent < 0 and spec.get("indent_origin") != "paragraph":
            text_left += abs(indent_px)
        text_right = right - int(float(spec.get("right_indent_chars", 0)) * size_pt * point_px)
        alignment = str((override or {}).get("alignment", align or spec.get("alignment", "left")))
        if not measure and spec.get("page_break_before") and page is not None and y > top:
            new_page()
        space_before = int(float(spec.get("space_before_pt", 0)) * point_px)
        if not measure:
            y += space_before
        source_images = source_visuals.images_for(text)
        if not source_images and _NATIVE_DRAWING_RE.fullmatch(str(text).strip()):
            raise ValueError("预览无法读取原文件中的图片或表格。请恢复原文件的位置，或重新导入该文档。")
        if source_images and _NATIVE_DRAWING_RE.fullmatch(str(text).strip()):
            if page is None:
                new_page()
            if block_index not in locators:
                locators[block_index] = (
                    len(pages),
                    max(0.0, min(1.0, (y - top) / max(1, bottom - top))),
                )
            draw_source_images(source_images)
            y += int(float(spec.get("space_after_pt", 0)) * 1.3 + after)
            return
        marks = source_visuals.marks_for(text)
        if spec.get("bold_prefix_length"):
            marks.append({"start": 0, "end": int(spec["bold_prefix_length"]), "bold": True})
        marks.extend(spec.get("_inline_formats", []))
        drawing = draw or ImageDraw.Draw(Image.new("RGB", (1, 1)))
        bold_font = _font(font_name, size_pt, True) if any(mark.get("bold") for mark in marks) else font
        if marks and (notice := font_substitution_notice(font_name, bold_font)):
            font_notices.add(notice)
        styled_fonts = {}
        for mark in marks:
            name = str(mark.get("font") or font_name)
            size = float(mark.get("size_pt", size_pt))
            bold = bool(mark.get("bold", semantic_bold))
            key = (name, size, bold)
            if key not in styled_fonts:
                styled_fonts[key] = _font(name, size, bold)
                if notice := font_substitution_notice(name, styled_fonts[key]):
                    font_notices.add(notice)
        def font_at(position):
            name, size, bold = font_name, size_pt, semantic_bold
            for mark in marks:
                if int(mark.get("start", 0)) <= position < int(mark.get("end", 0)):
                    name = str(mark.get("font") or name)
                    size = float(mark.get("size_pt", size))
                    bold = bool(mark.get("bold", bold))
            key = (name, size, bold)
            if key not in styled_fonts:
                styled_fonts[key] = _font(name, size, bold)
            return styled_fonts[key]
        if styled_fonts:
            line_height = max(line_height, int((max(f.size for f in styled_fonts.values()) * line_spacing + 3) * _WORD_LINE_BOX_FACTOR))
        if spec.get("line_height_pt"):
            line_height = float(spec["line_height_pt"]) * point_px
        if marks:
            line_infos = wrap_rich_text(
                drawing,
                text,
                font,
                bold_font,
                text_right - text_left,
                marks,
                first_width=text_right - text_left - indent_px,
                font_at=font_at,
            )
        else:
            line_infos = [
                (line, 0, 0)
                for line in wrap_plain_text(drawing, text, font, text_right - text_left, first_width=text_right - text_left - indent_px)
            ]
        if not line_infos:
            line_infos = [("", 0, 0)]
        if measure:
            return space_before + len(line_infos) * line_height + int(float(spec.get("space_after_pt", 0)) * point_px + after)
        if page is None:
            new_page()
        # Reserve the next line for headings / explicit paragraph keep flags.
        keep_next = bool(spec.get("keep_with_next")) or role in {"section_title", "subsection", "material_title", "material_author", "instruction"}
        needed = len(line_infos) * line_height + (line_height if keep_next else 0)
        if source_images:
            for source_image in source_images:
                image_scale = min(1.0, (right - left) / max(1, source_image.width), (bottom - top) / max(1, source_image.height))
                needed += max(1, int(source_image.height * image_scale)) + 4
        if (keep_next or source_images) and needed <= bottom - top and y + needed > bottom and y > top:
            new_page()
        for line_index, (line, line_start, _line_end) in enumerate(line_infos):
            check_cancelled()
            if y + line_height > bottom:
                new_page()
            if block_index not in locators:
                locators[block_index] = (
                    len(pages),
                    max(0.0, min(1.0, (y - top) / max(1, bottom - top))),
                )
            if marks:
                line_width = sum(
                    drawing.textlength(
                        character,
                        font=font_at(line_start + offset),
                    )
                    for offset, character in enumerate(line)
                )
            else:
                line_width = drawing.textlength(line, font=font)
            x = text_left + (indent_px if line_index == 0 else 0)
            if alignment in {"center", "居中"}:
                x = text_left + (text_right - text_left - line_width) / 2
            elif alignment in {"right", "右对齐"}:
                x = text_right - line_width
            if draw:
                if marks:
                    _draw_rich_line(
                        draw,
                        line,
                        line_start,
                        int(y),
                        x,
                        font,
                        bold_font,
                        marks,
                        font_at,
                    )
                else:
                    draw.text((x, y), line, fill="#111111", font=font, stroke_width=getattr(font, "preview_stroke_width", 0))
            y += line_height
        if source_images:
            draw_source_images(source_images)
        y += int(float(spec.get("space_after_pt", 0)) * point_px + after)

    def add_mixed(block_index: int, segments: Iterable[dict[str, Any]], override: dict[str, Any] | None = None) -> None:
        segments = list(segments)
        spec = dict(override or {})
        ranges, position = [], 0
        for item in segments:
            value = str(item.get("text", ""))
            if item.get("role") == "label" and not spec.get("font"):
                ranges.append({"start": position, "end": position + len(value), "font": "SimSun"})
            position += len(value)
        spec["_inline_formats"] = ranges + spec.get("_inline_formats", [])
        add_entry(block_index, "".join(str(item.get("text", "")) for item in segments), "embedded_body", override=spec, indent_chars=2)

    def add_columns(block_index, entries):
        nonlocal left, right, y
        outer_left, outer_right = left, right
        middle = (left + right) // 2
        for start in range(0, len(entries), 2):
            row = entries[start:start + 2]
            heights = []
            for column, (text, spec) in enumerate(row):
                left, right = (outer_left, middle) if column == 0 else (middle, outer_right)
                cursors = dict(source_visuals.decoration_cursor), dict(source_visuals.image_cursor)
                heights.append(add_entry(block_index, text, "choice_option", override=spec, measure=True) or 0)
                source_visuals.decoration_cursor, source_visuals.image_cursor = cursors
            left, right = outer_left, outer_right
            if y > top and y + max(heights) > bottom:
                new_page()
            # Very long options cannot fit a single table row on the preview page.
            if max(heights) > bottom - top:
                font_notices.add("双列选项过长，预览按单列展开；导出仍保留双列")
                for text, spec in row:
                    add_entry(block_index, text, "choice_option", override=spec)
                continue
            row_y = y
            for column, (text, spec) in enumerate(row):
                left, right = (outer_left, middle) if column == 0 else (middle, outer_right)
                y = row_y
                add_entry(block_index, text, "choice_option", override=spec)
            left, right, y = outer_left, outer_right, row_y + max(heights)

    new_page()
    metadata = raw_exam.get("metadata", {})
    def add_answer_titles():
        for title in answer_titles(raw_exam):
            add_entry(-1, title, "exam_name", override={"font": "SimSun", "size_pt": 14, "bold": True,
                                                      "alignment": "center", "line_height_pt": 23.5})
    if raw_exam.get("document_kind") == "answer":
        add_answer_titles()
    else:
        if str(metadata.get("confidentiality_text", "")).strip():
            add_entry(-1, str(metadata["confidentiality_text"]), "notice_body", override={"first_line_indent_chars": 0})
        add_entry(-1, str(metadata.get("exam_name", "高中语文试卷")), "exam_name", align="center", after=4)
        add_entry(-1, str(metadata.get("subject_name", "语　文")), "subject_name", align="center", after=4)
        if str(metadata.get("meta_text", "")).strip():
            add_entry(-1, str(metadata.get("meta_text", "")), "exam_meta", align="center", after=2)
        for text in metadata.get("header_lines", []):
            add_entry(-1, str(text), "exam_meta", align="center", after=2)
        notices = [str(item) for item in metadata.get("notices", []) if str(item).strip()]
        if notices:
            add_entry(-1, "注意事项：", "notice_title", after=1)
            for number, notice in enumerate(notices, start=1):
                add_entry(-1, f"{number}．{notice}", "notice_body", indent_chars=2)

    for index, block in enumerate(raw_exam.get("blocks", [])):
        if block.get("_answer_start") and raw_exam.get("document_kind") != "answer":
            if y > top:
                new_page()
            add_answer_titles()
        kind = str(block.get("type", ""))
        if kind == "section_title":
            add_entry(index, str(block.get("text", "")), "section_title", after=3, override=preview_format(block, base=block.get("format")))
        elif kind == "subsection":
            prefix = {"bold_prefix_length": len(str(block.get("name", ""))), **block.get("format", {})}
            prefix = preview_format(block, base=prefix)
            add_entry(index, f"{block.get('name', '')}{block.get('meta', '')}", "subsection", after=2, override=prefix)
        elif kind == "instruction":
            add_entry(index, str(block.get("text", "")), "instruction", indent_chars=2, override=preview_format(block, base=block.get("format")))
        elif kind in {"material", "poetry"}:
            _material(block, index, add_entry)
        elif kind == "question":
            _question(block.get("question", {}), index, add_entry, add_mixed, add_columns)
        elif kind == "page_break":
            new_page()

    if page is not None:
        finalize_page(len(pages) + 1)
        target = output / f"page-{len(pages) + 1}.png"
        page.save(target, "PNG", compress_level=1)
        pages.append(target)
    return InternalPreviewResult(tuple(pages), locators, 0, len(pages), tuple(sorted(font_notices)))


def _material(block: dict[str, Any], index: int, add_entry: Callable[..., None]) -> None:
    base = block.get("format", {})
    offset = int(bool(block.get("title"))) + int(bool(block.get("author")))
    def format_at(position):
        return preview_format(block, index=position, base=base)
    if block.get("title"):
        title = str(block["title"])
        title_align = "left" if _MATERIAL_LABEL_RE.fullmatch(title) else "center"
        add_entry(index, title, "material_title", align=title_align, after=1, override=format_at(0))
    if block.get("author"):
        add_entry(index, str(block["author"]), "material_author", align="center", after=1, override=format_at(int(bool(block.get("title")))))
    roles = [str(value) for value in block.get("paragraph_roles", [])]
    formats = {i: format_at(i + offset) for i in range(len(block.get("paragraphs", [])))}
    for paragraph_index, value in enumerate(block.get("paragraphs", [])):
        text = str(value)
        role = roles[paragraph_index] if paragraph_index < len(roles) else "body"
        if _MATERIAL_LABEL_RE.fullmatch(text):
            add_entry(index, text, "material_title", align="left", after=1, override=formats.get(paragraph_index))
        elif role in {"source", "publication_note"} or re.match(r"^\s*[（(].*(?:摘|选|译|发表于|有删改|有改动).*[）)]\s*$", text):
            add_entry(index, text, "material_source", align="right", override=formats.get(paragraph_index))
        elif role == "author":
            add_entry(index, text, "material_author", align="center", override=formats.get(paragraph_index))
        elif block.get("type") == "poetry":
            add_entry(index, text, "poetry", indent_chars=0, override=formats.get(paragraph_index))
        else:
            add_entry(index, text, "material_body", indent_chars=2, override=formats.get(paragraph_index))
    if block.get("note"):
        add_entry(index, str(block["note"]), "material_note", indent_chars=2, override=format_at(offset + len(block.get("paragraphs", []))))
    if block.get("source"):
        add_entry(index, str(block["source"]), "material_source", align="right", override=format_at(offset + len(block.get("paragraphs", [])) + int(bool(block.get("note")))))


def _question(question: dict[str, Any], index: int, add_entry: Callable[..., None], add_mixed: Callable[..., None], add_columns=None) -> None:
    kind = str(question.get("kind", "subjective"))
    role = "objective_stem" if kind == "objective" else "subjective_stem"
    spec = question.get("format") if isinstance(question.get("format"), dict) else None
    def format_for(target, position=0, base=None, prefix=0):
        return preview_format(question, target, position, base=base, prefix=prefix)
    score = question.get("score")
    suffix = ""
    if score is not None:
        value = float(score)
        suffix = f"（{int(value) if value.is_integer() else value}分）"
    prefix = f"{question.get('number', '')}．"
    add_entry(index, f"{prefix}{question.get('stem', '')}{suffix}", role, override=format_for('stem', base=spec, prefix=len(prefix)), indent_chars=0 if kind == "objective" else 1.5, after=1)
    option_spec = _option_spec(question.get("format", {}))
    options = [(str(option), format_for('option', position, option_spec)) for position, option in enumerate(question.get("options", []))]
    if question.get("option_layout") == "two_column" and add_columns:
        add_columns(index, options)
    else:
        for option, option_format in options:
            add_entry(index, option, "choice_option", override=option_format, indent_chars=1.5)
    for position, segments in enumerate(question.get("embedded_segments", [])):
        add_mixed(index, segments, format_for('embedded', position))
    if question.get("segmentation_text"):
        add_entry(index, str(question["segmentation_text"]), "segmentation_text", indent_chars=2, override=format_for('segmentation'))
    for number, text in enumerate(question.get("subquestions", []), start=1):
        marker = f"（{number}）"
        add_entry(index, marker + str(text), "subquestion", indent_chars=2, override=format_for('subquestion', number - 1, {"bold_prefix_length": len(marker)}, prefix=len(marker)))
    for key, role_name in (("composition_material", "composition_material"), ("composition_prompt", "composition_prompt"), ("composition_requirements", "composition_requirements")):
        for position, text in enumerate(question.get(key, [])):
            add_entry(index, str(text), role_name, indent_chars=2, override=format_for(key, position))


__all__ = ["InternalPreviewResult", "render_internal_preview"]
