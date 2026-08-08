"""Flexible importer v9 for adaptive subsection, poetry and header recognition."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from docx import Document

from .flexible_importers_v8 import import_exam as import_exam_v8
from .flexible_importers_v8 import parse_plain_lines, save_exam


SUBSECTION_RE = re.compile(
    r"^\s*(?P<name>[（(]\s*[一二三四五六七八九十]+\s*[）)]"
    r"\s*[^（(]+?)"
    r"(?P<meta>[（(]\s*本题共.+?[）)])\s*$"
)
AUTHOR_RE = re.compile(
    r"^\s*(?:[（(]\s*[\u3400-\u9fff]{1,4}\s*[）)]\s*)?"
    r"[\u3400-\u9fff·]{2,14}\s*$"
)
POETRY_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"[\[［【]\s*[甲乙丙丁其一二三四五六七八九十]+\s*[\]］】]"
    r"|其[一二三四五六七八九十]+"
    r"|第[一二三四五六七八九十]+首"
    r").+"
)
NOTE_RE = re.compile(
    r"^\s*(?:【\s*注\s*】|\[\s*注\s*\]|［\s*注\s*］|注\s*[：:])"
)
HEADER_DATE_RE = re.compile(r"(?P<date>20\d{2}\s*[./年-]\s*\d{1,2}(?:\s*月)?)")
SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、")


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import an exam while accepting common regional layout variations."""

    source = Path(path)
    result = import_exam_v8(source)
    _normalize_subsections(result)
    _normalize_multi_poetry(result)
    _normalize_composition_prompts(result)
    if source.suffix.lower() == ".docx":
        _normalize_header_metadata(source, result)
    return result


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
            cue in previous_instruction for cue in ("诗", "词", "曲")
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
        "以上材料",
        "请你以",
        "请以",
        "请写一篇",
        "写一篇",
        "发表演讲",
        "感悟与思考",
        "联想和思考",
    )
    for block in result.get("blocks", []):
        question = block.get("question")
        if not question or int(question.get("number", 0)) != 23:
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
        if left and left != metadata.get("exam_name") and "满分" not in left:
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
            if "满分" in text
            and any(cue in text for cue in ("本卷共", "本试卷共", "考试", "用时"))
        ),
        "",
    )
    if exam_info:
        metadata["exam_info_text"] = exam_info


__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
