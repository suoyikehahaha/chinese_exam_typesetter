"""Flexible importer v12 with article-end publication-note semantics."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .flexible_importers_v11 import import_exam as import_exam_v11
from .flexible_importers_v11 import parse_plain_lines, save_exam


PUBLICATION_NOTE_RE = re.compile(
    r"^\s*[（(]"
    r"(?=[^）)]*(?:译|发表于|刊于|载于|原载|初刊|首刊|有删改|有改动))"
    r"(?!\s*(?:摘自|摘编自|摘选自|选自|节选自|改编自|据|来源))"
    r"[^）)]+[）)]\s*$"
)


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import a document and mark parenthetical article-end notes."""

    result = import_exam_v11(path)
    mark_publication_notes(result)
    return result


def mark_publication_notes(result: dict[str, Any]) -> None:
    """Mark publication or translation notes without changing source lines."""

    for block in result.get("blocks", []):
        if block.get("type") != "material":
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        roles = [str(value) for value in block.get("paragraph_roles", [])]
        if len(roles) != len(paragraphs):
            roles = ["body"] * len(paragraphs)
        formats = list(block.get("paragraph_formats", []))
        changed = False
        for index, text in enumerate(paragraphs):
            if roles[index] == "source" or not PUBLICATION_NOTE_RE.fullmatch(text):
                continue
            roles[index] = "publication_note"
            formats = [
                spec
                for spec in formats
                if int(spec.get("target_index", -1)) != index
            ]
            formats.append(_publication_note_format(index))
            changed = True
        if changed:
            block["paragraph_roles"] = roles
            block["paragraph_formats"] = formats


def _publication_note_format(index: int) -> dict[str, Any]:
    return {
        "target_index": index,
        "semantic_role": "publication_note",
        "font": "宋体",
        "size_pt": 10.5,
        "bold": False,
        "alignment": "右对齐",
        "left_indent_chars": 0,
        "special_indent": "无",
        "special_indent_chars": 0,
        "line_spacing": 1.25,
        "space_before_pt": 0,
        "space_after_pt": 0,
    }


__all__ = [
    "PUBLICATION_NOTE_RE",
    "import_exam",
    "mark_publication_notes",
    "parse_plain_lines",
    "save_exam",
]
