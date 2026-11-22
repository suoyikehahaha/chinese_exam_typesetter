"""Canonical context-aware importer for the supported Word document formats.

The module keeps saved-draft compatibility for JSON, TXT, and Markdown while
the desktop facade handles DOCX and legacy DOC conversion.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Iterator

from docx import Document as OpenDocument
from .document_read_session import Document, document_read_session
from docx.document import Document as DocumentObject
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from .answer_typesetting import (
    ANSWER_MARKER_RE,
    attach_answer_blocks,
    find_answer_start,
    is_standalone_answer_docx,
    parse_answer_docx,
    standalone_answer_model,
)
from .models.identity import ensure_block_ids
from .layout_presets import DEFAULT_PRESET
from .import_material_roles import _is_author
from .word_numbering import NumberingLevel, _format_prefix, _numbering_definitions, materialize_automatic_numbering
from .drafts import save_draft as save_exam




# merged from flexible_importers.py


QUESTION_RE = re.compile(r"^\s*(\d{1,2})\s*[\uff0e.\u3001]\s*(.+)$")

OPTION_START_RE = re.compile(r"^\s*[A-D]\s*[\uff0e.\u3001]")

INLINE_OPTION_RE = re.compile(
    r"([A-D])\s*[\uff0e.\u3001]\s*(.*?)(?=(?:[A-D])\s*[\uff0e.\u3001]|$)"
)

SECTION_RE = re.compile(r"^\s*[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\u3001")

SUBSECTION_RE = re.compile(r"^\s*\uff08[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\uff09")

SCORE_RE = re.compile(r"[\uff08(](\d+(?:\.\d+)?)\u5206[\uff09)]\s*$")

NOTICE_RE = re.compile(r"^\s*\u6ce8\u610f\u4e8b\u9879\s*[\uff1a:]?\s*$")

NOTICE_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[\uff0e.\u3001]\s*(.+)$")

QUESTION_CUES = (
    "\u4e0b\u5217",
    "\u8bf7",
    "\u7b80\u8981",
    "\u6982\u62ec",
    "\u5206\u6790",
    "\u6839\u636e",
    "\u5982\u4f55",
    "\u4e3a\u4ec0\u4e48",
    "\u54ea\u4e9b",
    "\u8865\u5199",
    "\u7ffb\u8bd1",
    "\u5199\u51fa",
    "\u9009\u51fa",
    "\u6307\u51fa",
    "\u5b8c\u6210",
    "\u9605\u8bfb\u4e0b\u9762",
    "\u6700\u6070\u5f53",
    "\u4e0d\u6b63\u786e",
    "\u6b63\u786e\u7684\u4e00\u9879",
)

def _docx_lines(document: DocumentObject) -> Iterator[str]:
    """\u6309\u6587\u6863\u987a\u5e8f\u63d0\u53d6\u6b63\u6587\u6bb5\u843d\u548c\u8868\u683c\u884c\u3002"""

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text.strip()
            if text:
                yield text
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                text = "\t".join(item for item in cells if item)
                if text:
                    yield text

def parse_plain_lines(lines: Iterable[str], title: str = "\u9ad8\u4e2d\u8bed\u6587\u8bd5\u5377") -> dict[str, Any]:
    """\u4f7f\u7528\u5377\u9996\u72b6\u6001\u3001\u8fde\u7eed\u9898\u53f7\u548c\u9898\u578b\u8bc1\u636e\u89e3\u6790\u666e\u901a\u6587\u672c\u3002"""

    clean_lines = []
    for value in lines:
        line = str(value).strip()
        if not line:
            continue
        notice = re.match(r"^(注意事项|考生须知)\s*[：:]\s*(.+)$", line)
        clean_lines.extend([notice.group(1) + "：", notice.group(2)] if notice else [line])
    if not clean_lines:
        raise ValueError("\u6587\u4ef6\u4e2d\u6ca1\u6709\u53ef\u8bc6\u522b\u7684\u6587\u5b57\u3002")

    metadata = _metadata_from_header(clean_lines, title)
    blocks: list[dict[str, Any]] = []
    pending_material: list[str] = []
    current_question: dict[str, Any] | None = None
    questions_started = False
    expected_number = 1
    in_notice = False
    first_section_seen = False
    awaiting_material = False
    supplementary = False

    def flush_material() -> None:
        nonlocal pending_material
        if pending_material:
            blocks.append({"type": "material", "paragraphs": pending_material})
            pending_material = []

    for index, line in enumerate(clean_lines):
        if NOTICE_RE.match(line):
            in_notice = True
            current_question = None
            continue

        if SECTION_RE.match(line):
            if current_question is not None and not any(cue in line for cue in ("阅读", "文言文", "诗歌", "默写", "名篇名句", "语言文字", "写作", "作文")):
                current_question.setdefault("embedded_segments", []).append([{"text": line, "role": "body"}])
                continue
            if awaiting_material and not any(cue in line for cue in ("阅读", "默写", "名篇名句", "语言文字", "写作", "作文")):
                pending_material.append(line)
                continue
            flush_material()
            awaiting_material = False
            in_notice = False
            first_section_seen = True
            supplementary = False
            current_question = None
            blocks.append({"type": "section_title", "text": line})
            continue

        if in_notice:
            notice_match = NOTICE_ITEM_RE.match(line)
            if notice_match:
                metadata["notices"].append(notice_match.group(1))
                continue
            if SUBSECTION_RE.match(line) or line.startswith("\u9605\u8bfb\u4e0b\u9762"):
                in_notice = False
            elif not _looks_like_question(line, clean_lines, index):
                metadata["notices"].append(line)
                continue

        if SUBSECTION_RE.match(line):
            flush_material()
            current_question = None
            name, meta = _split_subsection(line)
            blocks.append({"type": "subsection", "name": name, "meta": meta})
            continue

        if "附加题" in line and re.search(r"不计入(?:总分|分数|成绩)", line):
            flush_material()
            current_question = None
            supplementary = True
            blocks.append({"type": "instruction", "text": line})
            continue

        if line.startswith("\u9605\u8bfb\u4e0b\u9762"):
            flush_material()
            current_question = None
            awaiting_material = True
            blocks.append({"type": "instruction", "text": line})
            continue

        question_match = QUESTION_RE.match(line)
        if question_match:
            number = int(question_match.group(1))
            stem = question_match.group(2)
            confidence = _question_confidence(stem, clean_lines, index)
            should_accept = False
            if number == expected_number:
                should_accept = questions_started or confidence >= 3
            elif questions_started and number > expected_number and confidence >= 4:
                should_accept = True
            elif (not questions_started or current_question is None) and (confidence >= 4 or (first_section_seen and confidence >= 3 and SCORE_RE.search(stem))):
                should_accept = True

            if should_accept:
                flush_material()
                score_match = SCORE_RE.search(stem)
                score = float(score_match.group(1)) if score_match else None
                if score_match:
                    stem = (stem[: score_match.start()] + stem[score_match.end():]).strip()
                current_question = {
                    "number": number,
                    "kind": "subjective",
                    "stem": stem,
                    "score": score,
                    "options": [],
                    "counts_toward_total": not supplementary,
                }
                blocks.append({"type": "question", "question": current_question})
                questions_started = True
                awaiting_material = False
                expected_number = number + 1
                continue

        inline_options = _extract_options(line)
        if current_question is not None and inline_options:
            current_question["kind"] = "objective"
            current_question["options"].extend(inline_options)
            continue

        if current_question is not None:
            segments = current_question.setdefault("embedded_segments", [])
            segments.append([{"text": line, "role": "body"}])
        elif first_section_seen or not _is_header_line(line, metadata):
            pending_material.append(line)

    flush_material()
    question_numbers = [
        block["question"]["number"]
        for block in blocks
        if block.get("type") == "question"
    ]
    if not question_numbers and not any(
        any(cue in str(block.get("text", "")) + str(block.get("name", "")) for cue in ("文言文", "诗歌", "默写", "名篇名句"))
        for block in blocks if block.get("type") in {"section_title", "subsection", "instruction"}
    ):
        raise ValueError(
            "\u6ca1\u6709\u8bc6\u522b\u5230\u9898\u76ee\u3002\u5efa\u8bae\u68c0\u67e5\u9898\u53f7\u662f\u5426\u4f7f\u7528\u201c1\uff0e\u9898\u5e72\u201d\u5f62\u5f0f\uff0c"
            "\u6216\u5148\u4fdd\u5b58\u4e3a\u53ef\u590d\u5236\u6587\u5b57\u7684 DOCX\u3001PDF\u3002"
        )
    _recover_question_scores(blocks)
    if not metadata.get("declared_total_score"):
        metadata["total_score"] = _score_total_or_default(blocks, metadata["total_score"])
    # A selection preserves the source numbering and declared score.
    header_text = " ".join(clean_lines[:3]) + " " + title
    practice = bool(re.search(r"限时训练|专项|专题训练|片段练习", header_text))
    practice = practice or (len(question_numbers) < 20 and not any(
        "写作" in str(block.get("text", "")) for block in blocks
    ) and not (metadata.get("declared_total_score") and metadata["total_score"] >= 150))
    metadata["document_scope"] = "practice" if practice else "full_exam"
    if practice:
        metadata["target_pages"] = 0
        if not metadata.get("declared_total_score"):
            metadata["total_score"] = sum(
                float(b["question"].get("score") or 0) for b in blocks if b.get("type") == "question" and b["question"].get("counts_toward_total", True)
            )
    return {"metadata": metadata, "blocks": blocks}


def _recover_question_scores(blocks: list[dict[str, Any]]) -> None:
    """Use explicit late score labels and unambiguous single-question headings."""
    heading_score = None
    group = []

    def finish():
        if heading_score is not None and len(group) == 1 and group[0].get("score") is None:
            group[0]["score"] = heading_score

    for block in blocks:
        if block.get("type") in {"section_title", "subsection"}:
            finish()
            group = []
            label = str(block.get("text", "")) + str(block.get("meta", "")) + str(block.get("name", ""))
            match = re.search(r"共\s*1\s*小题[，,、]\s*(\d+(?:\.\d+)?)\s*分", label)
            heading_score = float(match.group(1)) if match else None
        question = block.get("question")
        if not question:
            continue
        group.append(question)
        if question.get("score") is None:
            values = []
            for segments in question.get("embedded_segments", []):
                text = "".join(s.get("text", "") for s in segments)
                if not SUBQUESTION_RE.match(text):
                    match = SCORE_RE.search(text)
                    if match:
                        values.append(float(match.group(1)))
            if len(values) == 1:
                question["score"] = values[0]
    finish()

def _metadata_from_header(lines: list[str], fallback_title: str) -> dict[str, Any]:
    first_section = next(
        (index for index, line in enumerate(lines) if SECTION_RE.match(line)),
        min(len(lines), 12),
    )
    header = lines[:first_section]
    exam_name = next(
        (
            line
            for line in header
            if any(word in line for word in ("考试", "试卷", "限时训练", "专项训练")) and not NOTICE_RE.match(line)
        ),
        fallback_title,
    )
    subject_name = next(
        (line for line in header if re.fullmatch(r"\s*\u8bed\s*\u6587\s*", line)),
        "\u8bed\u3000\u6587",
    )
    meta_text = next(
        (
            line
            for line in header
            if ("\u6ee1\u5206" in line or "\u8003\u8bd5\u65f6\u95f4" in line) and not QUESTION_RE.match(line)
        ),
        "",
    )
    total_match = re.search(r"\u6ee1\u5206\s*(\d+(?:\.\d+)?)\s*\u5206", " ".join(header))
    total_score = float(total_match.group(1)) if total_match else 150
    return {
        "exam_name": exam_name,
        "subject_name": subject_name,
        "meta_text": meta_text,
        "total_score": total_score,
        "declared_total_score": bool(total_match),
        "notices": [],
    }

def _question_confidence(stem: str, lines: list[str], index: int) -> int:
    score = 0
    if SCORE_RE.search(stem):
        score += 3
    if any(cue in stem for cue in QUESTION_CUES):
        score += 2
    if "\uff1f" in stem or stem.endswith("?"):
        score += 1
    if _extract_options(stem):
        score += 3
    for following in lines[index + 1 : index + 4]:
        if SECTION_RE.match(following) or SUBSECTION_RE.match(following) or QUESTION_RE.match(following):
            break
        if _extract_options(following):
            score += 3
            break
        if SCORE_RE.search(following) and not SUBQUESTION_RE.match(following):
            score += 1
            break
    return score

def _looks_like_question(line: str, lines: list[str], index: int) -> bool:
    match = QUESTION_RE.match(line)
    return bool(match and _question_confidence(match.group(2), lines, index) >= 3)

def _extract_options(line: str) -> list[str]:
    if not OPTION_START_RE.match(line):
        return []
    matches = INLINE_OPTION_RE.findall(line)
    return [f"{letter}\uff0e{text.strip()}" for letter, text in matches if text.strip()]

def _split_subsection(line: str) -> tuple[str, str]:
    marker = line.find("\uff08\u672c\u9898")
    if marker < 0:
        return line, ""
    return line[:marker], line[marker:]

def _is_header_line(line: str, metadata: dict[str, Any]) -> bool:
    return line in {
        metadata.get("exam_name"),
        metadata.get("subject_name"),
        metadata.get("meta_text"),
    } or bool(re.fullmatch(r"\d{4}[.\u5e74]\d{1,2}\u6708?", line))

def _score_total_or_default(blocks: list[dict[str, Any]], default: float) -> float:
    scores = [
        block["question"].get("score")
        for block in blocks
        if block.get("type") == "question"
    ]
    numeric = [float(score) for score in scores if score is not None]
    return sum(numeric) if numeric and len(numeric) == len(scores) else default



# merged rule group: native DOCX objects


TITLE_AUTHOR_RE = re.compile(
    r"^(.+?)[\t\u3000 ]{2,}([\u3400-\u9fff\u00b7]{2,12})$"
)

def _docx_lines_with_native(
    document: DocumentObject,
) -> tuple[list[str], list[dict[str, Any]]]:
    """\u6309\u539f\u987a\u5e8f\u63d0\u53d6\u6587\u5b57\uff0c\u5e76\u7528\u5360\u4f4d\u7b26\u8bb0\u5f55\u8868\u683c\u548c\u72ec\u7acb\u56fe\u7247\u3002"""

    lines: list[str] = []
    native: list[dict[str, Any]] = []
    paragraph_index = 0
    table_index = 0
    occurrences = {}
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            drawings = paragraph._p.xpath(".//w:drawing | .//w:pict")
            occurrence = occurrences.get(text, 0)
            if text:
                occurrences[text] = occurrence + 1
                lines.append(text)
            if drawings:
                if text:
                    native.append(
                        {
                            "kind": "drawing",
                            "source_paragraph_index": paragraph_index,
                            "target_text": text,
                            "occurrence": occurrence,
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
    """\u628a\u5199\u4f5c\u9898\u9644\u5c5e\u6bb5\u843d\u5206\u6210\u6750\u6599\u3001\u5f15\u5bfc\u8bed\u548c\u8981\u6c42\u3002"""

    for block in result.get("blocks", []):
        question = block.get("question")
        if not question:
            continue
        content = str(question.get("stem", "")) + str(question.get("embedded_segments", []))
        if not re.search(r"写作|写一篇|不少于\s*800\s*字|不少于八百字", content):
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
            if stripped.startswith(("\u8981\u6c42\uff1a", "\u8981\u6c42:")):
                requirements.append(stripped)
            elif "\u4ee5\u4e0a\u6750\u6599" in stripped and any(
                cue in stripped
                for cue in ("\u5f15\u53d1", "\u8054\u60f3", "\u601d\u8003", "\u542f\u793a", "\u611f\u609f", "\u8bf7\u5199")
            ):
                prompt.append(stripped)
            else:
                material.append(stripped)
        question["composition_material"] = material
        question["composition_prompt"] = prompt
        question["composition_requirements"] = requirements
        question["embedded_segments"] = []

def _normalize_title_authors(result: dict[str, Any]) -> None:
    """\u8bc6\u522b\u6807\u9898\u548c\u4f5c\u8005\u540c\u6bb5\u7684\u6587\u7ae0\u4e0e\u8bd7\u6b4c\u3002"""

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
            and _is_author(match.group(2))
        ]
        if not matched:
            normalized.append(block)
            continue
        if "\u8bd7" in previous_instruction:
            for position, (start, match) in enumerate(matched):
                end = matched[position + 1][0] if position + 1 < len(matched) else len(paragraphs)
                body = paragraphs[start + 1 : end]
                note = ""
                if body and str(body[-1]).startswith(("\u3010\u6ce8\u3011", "[\u6ce8]", "\uff3b\u6ce8\uff3d")):
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


# merged rule group: question details


SUBQUESTION_RE = re.compile(r"^[\uff08(](\d+)[\uff09)]\s*(.*)$")

SEGMENTATION_MARKERS_RE = re.compile(r"A.+B.+C")

def _normalize_question_details(result: dict[str, Any]) -> None:
    for block in result.get("blocks", []):
        question = block.get("question")
        if not question:
            continue
        embedded = question.get("embedded_segments", [])
        remaining: list[list[dict[str, str]]] = []
        subquestions = list(question.get("subquestions", []))
        for segments in embedded:
            text = "".join(str(item.get("text", "")) for item in segments).strip()
            sub_match = SUBQUESTION_RE.match(text)
            if sub_match:
                subquestions.append(sub_match.group(2).strip())
            elif (
                any(cue in str(question.get("stem", "")) for cue in ("断句", "断开", "停顿"))
                and SEGMENTATION_MARKERS_RE.search(text)
            ):
                question["segmentation_text"] = text
            else:
                if subquestions:
                    subquestions[-1] += "\n" + text
                else:
                    remaining.append(segments)
        question["embedded_segments"] = remaining
        if subquestions:
            question["subquestions"] = subquestions


# merged rule group: headers and poetry


SECTION_RE = re.compile(r"^[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\u3001")

SUBJECT_RE = re.compile(
    r"^\s*语\s*文(?:\s*科)?(?:\s*试\s*[题卷])?(?:\s+|　+)?(.*)$"
)

DATE_RE = re.compile(r"\b20\d{2}(?:[.\u5e74/-]\d{1,2}(?:\u6708)?)?\b")

NOTE_RE = re.compile(r"^\s*(?:\u3010\s*\u6ce8\s*\u3011|\[\s*\u6ce8\s*\]|\uff3b\s*\u6ce8\s*\uff3d|\u6ce8\s*[\uff1a:])")

AUTHOR_RE = re.compile(
    r"^[\u3400-\u9fff\u00b7]{2,12}(?:\s*(?:\u3010\s*\u6ce8\s*\u3011|\[\s*\u6ce8\s*\]|\uff3b\s*\u6ce8\s*\uff3d))?$"
)

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
            if re.match(r"\s*(?:注意事项|考生须知)\s*[：:]?", line)
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
    metadata["subject_name"] = "\u8bed\u3000\u6587"
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
        or "\u6ee1\u5206" in text
        or "\u8003\u8bd5\u65f6\u95f4" in text
        or "\u7b54\u9898" in text
        or "\u8003\u751f" in text
    )

def _normalize_dynasty_poems(result: dict[str, Any]) -> None:
    """Recognize unlabelled poem pairs with author-followed dynasty brackets."""
    normalized = []
    instruction = ""
    for block in result.get("blocks", []):
        if block.get("type") == "instruction":
            instruction = block.get("text", "")
        paragraphs = block.get("paragraphs", [])
        if block.get("type") != "material" or not re.search(r"首.*[诗词]|[诗词].*首", instruction):
            normalized.append(block)
            continue
        starts = [i for i in range(len(paragraphs)-2)
                  if re.fullmatch(r"[^，。！？；：]{1,24}", paragraphs[i])
                  and re.fullmatch(r"[\u3400-\u9fff·]{2,10}\s*[【\[(（](?:先秦|秦|汉|魏晋|南北朝|隋|唐|五代|南唐|宋|北宋|南宋|元|明|清)[】\])）]", paragraphs[i+1])]
        if not starts or starts[0] != 0:
            normalized.append(block)
            continue
        for index, start in enumerate(starts):
            end = starts[index+1] if index+1 < len(starts) else len(paragraphs)
            body = list(paragraphs[start+2:end])
            note = body.pop() if body and NOTE_RE.match(body[-1]) else ""
            normalized.append({"type":"poetry", "title":paragraphs[start], "author":paragraphs[start+1], "paragraphs":body, "note":note})
    result["blocks"] = normalized


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
            cue in previous_instruction for cue in ("\u8bd7", "\u8bcd", "\u66f2")
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
            r"(?:\u3010\s*\u6ce8\s*\u3011|\[\s*\u6ce8\s*\]|\uff3b\s*\u6ce8\s*\uff3d)\s*$",
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


# merged rule group: source decorations


def _collect_source_decorations(document: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    occurrences: dict[str, int] = {}
    for paragraph in document.paragraphs:
        raw_text = paragraph.text
        text = raw_text.strip()
        if not text:
            continue
        leading = len(raw_text) - len(raw_text.lstrip())
        ranges: list[dict[str, Any]] = []
        cursor = 0
        paragraph_style_bold = bool(
            getattr(getattr(paragraph, "style", None), "font", None)
            and getattr(paragraph.style.font, "bold", False)
        )
        for run in paragraph.runs:
            start = cursor - leading
            end = start + len(run.text)
            cursor += len(run.text)
            if end <= 0 or start >= len(text):
                continue
            underline_nodes = run._r.xpath("./w:rPr/w:u")
            emphasis_nodes = run._r.xpath("./w:rPr/w:em")
            underline = [node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") or "single" for node in underline_nodes]
            emphasis = [node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") or "underDot" for node in emphasis_nodes]
            bold = bool(run.bold) or (run.bold is None and paragraph_style_bold)
            if not underline and not emphasis and not bold:
                continue
            mark: dict[str, Any] = {
                "start": max(0, start),
                "end": min(len(text), end),
            }
            if underline and underline[0] not in {"none", "0", "false"}:
                mark["underline"] = str(underline[0])
            if emphasis and emphasis[0] not in {"none", "0", "false"}:
                mark["emphasis"] = str(emphasis[0])
            if bold:
                mark["bold"] = True
            if len(mark) > 2:
                ranges.append(mark)
        occurrence = occurrences.get(text, 0)
        occurrences[text] = occurrence + 1
        if ranges:
            collected.append(
                {
                    "text": text,
                    "occurrence": occurrence,
                    "ranges": ranges,
                }
            )
    return collected


# merged rule group: formal title refinement


SECTION_RE = re.compile(r"^[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\u3001")

SUBJECT_RE = re.compile(
    r"^\s*\u8bed\s*\u6587(?:\s*\u8bd5\s*\u9898)?(?:\s+|\u3000+)?(.*)$"
)

CONFIDENTIAL_RE = re.compile(r"(?:\u4fdd\u5bc6|\u7edd\u5bc6|\u542f\u7528\u524d|\u8bd5\u9898\u7c7b\u578b)")

TITLE_CUES = ("\u8003\u8bd5", "\u68c0\u6d4b", "\u6d4b\u8bd5", "\u8bd5\u5377", "\u6a21\u62df")

HEADER_NOTICE_RE = re.compile(r"^\s*(?:\u6ce8\u610f\u4e8b\u9879|\u8003\u751f\u987b\u77e5)\s*[\uff1a:]?\s*$")

HEADER_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[.\uff0e\u3001]\s*")

HEADER_INFO_CUES = ("\u8003\u8bd5\u65f6\u95f4", "\u8bd5\u5377\u6ee1\u5206", "\u8003\u8bd5\u7528\u65f6", "\u672c\u8bd5\u5377\u5171")

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
    header = [part for line in header for part in re.split(r"(?<=：)(?=\s*1[.．、])|(?<=:)(?=\s*1[.．、])", line)]
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


# merged rule group: adaptive title and score recognition


AUTHOR_RE = re.compile(
    r"^(?:\[[^\]]{1,8}\]|\uff3b[^\uff3d]{1,8}\uff3d|\u3014[^\u3015]{1,8}\u3015|\uff08[^\uff09]{1,8}\uff09)?"
    r"[\u3400-\u9fff\u00b7]{2,14}$"
)

BODY_LEAD_RE = re.compile(
    r"^(?:[\u2460-\u2473\u3251-\u32bf]|\d{1,3}[\uff0e.]|[\u201c\u2018\uff08(\u300a]|[\u3400-\u9fff])"
)

LABEL_RE = re.compile(r"^(?:\u6750\u6599|\u6587\u672c)(?:[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+|\d+)\s*[\uff1a:]")

SCORE_RE = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)](?=\s*(?:[（(][\s\u3000]*[）)]\s*)*$)")

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
        if any(cue in previous_instruction for cue in ("\u8bd7", "\u8bcd", "\u66f2")):
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
    """Recognize scores such as ``\uff083 \u5206\uff09`` without altering question text."""

    for block in result.get("blocks", []):
        question = block.get("question")
        if not question or question.get("score") is not None:
            continue
        stem = str(question.get("stem", ""))
        match = SCORE_RE.search(stem)
        if not match:
            continue
        question["score"] = float(match.group(1))
        question["stem"] = (stem[: match.start()] + stem[match.end():]).strip()

def _record_segmentation_markers(result: dict[str, Any]) -> None:
    """Record exactly the uppercase markers found in each segmentation passage."""

    for block in result.get("blocks", []):
        question = block.get("question")
        if not question or not question.get("segmentation_text"):
            continue
        text = str(question["segmentation_text"])
        question["segmentation_markers"] = re.findall(r"[A-Z]", text)


# merged rule group: subsections and composition prompts


SUBSECTION_RE = re.compile(
    r"^\s*(?P<name>[\uff08(]\s*[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\s*[\uff09)]"
    r"\s*[^\uff08(]+?)"
    r"(?P<meta>[\uff08(]\s*\u672c\u9898\u5171.+?[\uff09)])\s*$"
)

AUTHOR_RE = re.compile(
    r"^\s*(?:[\uff08(]\s*[\u3400-\u9fff]{1,4}\s*[\uff09)]\s*)?"
    r"[\u3400-\u9fff\u00b7]{2,14}\s*$"
)

POETRY_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"[\[\uff3b\u3010]\s*[\u7532\u4e59\u4e19\u4e01\u5176\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\s*[\]\uff3d\u3011]"
    r"|\u5176[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+"
    r"|\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\u9996"
    r").+"
)

NOTE_RE = re.compile(
    r"^\s*(?:\u3010\s*\u6ce8\s*\u3011|\[\s*\u6ce8\s*\]|\uff3b\s*\u6ce8\s*\uff3d|\u6ce8\s*[\uff1a:])"
)

HEADER_DATE_RE = re.compile(r"(?P<date>20\d{2}\s*[./\u5e74-]\s*\d{1,2}(?:\s*\u6708)?)")

SECTION_RE = re.compile(r"^[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\u3001")

def _normalize_subsections(result: dict[str, Any]) -> None:
    normalized: list[dict[str, Any]] = []
    for block in result.get("blocks", []):
        if block.get("type") == "question":
            question = block.get("question", {})
            kept: list[list[dict[str, Any]]] = []
            lifted: list[dict[str, Any]] = []
            for segments in question.get("embedded_segments", []):
                text = "".join(str(item.get("text", "")) for item in segments).strip()
                match = SUBSECTION_RE.fullmatch(text)
                if match:
                    lifted.append(_subsection(match))
                else:
                    kept.append(segments)
            question["embedded_segments"] = kept
            normalized.append(block)
            normalized.extend(lifted)
            continue

        if block.get("type") != "material":
            normalized.append(block)
            continue

        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        if not any(SUBSECTION_RE.fullmatch(text) for text in paragraphs):
            normalized.append(block)
            continue

        current: list[str] = []
        for text in paragraphs:
            match = SUBSECTION_RE.fullmatch(text)
            if match:
                if current:
                    material = dict(block)
                    material["paragraphs"] = current
                    normalized.append(material)
                    current = []
                normalized.append(_subsection(match))
            else:
                current.append(text)
        if current:
            material = dict(block)
            material["paragraphs"] = current
            normalized.append(material)
    result["blocks"] = normalized

def _subsection(match: re.Match[str]) -> dict[str, str]:
    return {
        "type": "subsection",
        "name": match.group("name").strip(),
        "meta": match.group("meta").strip(),
    }

def _normalize_multi_poetry(result: dict[str, Any]) -> None:
    normalized: list[dict[str, Any]] = []
    previous_instruction = ""
    for block in result.get("blocks", []):
        if block.get("type") == "instruction":
            previous_instruction = str(block.get("text", ""))
            normalized.append(block)
            continue
        if block.get("type") != "material" or not any(
            cue in previous_instruction for cue in ("\u8bd7", "\u8bcd", "\u66f2")
        ):
            normalized.append(block)
            continue

        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        starts = [
            index
            for index in range(len(paragraphs) - 1)
            if POETRY_TITLE_RE.match(paragraphs[index])
            and AUTHOR_RE.fullmatch(paragraphs[index + 1])
        ]
        if not starts:
            normalized.append(block)
            continue

        prefix = paragraphs[: starts[0]]
        if prefix:
            normalized.append({"type": "material", "paragraphs": prefix})
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(paragraphs)
            body = paragraphs[start + 2 : end]
            note = ""
            if body and NOTE_RE.match(body[-1]):
                note = body.pop()
            normalized.append(
                {
                    "type": "poetry",
                    "title": paragraphs[start],
                    "author": paragraphs[start + 1],
                    "paragraphs": body,
                    "note": note,
                }
            )
    result["blocks"] = normalized

def _normalize_composition_prompts(result: dict[str, Any]) -> None:
    cues = (
        "\u4ee5\u4e0a\u6750\u6599",
        "\u8bf7\u4f60\u4ee5",
        "\u8bf7\u4ee5",
        "\u8bf7\u5199\u4e00\u7bc7",
        "\u5199\u4e00\u7bc7",
        "\u53d1\u8868\u6f14\u8bb2",
        "\u611f\u609f\u4e0e\u601d\u8003",
        "\u8054\u60f3\u548c\u601d\u8003",
    )
    for block in result.get("blocks", []):
        question = block.get("question")
        if not question or not question.get("composition_material"):
            continue
        material: list[str] = []
        prompt = [str(value) for value in question.get("composition_prompt", [])]
        for text in question.get("composition_material", []):
            value = str(text).strip()
            if any(cue in value for cue in cues):
                prompt.append(value)
            else:
                material.append(value)
        question["composition_material"] = material
        question["composition_prompt"] = prompt

def _normalize_header_metadata(source: Path, result: dict[str, Any]) -> None:
    document = Document(source)
    lines = [paragraph.text.strip() for paragraph in document.paragraphs]
    lines = [line for line in lines if line]
    first_section = next(
        (index for index, line in enumerate(lines) if SECTION_RE.match(line)),
        min(len(lines), 18),
    )
    header = lines[:first_section]
    metadata = result.setdefault("metadata", {})

    for text in header:
        date_match = HEADER_DATE_RE.search(text)
        if not date_match:
            continue
        left = text[: date_match.start()].strip()
        if (
            left
            and left != metadata.get("exam_name")
            and "\u6ee1\u5206" not in left
            and "\u8bed\u6587" not in left
        ):
            metadata["institution_text"] = left
            metadata["exam_date"] = date_match.group("date").strip()
            metadata["meta_text"] = (
                f"{metadata['institution_text']}\t{metadata['exam_date']}"
            )
            break

    exam_info = next(
        (
            text
            for text in header
            if "\u6ee1\u5206" in text
            and any(cue in text for cue in ("\u672c\u5377\u5171", "\u672c\u8bd5\u5377\u5171", "\u8003\u8bd5", "\u7528\u65f6"))
        ),
        "",
    )
    if exam_info:
        metadata["exam_info_text"] = exam_info


# merged rule group: notices and semantic materials


MAJOR_SECTION_RE = re.compile(
    r"^\s*[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\s*[\u3001\uff0c,]\s*(?P<body>.+?)\s*$"
)

NOTICE_TITLE_RE = re.compile(r"^\s*(\u6ce8\u610f\u4e8b\u9879|\u8003\u751f\u987b\u77e5)\s*[\uff1a:]?\s*$")

NOTICE_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[\uff0e.\u3001]\s*(?P<body>.+?)\s*$")

LABEL_RE = re.compile(r"^\s*(?:\u6750\u6599|\u6587\u672c)(?:[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+|\d+)\s*[\uff1a:]")

NOTE_RE = re.compile(
    r"^\s*(?:\u3010\s*\u6ce8\s*\u3011|\[\s*\u6ce8\s*\]|\uff3b\s*\u6ce8\s*\uff3d|\u6ce8\s*[\uff1a:])"
)

SOURCE_RE = re.compile(
    r"^\s*[\uff08(]\s*(?:"
    r"(?:\u6458\u81ea|\u6458\u7f16\u81ea|\u6458\u9009\u81ea|\u9009\u81ea|\u8282\u9009\u81ea|\u6539\u7f16\u81ea|\u636e).+|"
    r"\u672c\u6587.+|\u6709\u5220\u6539|\u6709\u6539\u52a8"
    r")[\uff09)]\s*$"
)

AUTHOR_RE = re.compile(
    r"^\s*(?:(?:\u3010[^\u3011]{1,8}\u3011|\[[^\]]{1,8}\]|\uff3b[^\uff3d]{1,8}\uff3d|"
    r"\uff08[^\uff09]{1,8}\uff09|\([^)]{1,8}\))\s*)?"
    r"[\u3400-\u9fff\u00b7\u30fb\u3001\uff0c,\s]{2,30}\s*$"
)

BODY_LEAD_RE = re.compile(r"^(?:[\u2460-\u2473]|\d|[\u201c\u2018\uff08(\u300a]|\u3400-\u9fff)")

SECTION_CUES = ("\u9605\u8bfb", "\u8bed\u8a00\u6587\u5b57\u8fd0\u7528", "\u5199\u4f5c")

POETRY_CUES = ("\u8bd7", "\u8bcd", "\u66f2")

def _normalize_header_notices(source: Path, result: dict[str, Any]) -> None:
    """Treat ``\u8003\u751f\u987b\u77e5`` and ``\u6ce8\u610f\u4e8b\u9879`` as equivalent optional notice headings."""

    document = Document(source)
    lines = [paragraph.text.strip() for paragraph in document.paragraphs]
    lines = [line for line in lines if line]
    first_section = next(
        (
            index
            for index, line in enumerate(lines)
            if _is_major_section(line)
        ),
        min(len(lines), 18),
    )
    header = [part for line in lines[:first_section] for part in re.split(r"(?<=：)(?=\s*1[.．、])|(?<=:)(?=\s*1[.．、])", line)]
    notice_index = next(
        (
            index
            for index, line in enumerate(header)
            if NOTICE_TITLE_RE.fullmatch(line)
        ),
        None,
    )
    if notice_index is None:
        return

    match = NOTICE_TITLE_RE.fullmatch(header[notice_index])
    assert match is not None
    raw_notice_lines = header[notice_index + 1 :]
    notices: list[str] = []
    for line in raw_notice_lines:
        item = NOTICE_ITEM_RE.fullmatch(line)
        notices.append(item.group("body").strip() if item else line.strip())

    metadata = result.setdefault("metadata", {})
    metadata["notice_title"] = f"{match.group(1)}\uff1a"
    metadata["notices"] = notices
    metadata["meta_text"] = _header_meta_before_notice(
        header[:notice_index],
        metadata,
    )
    exam_info = str(metadata.get("exam_info_text", "")).strip()
    if exam_info and exam_info in raw_notice_lines:
        metadata.pop("exam_info_text", None)

def _header_meta_before_notice(
    lines: list[str],
    metadata: dict[str, Any],
) -> str:
    excluded = {
        str(metadata.get("exam_name", "")).strip(),
        str(metadata.get("subject_name", "")).strip(),
        str(metadata.get("confidentiality_text", "")).strip(),
    }
    candidates = [
        line
        for line in lines
        if line not in excluded
        and ("\u6ee1\u5206" in line or "\u8003\u8bd5\u65f6\u95f4" in line or "\u8003\u8bd5\u7528\u65f6" in line)
    ]
    if candidates:
        return candidates[0]
    existing = str(metadata.get("meta_text", ""))
    if existing.rstrip("\uff1a:") in {"\u8003\u751f\u987b\u77e5", "\u6ce8\u610f\u4e8b\u9879"}:
        return ""
    return existing

def _lift_embedded_sections(result: dict[str, Any]) -> None:
    """Move major section headings out of question or material payloads."""

    normalized: list[dict[str, Any]] = []
    for block in result.get("blocks", []):
        lifted: list[dict[str, str]] = []
        question = block.get("question")
        if question:
            kept: list[list[dict[str, Any]]] = []
            for segments in question.get("embedded_segments", []):
                text = "".join(
                    str(segment.get("text", "")) for segment in segments
                ).strip()
                if _is_major_section(text):
                    lifted.append({"type": "section_title", "text": text})
                else:
                    kept.append(segments)
            question["embedded_segments"] = kept
            normalized.append(block)
            normalized.extend(lifted)
            continue

        if block.get("type") == "material":
            paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
            if any(_is_major_section(text) for text in paragraphs):
                current: list[str] = []
                for text in paragraphs:
                    if _is_major_section(text):
                        if current:
                            item = dict(block)
                            item["paragraphs"] = current
                            normalized.append(item)
                            current = []
                        normalized.append({"type": "section_title", "text": text})
                    else:
                        current.append(text)
                if current:
                    item = dict(block)
                    item["paragraphs"] = current
                    normalized.append(item)
                continue
        normalized.append(block)
    result["blocks"] = normalized

def _is_major_section(text: str) -> bool:
    match = MAJOR_SECTION_RE.fullmatch(text)
    return bool(match and any(cue in match.group("body") for cue in SECTION_CUES))

def _normalize_single_poetry(result: dict[str, Any]) -> None:
    """Promote one-poem material blocks with dynasty-prefixed authors."""

    normalized: list[dict[str, Any]] = []
    previous_instruction = ""
    for block in result.get("blocks", []):
        if block.get("type") == "instruction":
            previous_instruction = str(block.get("text", ""))
            normalized.append(block)
            continue
        if block.get("type") != "material" or not any(
            cue in previous_instruction for cue in POETRY_CUES
        ):
            normalized.append(block)
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        if len(paragraphs) < 3 or not _is_author_line(paragraphs[1]):
            normalized.append(block)
            continue
        body = paragraphs[2:]
        note = str(block.get("note", ""))
        if not note and body and NOTE_RE.match(body[-1]):
            note = body.pop()
        normalized.append(
            {
                "type": "poetry",
                "title": paragraphs[0],
                "author": paragraphs[1],
                "paragraphs": body,
                "note": note,
            }
        )
    result["blocks"] = normalized

def _normalize_prose_title_authors(result: dict[str, Any]) -> None:
    """Promote prose title and author lines, including multiple authors."""

    previous_instruction = ""
    for block in result.get("blocks", []):
        if block.get("type") == "instruction":
            previous_instruction = str(block.get("text", ""))
            continue
        if block.get("type") != "material":
            continue
        if block.get("title") or block.get("author"):
            _promote_trailing_source(block)
            continue
        if any(cue in previous_instruction for cue in POETRY_CUES):
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        if len(paragraphs) < 3:
            continue
        title, author, first_body = paragraphs[:3]
        if (
            not title
            or len(title) > 40
            or LABEL_RE.match(title)
            or not _is_author_line(author)
            or not BODY_LEAD_RE.match(first_body)
        ):
            continue
        block["title"] = title
        block["author"] = author
        block["paragraphs"] = paragraphs[2:]
        _promote_trailing_source(block)

def _is_author_line(text: str) -> bool:
    value = text.strip()
    if not AUTHOR_RE.fullmatch(value):
        return False
    han_count = len(re.findall(r"[\u3400-\u9fff]", value))
    return 2 <= han_count <= 20 and not any(
        mark in value for mark in ("\u3002", "\uff01", "\uff1f", "\uff1b", "\uff1a", "\u201c", "\u201d")
    )

def _promote_trailing_source(block: dict[str, Any]) -> None:
    paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
    if not block.get("source") and paragraphs and SOURCE_RE.fullmatch(paragraphs[-1]):
        block["source"] = paragraphs.pop()
        block["paragraphs"] = paragraphs

def _annotate_material_roles(
    source: Path | None,
    result: dict[str, Any],
) -> None:
    """Record label, source and centered subheading roles for later rendering."""

    centered_bold: set[str] = set()
    if source is not None:
        for paragraph in Document(source).paragraphs:
            text = paragraph.text.strip()
            visible_runs = [run for run in paragraph.runs if run.text.strip()]
            if (
                text
                and len(text) <= 60
                and paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
                and any(run.bold for run in visible_runs)
            ):
                centered_bold.add(text)

    for block in result.get("blocks", []):
        if block.get("type") != "material":
            continue
        paragraphs = [str(value).strip() for value in block.get("paragraphs", [])]
        roles: list[str] = []
        formats = list(block.get("paragraph_formats", []))
        offset = int(bool(block.get("title"))) + int(bool(block.get("author")))
        for index, text in enumerate(paragraphs):
            role = "body"
            spec: dict[str, Any] | None = None
            if LABEL_RE.match(text):
                role = "label"
                spec = _format_spec("\u9ed1\u4f53", "\u5de6\u5bf9\u9f50")
            elif SOURCE_RE.fullmatch(text):
                role = "source"
                spec = _format_spec("\u4eff\u5b8b", "\u53f3\u5bf9\u9f50")
            elif text in centered_bold:
                role = "subheading"
                spec = _format_spec("\u9ed1\u4f53", "\u5c45\u4e2d")
            roles.append(role)
            if spec is not None:
                spec["target_index"] = offset + index
                spec["semantic_role"] = role
                formats.append(spec)
        block["paragraph_roles"] = roles
        if formats:
            block["paragraph_formats"] = formats

def _format_spec(font: str, alignment: str) -> dict[str, Any]:
    return {
        "font": font,
        "size_pt": 10.5,
        "bold": False,
        "alignment": alignment,
        "left_indent_chars": 0,
        "special_indent": "\u65e0",
        "special_indent_chars": 0,
        "line_spacing": 1.25,
        "space_before_pt": 0,
        "space_after_pt": 0,
    }


# merged rule group: answers and title-author enhancement


AUTHOR_RE = re.compile(
    r"^\s*(?:(?:\u3010[^\u3011]{1,8}\u3011|\[[^\]]{1,8}\]|"
    r"\uff08[^\uff09]{1,8}\uff09|\([^)]{1,8}\))\s*)?"
    r"[\u3400-\u9fff\u00b7\u3001\uff0c,\s]{2,30}"
    r"(?:\s*(?:\u3010\s*\u6ce8\s*\u3011|\[\s*\u6ce8\s*\]|\uff3b\s*\u6ce8\s*\uff3d))?\s*$"
)

SOURCE_RE = re.compile(
    r"^\s*[\uff08(]\s*(?:\u6458\u81ea|\u6458\u7f16\u81ea|\u6458\u9009\u81ea|\u9009\u81ea|\u8282\u9009\u81ea|"
    r"\u6539\u7f16\u81ea|\u636e.+|\u672c\u6587.+|\u6709\u5220\u6539|\u6709\u6539\u52a8).+[\uff09)]\s*$"
)

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
        offset = int(bool(block.get("title"))) + int(bool(block.get("author")))
        for index, text in enumerate(paragraphs):
            if SOURCE_RE.fullmatch(text):
                roles[index] = "source"
                formats.append(_format(index + offset, "\u4eff\u5b8b", "\u53f3\u5bf9\u9f50", "source"))
                continue
            source_text = text if text in centered else re.sub(r"^[一二三四五六七八九十]+、", "", text)
            if source_text not in centered:
                continue
            previous_centered = index > 0 and paragraphs[index - 1] in centered
            next_is_author = (
                index + 1 < len(paragraphs)
                and paragraphs[index + 1] in centered
                and _is_author(paragraphs[index + 1])
            )
            if _is_author(text) and previous_centered:
                roles[index] = "author"
                formats.append(_format(index + offset, "\u4eff\u5b8b", "\u5c45\u4e2d", "author"))
            elif source_text in centered_bold or next_is_author:
                roles[index] = "subheading"
                formats.append(_format(index + offset, "\u9ed1\u4f53", "\u5c45\u4e2d", "subheading"))
        block["paragraph_roles"] = roles
        if formats:
            block["paragraph_formats"] = _deduplicate_formats(formats)

def _block_texts(block: dict[str, Any]) -> list[str]:
    """Return the visible strings that can carry an answer boundary marker."""

    values: list[str] = []
    for key in ("text", "name", "meta", "header"):
        if block.get(key):
            values.append(str(block[key]))
    for value in block.get("paragraphs", []):
        values.append(str(value.get("text", "")) if isinstance(value, dict) else str(value))
    return values


def _trim_answer_from_exam(result: dict[str, Any]) -> None:
    kept_blocks: list[dict[str, Any]] = []
    for block in result.get("blocks", []):
        question = block.get("question")
        marker_found = False
        if question and any(question.get(key) for key in ("composition_material", "composition_prompt", "composition_requirements")):
            for key in (
                "composition_material",
                "composition_prompt",
                "composition_requirements",
            ):
                values: list[str] = []
                for value in question.get(key, []):
                    text = str(value).strip()
                    if ANSWER_MARKER_RE.fullmatch(text):
                        marker_found = True
                        break
                    values.append(str(value))
                question[key] = values
                if marker_found:
                    break
            kept_blocks.append(block)
            if marker_found:
                break
            continue

        texts = _block_texts(block)
        if any(ANSWER_MARKER_RE.fullmatch(str(text).strip()) for text in texts):
            break
        kept_blocks.append(block)
    result["blocks"] = kept_blocks

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
        "special_indent": "\u65e0",
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


# merged rule group: publication-note semantics


PUBLICATION_NOTE_RE = re.compile(
    r"^\s*[\uff08(]"
    r"(?=[^\uff09)]*(?:\u8bd1|\u53d1\u8868\u4e8e|\u520a\u4e8e|\u8f7d\u4e8e|\u539f\u8f7d|\u521d\u520a|\u9996\u520a|\u6709\u5220\u6539|\u6709\u6539\u52a8))"
    r"(?!\s*(?:\u6458\u81ea|\u6458\u7f16\u81ea|\u6458\u9009\u81ea|\u9009\u81ea|\u8282\u9009\u81ea|\u6539\u7f16\u81ea|\u636e|\u6765\u6e90))"
    r"[^\uff09)]+[\uff09)]\s*$"
)

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
        "font": "\u5b8b\u4f53",
        "size_pt": 10.5,
        "bold": False,
        "alignment": "\u53f3\u5bf9\u9f50",
        "left_indent_chars": 0,
        "special_indent": "\u65e0",
        "special_indent_chars": 0,
        "line_spacing": 1.25,
        "space_before_pt": 0,
        "space_after_pt": 0,
    }


def _parse_docx_payload(source: Path, original: Path) -> dict[str, Any]:
    """Extract a DOCX in document order while retaining native objects."""

    document = Document(source)
    lines, native_objects = _docx_lines_with_native(document)
    result = parse_plain_lines(lines, original.stem)
    metadata = result.setdefault("metadata", {})
    metadata["source_docx_path"] = str(original.resolve())
    metadata["native_objects"] = native_objects
    if document.sections:
        section = document.sections[0]
        metadata["source_page"] = {
            "margin_top_mm": round(float(section.top_margin.mm), 2),
            "margin_bottom_mm": round(float(section.bottom_margin.mm), 2),
            "margin_left_mm": round(float(section.left_margin.mm), 2),
            "margin_right_mm": round(float(section.right_margin.mm), 2),
        }
    metadata.setdefault("layout_preset", DEFAULT_PRESET)
    return result


def _run_docx_normalizers(source: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Apply the verified normalization passes in their historical order."""

    _normalize_composition(result)
    _normalize_docx_header(source, result)
    _normalize_title_authors(result)
    _normalize_question_details(result)
    _normalize_dynasty_poems(result)
    _normalize_poetry_blocks(result)
    _normalize_notes(result)
    result.setdefault("metadata", {})["source_decorations"] = (
        _collect_source_decorations(Document(source))
    )
    _refine_exam_name(source, result)
    _normalize_separate_title_authors(result)
    _normalize_spaced_scores(result)
    _record_segmentation_markers(result)
    _normalize_subsections(result)
    _normalize_multi_poetry(result)
    _normalize_composition_prompts(result)
    _normalize_header_metadata(source, result)
    _normalize_header_notices(source, result)
    _lift_embedded_sections(result)
    _normalize_single_poetry(result)
    _normalize_prose_title_authors(result)
    _annotate_material_roles(source, result)
    return result


