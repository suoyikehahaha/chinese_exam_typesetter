"""灵活导入器第二版，优化短篇 PDF 与扫描件判断。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader

from .flexible_importers import _docx_lines, parse_plain_lines, save_exam


def import_exam(path: str | Path) -> dict[str, Any]:
    """导入 JSON、DOCX、TXT、Markdown 或文本型 PDF。"""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        return json.loads(source.read_text(encoding="utf-8"))
    if suffix == ".docx":
        return parse_plain_lines(list(_docx_lines(Document(source))), source.stem)
    if suffix == ".pdf":
        return parse_plain_lines(_pdf_lines(source), source.stem)
    if suffix in {".txt", ".md", ".markdown"}:
        text = source.read_text(encoding="utf-8-sig")
        return parse_plain_lines(
            [line.strip() for line in text.splitlines() if line.strip()],
            source.stem,
        )
    raise ValueError("支持的试题格式为 JSON、DOCX、PDF、TXT 和 Markdown。")


def _pdf_lines(path: Path) -> list[str]:
    reader = PdfReader(path)
    lines: list[str] = []
    page_char_counts: list[int] = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        page_char_counts.append(len(text.strip()))
        lines.extend(line.strip() for line in text.splitlines() if line.strip())
    total_chars = sum(page_char_counts)
    low_text_pages = sum(count < 20 for count in page_char_counts)
    mostly_empty = bool(
        page_char_counts and low_text_pages / len(page_char_counts) > 0.8
    )
    if total_chars < 20 or mostly_empty:
        raise ValueError(
            "该 PDF 可提取文字过少，可能是扫描版。当前版本需要先进行 OCR，"
            "或导入可编辑 DOCX、文本型 PDF。"
        )
    return lines
