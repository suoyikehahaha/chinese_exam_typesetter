"""Semantic internal preview with per-question format overrides."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from docx import Document
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

from .config import load_layout
from .internal_preview_v01 import _FONT_FILES, _font, _wrap_text
from .page_target_v01 import adjusted_layout, get_target_pages


@dataclass(frozen=True, slots=True)
class InternalPreviewResult:
    pages: tuple[Path, ...]
    locators: dict[int, tuple[int, float]]
    target_pages: int
    actual_pages: int


_MATERIAL_LABEL_RE = re.compile(
    r"^\s*材料(?:[一二三四五六七八九十百]+|\d+)\s*[：:]?\s*$"
)
_NATIVE_DRAWING_RE = re.compile(r"^\[\[NATIVE_DRAWING:\d+\]\]$")


@dataclass
class _SourceVisuals:
    """Source-level character marks and drawing images used by the preview."""

    decorations: dict[str, list[dict[str, Any]]]
    images: dict[str, list[list[Image.Image]]]
    decoration_cursor: dict[str, int]
    image_cursor: dict[str, int]

    def marks_for(self, text: str) -> list[dict[str, Any]]:
        key = str(text).strip()
        entries = self.decorations.get(key, [])
        position = self.decoration_cursor.get(key, 0)
        if position >= len(entries):
            return []
        self.decoration_cursor[key] = position + 1
        return list(entries[position].get("ranges", []))

    def images_for(self, text: str) -> list[Image.Image]:
        key = str(text).strip()
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
            decorations[str(entry["text"]).strip()].append(entry)
    images: dict[str, list[list[Image.Image]]] = defaultdict(list)
    source_path = Path(str(metadata.get("source_docx_path", "")))
    if not source_path.is_file():
        return _SourceVisuals(dict(decorations), dict(images), {}, {})
    try:
        document = Document(source_path)
        markers = {
            int(item.get("source_paragraph_index", -1)): str(item.get("marker", ""))
            for item in metadata.get("native_objects", [])
            if isinstance(item, dict) and item.get("marker")
        }
        for paragraph_index, paragraph in enumerate(document.paragraphs):
            blips = paragraph._p.xpath(".//a:blip")
            if not blips:
                continue
            key = paragraph.text.strip() or markers.get(paragraph_index, "")
            if not key:
                continue
            group: list[Image.Image] = []
            for blip in blips:
                relationship = blip.get(qn("r:embed"))
                part = document.part.related_parts.get(relationship)
                if part is None:
                    continue
                try:
                    with Image.open(BytesIO(part.blob)) as source:
                        group.append(source.convert("RGBA"))
                except (OSError, ValueError):
                    continue
            if group:
                images[key].append(group)
    except (OSError, ValueError, RuntimeError):
        # Preview must remain usable when a source document is unavailable or
        # contains a drawing format that Pillow cannot decode.
        pass
    return _SourceVisuals(dict(decorations), dict(images), {}, {})


def _mark_active(ranges: list[dict[str, Any]], position: int, key: str) -> bool:
    return any(
        int(mark.get("start", 0)) <= position < int(mark.get("end", 0))
        and bool(mark.get(key))
        for mark in ranges
    )


def _wrap_rich_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    bold_font: ImageFont.ImageFont,
    width: int,
    ranges: list[dict[str, Any]],
) -> list[tuple[str, int, int]]:
    """Wrap text while retaining source character positions for decorations."""

    result: list[tuple[str, int, int]] = []
    offset = 0
    for raw_line in str(text).splitlines() or [""]:
        if not raw_line:
            result.append(("", offset, offset))
            offset += 1
            continue
        current: list[str] = []
        current_width = 0.0
        line_start = offset
        for position, character in enumerate(raw_line):
            absolute = offset + position
            chosen = bold_font if _mark_active(ranges, absolute, "bold") else font
            character_width = draw.textlength(character, font=chosen)
            if current and current_width + character_width > width:
                result.append(("".join(current), line_start, absolute))
                current = []
                current_width = 0.0
                line_start = absolute
            current.append(character)
            current_width += character_width
        result.append(("".join(current), line_start, offset + len(raw_line)))
        offset += len(raw_line) + 1
    return result


def _draw_rich_line(
    draw: ImageDraw.ImageDraw,
    line: str,
    start: int,
    y: int,
    x: float,
    font: ImageFont.ImageFont,
    bold_font: ImageFont.ImageFont,
    ranges: list[dict[str, Any]],
) -> None:
    """Draw a line with source bold, underline and emphasis marks."""

    cursor = x
    position = start
    segment: list[str] = []
    segment_bold = False
    segment_underline = False
    segment_emphasis = False

    def flush() -> None:
        nonlocal cursor, segment
        if not segment:
            return
        value = "".join(segment)
        chosen = bold_font if segment_bold else font
        draw.text((cursor, y), value, fill="#111111", font=chosen)
        width = draw.textlength(value, font=chosen)
        if segment_underline:
            baseline = y + max(2, int(getattr(chosen, "size", 12))) + 1
            draw.line((cursor, baseline, cursor + width, baseline), fill="#111111", width=1)
        if segment_emphasis:
            dot_y = y + max(2, int(getattr(chosen, "size", 12))) + 3
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
        if segment and (bold, underline, emphasis) != (segment_bold, segment_underline, segment_emphasis):
            flush()
        if not segment:
            segment_bold, segment_underline, segment_emphasis = bold, underline, emphasis
        segment.append(character)
        position += 1
    flush()


def render_internal_preview(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
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
    page: Image.Image | None = None
    draw: ImageDraw.ImageDraw | None = None
    y = top
    source_visuals = _load_source_visuals(raw_exam)

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
            page.save(target, "PNG")
            pages.append(target)
        page = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(page)
        y = top

    def draw_source_images(images: list[Image.Image]) -> None:
        nonlocal y
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
    ) -> None:
        nonlocal y
        spec = dict(layout.get("styles", {}).get(role, layout.get("styles", {}).get("material_body", {})))
        if override:
            spec.update({key: value for key, value in override.items() if value is not None})
        font_name = str(spec.get("font", "SimSun"))
        size_pt = float(spec.get("size_pt", 10.5))
        semantic_bold = bool(spec.get("bold", False))
        font = _font(font_name, size_pt, semantic_bold)
        line_spacing = float(spec.get("line_spacing", layout.get("defaults", {}).get("body_line_spacing", 1.25)))
        line_height = max(14, int(font.size * line_spacing + 3))
        indent = float(spec.get("first_line_indent_chars", 0) if indent_chars is None else indent_chars)
        indent_px = int(size_pt * indent * 1.2)
        alignment = align or str(spec.get("alignment", "left"))
        source_images = source_visuals.images_for(text)
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
        drawing = draw or ImageDraw.Draw(Image.new("RGB", (1, 1)))
        bold_font = _font(font_name, size_pt, True)
        if marks:
            line_infos = _wrap_rich_text(
                drawing,
                text,
                font,
                bold_font,
                right - left - indent_px,
                marks,
            )
        else:
            line_infos = [
                (line, 0, 0)
                for line in _wrap_text(drawing, text, font, right - left - indent_px)
            ]
        if not line_infos:
            line_infos = [("", 0, 0)]
        if page is None:
            new_page()
        for line_index, (line, line_start, _line_end) in enumerate(line_infos):
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
                        font=(bold_font if _mark_active(marks, line_start + offset, "bold") else font),
                    )
                    for offset, character in enumerate(line)
                )
            else:
                line_width = drawing.textlength(line, font=font)
            x = left + (indent_px if line_index == 0 else 0)
            if alignment in {"center", "居中"}:
                x = left + (right - left - line_width) / 2
            elif alignment in {"right", "右对齐"}:
                x = right - line_width
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
                    )
                else:
                    draw.text((x, y), line, fill="#111111", font=font)
            y += line_height
        if source_images:
            draw_source_images(source_images)
        y += int(float(spec.get("space_after_pt", 0)) * 1.3 + after)

    def add_mixed(block_index: int, segments: Iterable[dict[str, Any]], override: dict[str, Any] | None = None) -> None:
        add_entry(block_index, "".join(str(item.get("text", "")) for item in segments), "embedded_body", override=override, indent_chars=2)

    new_page()
    metadata = raw_exam.get("metadata", {})
    add_entry(-1, str(metadata.get("exam_name", "高中语文试卷")), "exam_name", align="center", after=4)
    add_entry(-1, str(metadata.get("subject_name", "语　文")), "subject_name", align="center", after=4)
    if str(metadata.get("meta_text", "")).strip():
        add_entry(-1, str(metadata.get("meta_text", "")), "exam_meta", align="center", after=2)
    notices = [str(item) for item in metadata.get("notices", []) if str(item).strip()]
    if notices:
        add_entry(-1, "注意事项：", "notice_title", after=1)
        for number, notice in enumerate(notices, start=1):
            add_entry(-1, f"{number}．{notice}", "notice_body", indent_chars=2)

    for index, block in enumerate(raw_exam.get("blocks", [])):
        kind = str(block.get("type", ""))
        if kind == "section_title":
            add_entry(index, str(block.get("text", "")), "section_title", after=3)
        elif kind == "subsection":
            add_entry(index, f"{block.get('name', '')}{block.get('meta', '')}", "subsection", after=2)
        elif kind == "instruction":
            add_entry(index, str(block.get("text", "")), "instruction", indent_chars=2)
        elif kind in {"material", "poetry"}:
            _material(block, index, add_entry)
        elif kind == "question":
            _question(block.get("question", {}), index, add_entry, add_mixed)
        elif kind == "page_break":
            new_page()

    if page is not None:
        finalize_page(len(pages) + 1)
        target = output / f"page-{len(pages) + 1}.png"
        page.save(target, "PNG")
        pages.append(target)
    return InternalPreviewResult(tuple(pages), locators, get_target_pages(raw_exam), len(pages))


def _material(block: dict[str, Any], index: int, add_entry: Callable[..., None]) -> None:
    if block.get("title"):
        title = str(block["title"])
        title_align = "left" if _MATERIAL_LABEL_RE.fullmatch(title) else "center"
        add_entry(index, title, "material_title", align=title_align, after=1)
    if block.get("author"):
        add_entry(index, str(block["author"]), "material_author", align="center", after=1)
    roles = [str(value) for value in block.get("paragraph_roles", [])]
    formats = {int(item.get("target_index", -1)): item for item in block.get("paragraph_formats", []) if isinstance(item, dict)}
    for paragraph_index, value in enumerate(block.get("paragraphs", [])):
        text = str(value)
        role = roles[paragraph_index] if paragraph_index < len(roles) else "body"
        if _MATERIAL_LABEL_RE.fullmatch(text):
            add_entry(index, text, "material_title", align="left", after=1)
        elif role in {"source", "publication_note"} or re.match(r"^\s*[（(].*(?:摘|选|译|发表于|有删改|有改动).*[）)]\s*$", text):
            add_entry(index, text, "material_source", align="right", override=formats.get(paragraph_index))
        elif role == "author":
            add_entry(index, text, "material_author", align="center", override=formats.get(paragraph_index))
        else:
            add_entry(index, text, "material_body", indent_chars=2, override=formats.get(paragraph_index))
    if block.get("note"):
        add_entry(index, str(block["note"]), "material_note", indent_chars=2)
    if block.get("source"):
        add_entry(index, str(block["source"]), "material_source", align="right")


def _question(question: dict[str, Any], index: int, add_entry: Callable[..., None], add_mixed: Callable[..., None]) -> None:
    kind = str(question.get("kind", "subjective"))
    role = "objective_stem" if kind == "objective" else "subjective_stem"
    spec = question.get("format") if isinstance(question.get("format"), dict) else None
    score = question.get("score")
    suffix = ""
    if score is not None:
        value = float(score)
        suffix = f"（{int(value) if value.is_integer() else value}分）"
    add_entry(index, f"{question.get('number', '')}．{question.get('stem', '')}{suffix}", role, override=spec, indent_chars=0 if kind == "objective" else 1.5, after=1)
    option_spec = {
        "font": question.get("format", {}).get("option_font"),
        "size_pt": question.get("format", {}).get("option_size_pt"),
        "left_indent_chars": question.get("format", {}).get("option_left_indent_chars"),
        "first_line_indent_chars": -float(question.get("format", {}).get("option_hanging_indent_chars", 1.7)),
    } if isinstance(question.get("format"), dict) else None
    for option in question.get("options", []):
        add_entry(index, str(option), "choice_option", override=option_spec, indent_chars=1.5)
    for segments in question.get("embedded_segments", []):
        add_mixed(index, segments, spec)
    if question.get("segmentation_text"):
        add_entry(index, str(question["segmentation_text"]), "segmentation_text", indent_chars=2, override=spec)
    for number, text in enumerate(question.get("subquestions", []), start=1):
        add_entry(index, f"（{number}）{text}", "subquestion", indent_chars=2, override=spec)
    for key, role_name in (("composition_material", "composition_material"), ("composition_prompt", "composition_prompt"), ("composition_requirements", "composition_requirements")):
        for text in question.get(key, []):
            add_entry(index, str(text), role_name, indent_chars=2, override=spec)


__all__ = ["InternalPreviewResult", "render_internal_preview"]