@document_read_session
def import_exam(path: str | Path) -> dict[str, Any]:
    """Import one exam or answer document through the canonical pipeline.

    The flexible layer keeps text and JSON compatibility for saved drafts. The
    desktop facade accepts only DOCX or DOC and performs legacy DOC conversion.
    PDF input is rejected consistently because PDF export/import was removed
    from the current product scope.
    """

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        result = json.loads(source.read_text(encoding="utf-8"))
        mark_publication_notes(result)
        return result
    if suffix in {".txt", ".md", ".markdown"}:
        text = source.read_text(encoding="utf-8-sig")
        result = parse_plain_lines(
            [line.strip() for line in text.splitlines() if line.strip()],
            source.stem,
        )
        _normalize_composition(result)
        _normalize_question_details(result)
        _normalize_subsections(result)
        _normalize_composition_prompts(result)
        mark_publication_notes(result)
        return result
    if suffix == ".pdf":
        raise ValueError("\u5f53\u524d\u7248\u672c\u4e0d\u518d\u63d0\u4f9b PDF \u5bfc\u5165\uff0c\u8bf7\u4f7f\u7528 DOCX \u6216 DOC \u6587\u4ef6\u3002")
    if suffix != ".docx":
        raise ValueError(
            "\u652f\u6301\u7684\u8bd5\u9898\u683c\u5f0f\u4e3a DOCX\u3001DOC\u3001JSON\u3001TXT \u548c Markdown\u3002"
        )
    if is_standalone_answer_docx(source):
        result = standalone_answer_model(parse_answer_docx(source))
        document = Document(source)
        _, native = _docx_lines_with_native(document)
        result["metadata"]["native_objects"] = [spec for spec in native if spec["kind"] == "drawing"]
        result["metadata"]["source_decorations"] = _collect_source_decorations(document)
        return result

    # Reopen for numbering edits: cached python-docx back-references cannot
    # reliably be deep-copied together with the package's serialization tree.
    document = OpenDocument(source)
    inserted = materialize_automatic_numbering(document)
    if inserted:
        with tempfile.TemporaryDirectory(prefix="exam-numbering-") as folder:
            prepared = Path(folder) / source.name
            document.save(prepared)
            result = _parse_docx_payload(prepared, source)
            result = _run_docx_normalizers(source, result)
    else:
        result = _parse_docx_payload(source, source)
        result = _run_docx_normalizers(source, result)

    _enhance_material_titles_authors(source, result)
    original_lines = [p.text.strip() for p in Document(source).paragraphs if p.text.strip()]
    boundary = next((i for i, line in enumerate(original_lines) if SECTION_RE.match(line) or line.startswith("阅读下面") or QUESTION_RE.match(line)), 0)
    metadata = result.setdefault("metadata", {})
    excluded = {str(metadata.get(key, "")).strip() for key in ("exam_name", "subject_name", "meta_text", "confidentiality_text", "exam_info_text")}
    if not metadata.get("notices"):
        metadata["header_lines"] = [line for line in original_lines[:boundary] if line not in excluded and not SUBJECT_RE.match(line)]
    answer_start = find_answer_start(source)
    if answer_start is not None:
        _trim_answer_from_exam(result)
        answer = parse_answer_docx(
            source,
            start_paragraph=answer_start,
            fallback_title=str(result.get("metadata", {}).get("exam_name", "")),
        )
        attach_answer_blocks(result, answer)
    mark_publication_notes(result)
    ensure_block_ids(result)
    result.setdefault("metadata", {})["source_format"] = "docx"
    return result


__all__ = [
    "import_exam",
    "parse_plain_lines",
    "save_exam",
    "materialize_automatic_numbering",
    "mark_publication_notes",
]
