"""Flexible importer v11 with answer documents and answer appendices."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .answer_typesetting_v1 import (
    ANSWER_MARKER_RE,
    attach_answer_blocks,
    find_answer_start,
    is_standalone_answer_docx,
    parse_answer_docx,
    standalone_answer_model,
)
from .flexible_importers_v10 import import_exam as import_exam_v10
from .flexible_importers_v10 import parse_plain_lines, save_exam


AUTHOR_RE = re.compile(
    r"^\s*(?:(?:【[^】]{1,8}】|\[[^\]]{1,8}\]|"
    r"（[^）]{1,8}）|\([^)]{1,8}\))\s*)?"
    r"[\u3400-\u9fff·、，,\s]{2,30}\s*$"
)
SOURCE_RE = re.compile(
    r"^\s*[（(]\s*(?:摘自|摘编自|摘选自|选自|节选自|"
    r"改编自|据.+|本文.+|有删改|有改动).+[）)]\s*$"
)


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import either an exam, a standalone answer, or an exam with answers."""

    source = Path(path)
    if source.suffix.lower() == ".docx" and is_standalone_answer_docx(source):
        return standalone_answer_model(parse_answer_docx(source))

    result = import_exam_v10(source)
    if source.suffix.lower() == ".docx":
        _enhance_material_titles_authors(source, result)
        start = find_answer_start(source)
        if start is not None:
            _trim_answer_from_exam(result)
            answer = parse_answer_docx(
                source,
                start_paragraph=start,
                fallback_title=str(result.get("metadata", {}).get("exam_name", "")),
            )
            attach_answer_blocks(result, answer)
    return result


def _enhance_material_titles_authors(
    source: Path,
    result: dict[str, Any],
) -> None:
    document = Document(source)
    centered: set[str] = set()
    centered_bold: set[str] = set()
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text or len(text) > 60:
            continue
        if paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER:
            centered.add(text)
            if any(run.bold for run in paragraph.runs if run.text.strip()):
                centered_bold.add(text)

    for block in result.get("blocks", []):
        if block.get("type") != "material":
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        if not block.get("title") and paragraphs:
            first = paragraphs[0]
            if first in centered_bold and not _is_author(first):
                block["title"] = first
                paragraphs.pop(0)
                block["paragraphs"] = paragraphs
        roles = [str(value) for value in block.get("paragraph_roles", [])]
        if len(roles) != len(paragraphs):
            roles = ["body"] * len(paragraphs)
        formats = list(block.get("paragraph_formats", []))
        for index, text in enumerate(paragraphs):
            if SOURCE_RE.fullmatch(text):
                roles[index] = "source"
                formats.append(_format(index, "仿宋", "右对齐", "source"))
                continue
            if text not in centered:
                continue
            previous_centered = index > 0 and paragraphs[index - 1] in centered
            next_is_author = (
                index + 1 < len(paragraphs)
                and paragraphs[index + 1] in centered
                and _is_author(paragraphs[index + 1])
            )
            if _is_author(text) and previous_centered:
                roles[index] = "author"
                formats.append(_format(index, "仿宋", "居中", "author"))
            elif text in centered_bold or next_is_author:
                roles[index] = "subheading"
                formats.append(_format(index, "黑体", "居中", "subheading"))
        block["paragraph_roles"] = roles
        if formats:
            block["paragraph_formats"] = _deduplicate_formats(formats)


def _trim_answer_from_exam(result: dict[str, Any]) -> None:
    for block in result.get("blocks", []):
        question = block.get("question")
        if not question or int(question.get("number", 0)) != 23:
            continue
        for key in (
            "composition_material",
            "composition_prompt",
            "composition_requirements",
        ):
            kept: list[str] = []
            for value in question.get(key, []):
                text = str(value).strip()
                if ANSWER_MARKER_RE.fullmatch(text):
                    break
                kept.append(str(value))
            question[key] = kept
        question["composition_prompt"] = [
            text
            for text in question.get("composition_prompt", [])
            if not ANSWER_MARKER_RE.fullmatch(str(text).strip())
        ]
        question["composition_requirements"] = [
            text
            for text in question.get("composition_requirements", [])
            if not ANSWER_MARKER_RE.fullmatch(str(text).strip())
        ]


def _is_author(text: str) -> bool:
    value = text.strip()
    if not AUTHOR_RE.fullmatch(value):
        return False
    han = len(re.findall(r"[\u3400-\u9fff]", value))
    return 2 <= han <= 20 and not any(
        mark in value for mark in ("。", "！", "？", "；", "：", "“", "”")
    )


def _format(
    index: int,
    font: str,
    alignment: str,
    role: str,
) -> dict[str, Any]:
    return {
        "target_index": index,
        "semantic_role": role,
        "font": font,
        "size_pt": 10.5,
        "bold": False,
        "alignment": alignment,
        "left_indent_chars": 0,
        "special_indent": "无",
        "special_indent_chars": 0,
        "line_spacing": 1.25,
        "space_before_pt": 0,
        "space_after_pt": 0,
    }


def _deduplicate_formats(
    formats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for spec in reversed(formats):
        key = (
            int(spec.get("target_index", -1)),
            str(spec.get("semantic_role", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(spec)
    result.reverse()
    return result


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
