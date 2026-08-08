"""灵活导入器第三版，保留原生对象并增强语义结构识别。"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from .flexible_importers import parse_plain_lines, save_exam
from .flexible_importers_v2 import _pdf_lines


TITLE_AUTHOR_RE = re.compile(
    r"^(.+?)[\t\u3000 ]{2,}([\u3400-\u9fff·]{2,12})$"
)


def import_exam(path: str | Path) -> dict[str, Any]:
    """导入结构化项目、DOCX、文本型 PDF、TXT 或 Markdown。"""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        result = json.loads(source.read_text(encoding="utf-8"))
    elif suffix == ".docx":
        document = Document(source)
        lines, native_objects = _docx_lines_with_native(document)
        result = parse_plain_lines(lines, source.stem)
        result["metadata"]["source_docx_path"] = str(source.resolve())
        result["metadata"]["native_objects"] = native_objects
    elif suffix == ".pdf":
        result = parse_plain_lines(_pdf_lines(source), source.stem)
    elif suffix in {".txt", ".md", ".markdown"}:
        text = source.read_text(encoding="utf-8-sig")
        result = parse_plain_lines(
            [line.strip() for line in text.splitlines() if line.strip()],
            source.stem,
        )
    else:
        raise ValueError("支持的试题格式为 JSON、DOCX、PDF、TXT 和 Markdown。")
    _normalize_composition(result)
    _normalize_title_authors(result)
    return result


def _docx_lines_with_native(
    document: DocumentObject,
) -> tuple[list[str], list[dict[str, Any]]]:
    """按原顺序提取文字，并用占位符记录表格和独立图片。"""

    lines: list[str] = []
    native: list[dict[str, Any]] = []
    paragraph_index = 0
    table_index = 0
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            drawings = paragraph._p.xpath(".//w:drawing | .//w:pict")
            if text:
                lines.append(text)
            if drawings:
                if text:
                    native.append(
                        {
                            "kind": "drawing",
                            "source_paragraph_index": paragraph_index,
                            "target_text": text,
                        }
                    )
                else:
                    marker = f"[[NATIVE_DRAWING:{paragraph_index}]]"
                    lines.append(marker)
                    native.append(
                        {
                            "kind": "drawing",
                            "source_paragraph_index": paragraph_index,
                            "marker": marker,
                        }
                    )
            paragraph_index += 1
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            marker = f"[[NATIVE_TABLE:{table_index}]]"
            lines.append(marker)
            native.append(
                {
                    "kind": "table",
                    "source_table_index": table_index,
                    "marker": marker,
                    "rows": len(table.rows),
                    "columns": len(table.columns),
                }
            )
            table_index += 1
    return lines, native


def _normalize_composition(result: dict[str, Any]) -> None:
    """把写作题附属段落分成材料、引导语和要求。"""

    for block in result.get("blocks", []):
        question = block.get("question")
        if not question:
            continue
        if question.get("number") != 23 and "写作" not in str(question.get("stem", "")):
            continue
        paragraphs = [
            "".join(str(segment.get("text", "")) for segment in segments)
            for segments in question.get("embedded_segments", [])
        ]
        if not paragraphs:
            continue
        material: list[str] = []
        prompt: list[str] = []
        requirements: list[str] = []
        for text in paragraphs:
            stripped = text.strip()
            if stripped.startswith(("要求：", "要求:")):
                requirements.append(stripped)
            elif "以上材料" in stripped and any(
                cue in stripped
                for cue in ("引发", "联想", "思考", "启示", "感悟", "请写")
            ):
                prompt.append(stripped)
            else:
                material.append(stripped)
        question["composition_material"] = material
        question["composition_prompt"] = prompt
        question["composition_requirements"] = requirements
        question["embedded_segments"] = []


def _normalize_title_authors(result: dict[str, Any]) -> None:
    """识别标题和作者同段的文章与诗歌。"""

    blocks = result.get("blocks", [])
    previous_instruction = ""
    normalized: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "instruction":
            previous_instruction = str(block.get("text", ""))
            normalized.append(block)
            continue
        if block.get("type") != "material":
            normalized.append(block)
            continue
        paragraphs = list(block.get("paragraphs", []))
        matched = [
            (index, match)
            for index, text in enumerate(paragraphs)
            if (match := TITLE_AUTHOR_RE.match(str(text)))
        ]
        if not matched:
            normalized.append(block)
            continue
        if "诗" in previous_instruction:
            for position, (start, match) in enumerate(matched):
                end = matched[position + 1][0] if position + 1 < len(matched) else len(paragraphs)
                body = paragraphs[start + 1 : end]
                note = ""
                if body and str(body[-1]).startswith(("【注】", "[注]", "［注］")):
                    note = str(body.pop())
                normalized.append(
                    {
                        "type": "poetry",
                        "title": match.group(1).strip(),
                        "author": match.group(2).strip(),
                        "paragraphs": body,
                        "note": note,
                    }
                )
        else:
            first_index, first_match = matched[0]
            prefix = paragraphs[:first_index]
            if prefix:
                normalized.append({"type": "material", "paragraphs": prefix})
            updated = dict(block)
            updated["title"] = first_match.group(1).strip()
            updated["author"] = first_match.group(2).strip()
            updated["paragraphs"] = paragraphs[first_index + 1 :]
            normalized.append(updated)
    result["blocks"] = normalized


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
