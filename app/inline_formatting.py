"""Apply selected character formatting (font, size, bold, underline, emphasis dot)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from .paragraph_formatting import _locate
from .word_document import WordTarget, edit_document, save_document


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
            raw_font = entry.get("font")
            font_name = FONT_NAMES.get(str(raw_font), str(raw_font)) if raw_font else None
            size = float(entry["size_pt"]) if entry.get("size_pt") is not None else None
            bold = bool(entry["bold"]) if "bold" in entry and entry["bold"] is not None else None
            underline = (
                bool(entry["underline"])
                if "underline" in entry and entry["underline"] is not None
                else None
            )
            emphasis = entry.get("emphasis") if "emphasis" in entry else None
            _format_range(
                paragraph,
                int(entry.get("start", 0)) + prefix,
                int(entry.get("end", 0)) + prefix,
                font_name=font_name,
                size=size,
                bold=bold,
                underline=underline,
                emphasis=str(emphasis) if emphasis is not None else None,
            )
    save_document(document, docx_path)


def _format_range(
    paragraph: Any,
    start: int,
    end: int,
    font_name: str | None = None,
    size: float | None = None,
    bold: bool | None = None,
    *,
    underline: bool | None = None,
    emphasis: str | None = None,
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
            for element in source_run._r.xpath(
                "./w:drawing | ./w:pict | ./w:object | ./*[local-name()='AlternateContent']"
            ):
                run._r.append(deepcopy(element))
        if source_run._r.rPr is not None:
            run._r.insert(0, deepcopy(source_run._r.rPr))
        if selected:
            rpr = run._element.get_or_add_rPr()
            if font_name:
                run.font.name = font_name
                rfonts = rpr.get_or_add_rFonts()
                for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                    rfonts.set(qn(f"w:{key}"), font_name)
            if size is not None:
                run.font.size = Pt(size)
            if bold is not None:
                run.bold = bold
            if underline is not None:
                old_u = rpr.find(qn("w:u"))
                if old_u is not None:
                    rpr.remove(old_u)
                if underline:
                    elem_u = OxmlElement("w:u")
                    elem_u.set(qn("w:val"), "single")
                    rpr.append(elem_u)
                else:
                    run.underline = False
            if emphasis is not None:
                old_em = rpr.find(qn("w:em"))
                if old_em is not None:
                    rpr.remove(old_em)
                if emphasis:
                    elem_em = OxmlElement("w:em")
                    elem_em.set(qn("w:val"), emphasis)
                    rpr.append(elem_em)
