"""Flexible importer v7 with confidentiality-aware title selection."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from docx import Document

from .flexible_importers_v6 import import_exam as import_exam_v6
from .flexible_importers_v6 import parse_plain_lines, save_exam


SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、")
SUBJECT_RE = re.compile(r"^\s*语\s*文(?:\s*试\s*题)?(?:\s+.*)?$")
CONFIDENTIAL_RE = re.compile(r"(?:保密|绝密|启用前|试题类型)")
TITLE_CUES = ("考试", "检测", "测试", "试卷", "模拟")
HEADER_NOTICE_RE = re.compile(r"^\s*(?:注意事项|考生须知)\s*[：:]?\s*$")
HEADER_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[.．、]\s*")
HEADER_INFO_CUES = ("考试时间", "试卷满分", "考试用时", "本试卷共")


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import an exam and refine the formal title from DOCX headers."""

    source = Path(path)
    result = import_exam_v6(source)
    if source.suffix.lower() == ".docx":
        _refine_exam_name(source, result)
    return result


def _refine_exam_name(source: Path, result: dict[str, Any]) -> None:
    lines = [
        paragraph.text.strip()
        for paragraph in Document(source).paragraphs
        if paragraph.text.strip()
    ]
    first_section = next(
        (index for index, line in enumerate(lines) if SECTION_RE.match(line)),
        min(len(lines), 16),
    )
    header = lines[:first_section]
    subject_index = next(
        (index for index, line in enumerate(header) if SUBJECT_RE.match(line)),
        len(header),
    )
    candidates = [
        line
        for line in header[:subject_index]
        if not CONFIDENTIAL_RE.search(line)
        and not re.fullmatch(r"20\d{2}(?:[.\u5e74/-]\d{1,2}(?:\u6708)?)?", line)
    ]
    # Notice headings, numbered notice items, and exam metadata are header
    # content. Excluding them prevents the last notice item from becoming the
    # formal exam title when a document has no separate title line.
    title_candidates = [
        line
        for line in candidates
        if not HEADER_NOTICE_RE.fullmatch(line)
        and not HEADER_ITEM_RE.match(line)
        and not any(cue in line for cue in HEADER_INFO_CUES)
    ]
    formal = next(
        (
            line
            for line in reversed(title_candidates)
            if any(cue in line for cue in TITLE_CUES)
        ),
        title_candidates[0] if title_candidates else candidates[0] if candidates else "",
    )
    if formal:
        result.setdefault("metadata", {})["exam_name"] = formal
    confidentiality = [
        line for line in header[:subject_index] if CONFIDENTIAL_RE.search(line)
    ]
    if confidentiality:
        result.setdefault("metadata", {})["confidentiality_text"] = confidentiality[0]


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
