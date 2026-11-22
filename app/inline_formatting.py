"""Apply selected character formatting, including bold."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .word_document import WordTarget, edit_document, save_document
from docx.oxml.ns import qn
from docx.shared import Pt

from .paragraph_formatting import _locate


FONT_NAMES = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
}


def apply_inline_formats(docx_path: WordTarget, raw_exam: dict[str, Any]) -> None:
    """Apply saved selected ranges after all paragraph formatting."""

    document = edit_document(docx_path)
    for block in raw_exam.get("blocks", []):
        if str(block.get("type", "")).startswith("answer_"):
            continue
        entries = (
            block.get("question", {}).get("inline_formats", [])
            if block.get("type") == "question"
            else block.get("inline_formats", [])
        )
        for entry in entries:
            paragraph, prefix = _locate(document, block, entry)
            if paragraph is None:
                continue
            _format_range(
                paragraph,
                int(entry.get("start", 0)) + prefix,
                int(entry.get("end", 0)) + prefix,
                FONT_NAMES.get(str(entry.get("font", "")), str(entry.get("font", ""))),
                float(entry.get("size_pt", 10.5)),
                bool(entry.get("bold", False)),
            )
    save_document(document, docx_path)


def _format_range(
    paragraph: Any,
    start: int,
    end: int,
    font_name: str,
    size: float,
    bold: bool,
) -> None:
    if start >= end:
        return
    old_runs = list(paragraph.runs)
    fragments: list[tuple[str | None, Any, bool]] = []
    cursor = 0
    for run in old_runs:
        text = run.text
        run_start = cursor
        run_end = cursor + len(text)
        points = {run_start, run_end}
        if run_start < start < run_end:
            points.add(start)
        if run_start < end < run_end:
            points.add(end)
        boundaries = sorted(points)
        for left, right in zip(boundaries, boundaries[1:]):
            fragments.append(
                (
                    text[left - run_start : right - run_start],
                    run,
                    start <= left and right <= end,
                )
            )
        cursor = run_end
        if run._r.xpath("./w:drawing | ./w:pict | ./w:object | ./*[local-name()='AlternateContent']"):
            fragments.append((None, run, False))
    for run in old_runs:
        paragraph._p.remove(run._r)
    for text, source_run, selected in fragments:
        run = paragraph.add_run(text or "")
        if text is None:
            for element in source_run._r.xpath("./w:drawing | ./w:pict | ./w:object | ./*[local-name()='AlternateContent']"):
                run._r.append(deepcopy(element))
        if source_run._r.rPr is not None:
            run._r.insert(0, deepcopy(source_run._r.rPr))
        if selected:
            run.font.name = font_name
            run.font.size = Pt(size)
            run.bold = bold
            rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
            for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                rfonts.set(qn(f"w:{key}"), font_name)
