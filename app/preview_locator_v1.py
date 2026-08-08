"""Locate structured exam blocks inside an internal PDF preview."""

from __future__ import annotations

from pathlib import Path
import unicodedata
from typing import Any

import pypdfium2 as pdfium


Locator = tuple[int, float]


def build_preview_locators(
    pdf_path: str | Path,
    raw_exam: dict[str, Any],
) -> dict[int, Locator]:
    """Return ``block index -> (page index, vertical fraction)`` mappings."""

    document = pdfium.PdfDocument(str(pdf_path))
    page_data: list[tuple[str, list[int], Any, float]] = []
    try:
        for page in document:
            text_page = page.get_textpage()
            raw = text_page.get_text_range()
            normalized, positions = _normalize_with_positions(raw)
            _width, height = page.get_size()
            page_data.append((normalized, positions, text_page, float(height)))

        result: dict[int, Locator] = {}
        cursor_page = 0
        cursor_offset = 0
        for block_index, block in enumerate(raw_exam.get("blocks", [])):
            anchor = _normalize(_block_anchor(block))
            if not anchor:
                continue
            anchor = anchor[:36]
            found = _find_after(page_data, anchor, cursor_page, cursor_offset)
            if found is None and len(anchor) > 14:
                found = _find_after(
                    page_data,
                    anchor[:14],
                    cursor_page,
                    cursor_offset,
                )
            if found is None:
                found = _find_after(page_data, anchor[:14], 0, 0)
            if found is None:
                continue
            page_index, normalized_index = found
            normalized_text, positions, text_page, height = page_data[page_index]
            raw_index = positions[min(normalized_index, len(positions) - 1)]
            try:
                _left, _bottom, _right, top = text_page.get_charbox(raw_index)
                vertical = max(0.0, min(0.94, 1.0 - float(top) / height - 0.035))
            except Exception:
                vertical = 0.0
            result[block_index] = (page_index, vertical)
            cursor_page = page_index
            cursor_offset = normalized_index + min(len(anchor), 14)
        return result
    finally:
        for _text, _positions, text_page, _height in page_data:
            text_page.close()
        document.close()


def _find_after(
    pages: list[tuple[str, list[int], Any, float]],
    anchor: str,
    start_page: int,
    start_offset: int,
) -> tuple[int, int] | None:
    if not anchor:
        return None
    for page_index in range(start_page, len(pages)):
        text = pages[page_index][0]
        offset = start_offset if page_index == start_page else 0
        found = text.find(anchor, offset)
        if found >= 0:
            return page_index, found
    return None


def _block_anchor(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    if block_type in {"section_title", "instruction"}:
        return str(block.get("text", ""))
    if block_type == "subsection":
        return f"{block.get('name', '')}{block.get('meta', '')}"
    if block_type in {"material", "poetry"}:
        if block.get("title"):
            return str(block["title"])
        for text in block.get("paragraphs", []):
            value = str(text).strip()
            if value:
                return value
    if block_type == "question":
        question = block.get("question", {})
        return f"{question.get('number', '')}．{question.get('stem', '')}"
    return ""


def _normalize(text: str) -> str:
    normalized, _positions = _normalize_with_positions(text)
    return normalized


def _normalize_with_positions(text: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    positions: list[int] = []
    replacements = {
        ".": "．",
        "(": "（",
        ")": "）",
        "Ⅰ": "I",
        "Ⅱ": "II",
        "Ⅲ": "III",
        "Ⅳ": "IV",
        "Ⅴ": "V",
    }
    for index, character in enumerate(text):
        if character.isspace():
            continue
        value = replacements.get(character, character)
        value = unicodedata.normalize("NFKC", value)
        for item in value:
            characters.append(item)
            positions.append(index)
    return "".join(characters), positions


__all__ = ["build_preview_locators"]
