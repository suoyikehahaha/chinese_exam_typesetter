"""Flexible importer v8 with adaptive literature and score recognition."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .flexible_importers_v7 import import_exam as import_exam_v7
from .flexible_importers_v7 import parse_plain_lines, save_exam


AUTHOR_RE = re.compile(
    r"^(?:\[[^\]]{1,8}\]|［[^］]{1,8}］|〔[^〕]{1,8}〕|（[^）]{1,8}）)?"
    r"[\u3400-\u9fff·]{2,14}$"
)
BODY_LEAD_RE = re.compile(
    r"^(?:[①-⑳㉑-㊿]|\d{1,3}[．.]|[“‘（(《]|[\u3400-\u9fff])"
)
LABEL_RE = re.compile(r"^(?:材料|文本)(?:[一二三四五六七八九十]+|\d+)\s*[：:]")
SCORE_RE = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]\s*$")


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import a source while keeping its wording and adapting structural cues."""

    result = import_exam_v7(path)
    _normalize_separate_title_authors(result)
    _normalize_spaced_scores(result)
    _record_segmentation_markers(result)
    return result


def _normalize_separate_title_authors(result: dict[str, Any]) -> None:
    """Promote short title and author lines in prose reading materials."""

    previous_instruction = ""
    for block in result.get("blocks", []):
        if block.get("type") == "instruction":
            previous_instruction = str(block.get("text", ""))
            continue
        if block.get("type") != "material":
            continue
        if block.get("title") or block.get("author"):
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        if len(paragraphs) < 3:
            continue
        title, author, first_body = paragraphs[:3]
        if any(cue in previous_instruction for cue in ("诗", "词", "曲")):
            continue
        if (
            not title
            or len(title) > 40
            or len(author) > 20
            or LABEL_RE.match(title)
            or not AUTHOR_RE.fullmatch(author)
            or not BODY_LEAD_RE.match(first_body)
        ):
            continue
        block["title"] = title
        block["author"] = author
        block["paragraphs"] = paragraphs[2:]


def _normalize_spaced_scores(result: dict[str, Any]) -> None:
    """Recognize scores such as ``（3 分）`` without altering question text."""

    for block in result.get("blocks", []):
        question = block.get("question")
        if not question or question.get("score") is not None:
            continue
        stem = str(question.get("stem", ""))
        match = SCORE_RE.search(stem)
        if not match:
            continue
        question["score"] = float(match.group(1))
        question["stem"] = stem[: match.start()].rstrip()


def _record_segmentation_markers(result: dict[str, Any]) -> None:
    """Record exactly the uppercase markers found in each segmentation passage."""

    for block in result.get("blocks", []):
        question = block.get("question")
        if not question or not question.get("segmentation_text"):
            continue
        text = str(question["segmentation_text"])
        question["segmentation_markers"] = re.findall(r"[A-Z]", text)


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
