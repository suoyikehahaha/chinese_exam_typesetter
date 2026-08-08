"""Flexible importer v5 with safer header and poetry recognition."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from docx import Document

from .flexible_importers_v4 import import_exam as import_exam_v4
from .flexible_importers_v4 import parse_plain_lines, save_exam


SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、")
SUBJECT_RE = re.compile(
    r"^\s*语\s*文(?:\s*试\s*题)?(?:\s+|\u3000+)?(.*)$"
)
DATE_RE = re.compile(r"\b20\d{2}(?:[.年/-]\d{1,2}(?:月)?)?\b")
NOTE_RE = re.compile(r"^\s*(?:【\s*注\s*】|\[\s*注\s*\]|［\s*注\s*］|注\s*[：:])")
AUTHOR_RE = re.compile(
    r"^[\u3400-\u9fff·]{2,12}(?:\s*(?:【\s*注\s*】|\[\s*注\s*\]|［\s*注\s*］))?$"
)


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import an exam and normalize headers plus separately written poems."""

    source = Path(path)
    result = import_exam_v4(source)
    if source.suffix.lower() == ".docx":
        _normalize_docx_header(source, result)
    _normalize_poetry_blocks(result)
    _normalize_notes(result)
    return result


def _normalize_docx_header(source: Path, result: dict[str, Any]) -> None:
    document = Document(source)
    lines = [paragraph.text.strip() for paragraph in document.paragraphs]
    lines = [line for line in lines if line]
    first_section = next(
        (index for index, line in enumerate(lines) if SECTION_RE.match(line)),
        min(len(lines), 16),
    )
    header = lines[:first_section]
    if not header:
        return

    notice_index = next(
        (
            index
            for index, line in enumerate(header)
            if re.fullmatch(r"\s*注意事项\s*[：:]?\s*", line)
        ),
        len(header),
    )
    identity_lines = header[:notice_index]
    subject_index: int | None = None
    subject_tail = ""
    for index, line in enumerate(identity_lines):
        match = SUBJECT_RE.match(line)
        if match:
            subject_index = index
            subject_tail = match.group(1).strip()
            break

    exam_name = ""
    if subject_index is not None:
        candidates = identity_lines[:subject_index]
        exam_name = next(
            (
                line
                for line in candidates
                if not _looks_like_meta(line)
            ),
            "",
        )
    if not exam_name:
        exam_name = next(
            (
                line
                for line in identity_lines
                if not SUBJECT_RE.match(line) and not _looks_like_meta(line)
            ),
            str(result.get("metadata", {}).get("exam_name", source.stem)),
        )

    metadata = result.setdefault("metadata", {})
    metadata["exam_name"] = exam_name
    metadata["subject_name"] = "语　文"
    meta_candidates = [
        line
        for index, line in enumerate(identity_lines)
        if index != subject_index and line != exam_name and _looks_like_meta(line)
    ]
    if subject_tail:
        meta_candidates.insert(0, subject_tail)
    metadata["meta_text"] = next(
        (
            value
            for value in meta_candidates
            if value and not SUBJECT_RE.match(value)
        ),
        "",
    )

    header_set = set(identity_lines)
    cleaned: list[dict[str, Any]] = []
    for block in result.get("blocks", []):
        if block.get("type") == "material" and not cleaned:
            updated = dict(block)
            paragraphs = [
                str(value)
                for value in updated.get("paragraphs", [])
                if str(value).strip() not in header_set
                and not SUBJECT_RE.match(str(value).strip())
            ]
            updated["paragraphs"] = paragraphs
            if paragraphs or updated.get("title") or updated.get("author"):
                cleaned.append(updated)
            continue
        cleaned.append(block)
    result["blocks"] = cleaned


def _looks_like_meta(text: str) -> bool:
    return bool(
        DATE_RE.search(text)
        or "满分" in text
        or "考试时间" in text
        or "答题" in text
        or "考生" in text
    )


def _normalize_poetry_blocks(result: dict[str, Any]) -> None:
    blocks = result.get("blocks", [])
    normalized: list[dict[str, Any]] = []
    previous_instruction = ""
    for block in blocks:
        if block.get("type") == "instruction":
            previous_instruction = str(block.get("text", ""))
            normalized.append(block)
            continue
        if block.get("type") != "material" or not any(
            cue in previous_instruction for cue in ("诗", "词", "曲")
        ):
            normalized.append(block)
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        if len(paragraphs) < 3 or not AUTHOR_RE.fullmatch(paragraphs[1]):
            normalized.append(block)
            continue
        title = paragraphs[0]
        author = NOTE_RE.sub("", paragraphs[1]).strip()
        author = re.sub(
            r"(?:【\s*注\s*】|\[\s*注\s*\]|［\s*注\s*］)\s*$",
            "",
            author,
        ).strip()
        body = paragraphs[2:]
        note = ""
        if body and NOTE_RE.match(body[-1]):
            note = body.pop()
        normalized.append(
            {
                "type": "poetry",
                "title": title,
                "author": author,
                "paragraphs": body,
                "note": note,
            }
        )
    result["blocks"] = normalized


def _normalize_notes(result: dict[str, Any]) -> None:
    for block in result.get("blocks", []):
        if block.get("type") not in {"material", "poetry"}:
            continue
        paragraphs = list(block.get("paragraphs", []))
        if paragraphs and NOTE_RE.match(str(paragraphs[-1])):
            block["note"] = str(paragraphs.pop())
            block["paragraphs"] = paragraphs


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
