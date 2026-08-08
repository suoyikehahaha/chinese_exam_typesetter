"""面向多种试卷结构的上下文感知导入器。"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


QUESTION_RE = re.compile(r"^\s*(\d{1,2})\s*[．.、]\s*(.+)$")
OPTION_START_RE = re.compile(r"^\s*[A-D]\s*[．.、]")
INLINE_OPTION_RE = re.compile(
    r"([A-D])\s*[．.、]\s*(.*?)(?=(?:[A-D])\s*[．.、]|$)"
)
SECTION_RE = re.compile(r"^\s*[一二三四五六七八九十]+、")
SUBSECTION_RE = re.compile(r"^\s*（[一二三四五六七八九十]+）")
SCORE_RE = re.compile(r"[（(](\d+(?:\.\d+)?)分[）)]\s*$")
NOTICE_RE = re.compile(r"^\s*注意事项\s*[：:]?\s*$")
NOTICE_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[．.、]\s*(.+)$")
QUESTION_CUES = (
    "下列",
    "请",
    "简要",
    "概括",
    "分析",
    "根据",
    "如何",
    "为什么",
    "哪些",
    "补写",
    "翻译",
    "写出",
    "选出",
    "指出",
    "完成",
    "阅读下面",
    "最恰当",
    "不正确",
    "正确的一项",
)


def import_exam(path: str | Path) -> dict[str, Any]:
    """导入 JSON、DOCX、TXT、Markdown 或文本型 PDF。"""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        return json.loads(source.read_text(encoding="utf-8"))
    if suffix == ".docx":
        document = Document(source)
        return parse_plain_lines(list(_docx_lines(document)), source.stem)
    if suffix == ".pdf":
        return parse_plain_lines(_pdf_lines(source), source.stem)
    if suffix in {".txt", ".md", ".markdown"}:
        text = source.read_text(encoding="utf-8-sig")
        return parse_plain_lines(
            [line.strip() for line in text.splitlines() if line.strip()],
            source.stem,
        )
    raise ValueError("支持的试题格式为 JSON、DOCX、PDF、TXT 和 Markdown。")


def _docx_lines(document: DocumentObject) -> Iterator[str]:
    """按文档顺序提取正文段落和表格行。"""

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


def _pdf_lines(path: Path) -> list[str]:
    """提取文本型 PDF；扫描型 PDF 返回明确的 OCR 提示。"""

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
    low_text_pages = sum(count < 30 for count in page_char_counts)
    if total_chars < 120 or (
        page_char_counts and low_text_pages / len(page_char_counts) > 0.7
    ):
        raise ValueError(
            "该 PDF 可提取文字过少，可能是扫描版。当前版本需要先进行 OCR，"
            "或导入可编辑 DOCX、文本型 PDF。"
        )
    return lines


def parse_plain_lines(lines: Iterable[str], title: str = "高中语文试卷") -> dict[str, Any]:
    """使用卷首状态、连续题号和题型证据解析普通文本。"""

    clean_lines = [str(line).strip() for line in lines if str(line).strip()]
    if not clean_lines:
        raise ValueError("文件中没有可识别的文字。")

    metadata = _metadata_from_header(clean_lines, title)
    blocks: list[dict[str, Any]] = []
    pending_material: list[str] = []
    current_question: dict[str, Any] | None = None
    questions_started = False
    expected_number = 1
    in_notice = False
    first_section_seen = False

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
            flush_material()
            in_notice = False
            first_section_seen = True
            current_question = None
            blocks.append({"type": "section_title", "text": line})
            continue

        if in_notice:
            notice_match = NOTICE_ITEM_RE.match(line)
            if notice_match:
                metadata["notices"].append(notice_match.group(1))
                continue
            if SUBSECTION_RE.match(line) or line.startswith("阅读下面"):
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

        if line.startswith("阅读下面"):
            flush_material()
            current_question = None
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
            elif not questions_started and number == 1 and confidence >= 4:
                should_accept = True

            if should_accept:
                flush_material()
                score_match = SCORE_RE.search(stem)
                score = float(score_match.group(1)) if score_match else None
                if score_match:
                    stem = stem[: score_match.start()].rstrip()
                current_question = {
                    "number": number,
                    "kind": "subjective",
                    "stem": stem,
                    "score": score,
                    "options": [],
                }
                blocks.append({"type": "question", "question": current_question})
                questions_started = True
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
    if not question_numbers:
        raise ValueError(
            "没有识别到题目。建议检查题号是否使用“1．题干”形式，"
            "或先保存为可复制文字的 DOCX、PDF。"
        )
    metadata["total_score"] = _score_total_or_default(blocks, metadata["total_score"])
    return {"metadata": metadata, "blocks": blocks}


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
            if ("考试" in line or "试卷" in line) and not NOTICE_RE.match(line)
        ),
        fallback_title,
    )
    subject_name = next(
        (line for line in header if re.fullmatch(r"\s*语\s*文\s*", line)),
        "语　文",
    )
    meta_text = next(
        (
            line
            for line in header
            if ("满分" in line or "考试时间" in line) and not QUESTION_RE.match(line)
        ),
        "请在左侧检查试卷信息、题目分值和版式参数。",
    )
    total_match = re.search(r"满分\s*(\d+(?:\.\d+)?)\s*分", " ".join(header))
    total_score = float(total_match.group(1)) if total_match else 150
    return {
        "exam_name": exam_name,
        "subject_name": subject_name,
        "meta_text": meta_text,
        "total_score": total_score,
        "notices": [],
    }


def _question_confidence(stem: str, lines: list[str], index: int) -> int:
    score = 0
    if SCORE_RE.search(stem):
        score += 3
    if any(cue in stem for cue in QUESTION_CUES):
        score += 2
    if "？" in stem or stem.endswith("?"):
        score += 1
    if _extract_options(stem):
        score += 3
    for following in lines[index + 1 : index + 4]:
        if _extract_options(following):
            score += 3
            break
    return score


def _looks_like_question(line: str, lines: list[str], index: int) -> bool:
    match = QUESTION_RE.match(line)
    return bool(match and _question_confidence(match.group(2), lines, index) >= 3)


def _extract_options(line: str) -> list[str]:
    if not OPTION_START_RE.match(line):
        return []
    matches = INLINE_OPTION_RE.findall(line)
    return [f"{letter}．{text.strip()}" for letter, text in matches if text.strip()]


def _split_subsection(line: str) -> tuple[str, str]:
    marker = line.find("（本题")
    if marker < 0:
        return line, ""
    return line[:marker], line[marker:]


def _is_header_line(line: str, metadata: dict[str, Any]) -> bool:
    return line in {
        metadata.get("exam_name"),
        metadata.get("subject_name"),
        metadata.get("meta_text"),
    } or bool(re.fullmatch(r"\d{4}[.年]\d{1,2}月?", line))


def _score_total_or_default(blocks: list[dict[str, Any]], default: float) -> float:
    scores = [
        block["question"].get("score")
        for block in blocks
        if block.get("type") == "question"
    ]
    numeric = [float(score) for score in scores if score is not None]
    return sum(numeric) if numeric and len(numeric) == len(scores) else default


def save_exam(data: dict[str, Any], path: str | Path) -> Path:
    """保存可继续编辑的结构化试题。"""

    target = Path(path)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
