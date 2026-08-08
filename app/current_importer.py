"""Word-only importer facade used by the current desktop runtime."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile
from typing import Any

from .flexible_importers_v12 import import_exam as import_exam_legacy
from .models.identity import ensure_block_ids
from .office_bridge_v01 import convert_doc_to_docx


_AUTHOR_RE = re.compile(r"^[\u3400-\u9fff·、，,\s]{2,30}$")
_TITLE_AUTHOR_RE = re.compile(
    r"^(?P<title>《[^》]+》|[^|｜\t]{2,40})"
    r"(?:\s{2,}|　{1,}|[|｜\t]+)"
    r"(?P<author>[\u3400-\u9fff·、，,\s]{2,30})$"
)
_SOURCE_RE = re.compile(
    r"^\s*[（(].*(?:摘自|摘编自|选自|节选自|译|发表于|刊于|载于|有删改|有改动).*[）)]\s*$"
)


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import a DOCX or legacy DOC through the current facade.

    Historical importers still exist for old projects, while the current
    desktop file picker intentionally exposes only Word documents.
    """

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in {".docx", ".doc"}:
        raise ValueError(
            "当前版本只支持导入 Word 文档（.docx 或 .doc）。"
            "PDF、TXT、Markdown 和 JSON 不属于当前桌面导入格式。"
        )
    if suffix == ".doc":
        with tempfile.TemporaryDirectory(prefix="exam-doc-convert-") as folder:
            converted = Path(folder) / f"{source.stem}.docx"
            convert_doc_to_docx(source, converted)
            result = import_exam_legacy(converted)
    else:
        result = import_exam_legacy(source)
    ensure_block_ids(result)
    result.setdefault("metadata", {})["source_format"] = suffix[1:]
    diagnostics: list[dict[str, Any]] = list(result.get("diagnostics", []))
    _normalize_same_line_title_authors(result, diagnostics)
    _normalize_publication_notes(result)
    result["diagnostics"] = diagnostics
    return result


def _normalize_same_line_title_authors(
    result: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> None:
    """Split centered-style ``title  author`` lines when evidence is strong."""

    for index, block in enumerate(result.get("blocks", [])):
        if not isinstance(block, dict) or block.get("type") not in {"material", "poetry"}:
            continue
        if block.get("title") or block.get("author"):
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        if not paragraphs:
            continue
        match = _TITLE_AUTHOR_RE.fullmatch(paragraphs[0])
        if not match or not _is_author(match.group("author")):
            continue
        title = match.group("title").strip()
        author = match.group("author").strip()
        if _looks_like_label(title) or _looks_like_body(title):
            continue
        block["title"] = title
        block["author"] = author
        block["paragraphs"] = paragraphs[1:]
        roles = list(block.get("paragraph_roles", []))
        if len(roles) != len(paragraphs):
            roles = ["body"] * len(paragraphs)
        block["paragraph_roles"] = roles[1:]
        formats = list(block.get("paragraph_formats", []))
        formats.append(_role_format("author", 0, "仿宋", "居中"))
        block["paragraph_formats"] = formats
        diagnostics.append(
            {
                "code": "same-line-title-author",
                "block": block.get("id", f"block-{index + 1}"),
                "message": "已根据分隔符和作者文字特征拆分同一行标题与作者",
            }
        )


def _normalize_publication_notes(result: dict[str, Any]) -> None:
    """Ensure article-end parenthetical notes use the source-note role."""

    for block in result.get("blocks", []):
        if not isinstance(block, dict) or block.get("type") not in {"material", "poetry"}:
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        roles = list(block.get("paragraph_roles", []))
        if len(roles) != len(paragraphs):
            roles = ["body"] * len(paragraphs)
        formats = list(block.get("paragraph_formats", []))
        for index, text in enumerate(paragraphs):
            if _SOURCE_RE.fullmatch(text):
                roles[index] = "source"
                formats = [
                    item
                    for item in formats
                    if int(item.get("target_index", -1)) != index
                ]
                formats.append(_role_format("source", index, "仿宋", "右对齐"))
        block["paragraph_roles"] = roles
        if formats:
            block["paragraph_formats"] = formats


def _role_format(
    role: str,
    index: int,
    font: str,
    alignment: str,
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


def _is_author(value: str) -> bool:
    text = value.strip()
    if not _AUTHOR_RE.fullmatch(text):
        return False
    return 2 <= len(re.findall(r"[\u3400-\u9fff]", text)) <= 20


def _looks_like_label(value: str) -> bool:
    return bool(re.match(r"^(?:材料|文本|文段|注释|说明)[一二三四五六七八九十0-9：:]", value))


def _looks_like_body(value: str) -> bool:
    return len(value) > 24 or value.endswith(("。", "！", "？", "；"))


__all__ = ["import_exam"]
