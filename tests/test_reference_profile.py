"""Check exported OOXML against the annotated reference roles."""

from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from app.current_pipeline import build_documents
from app.flexible_importers import parse_plain_lines
from app.inspector_model import content_objects_for_block, set_paragraph_format

ROOT = Path(__file__).resolve().parents[1]


def reference_exam():
    return {"metadata": {"exam_name": "模板核对", "subject_name": "语　文", "target_pages": 0,
                         "meta_text": "本试卷共1小题，满分3分。考试用时10分钟。", "notices": ["请认真作答。"],
                         "layout_preset": "national_ii_2026", "document_scope": "practice"},
            "blocks": [{"type": "section_title", "text": "一、阅读（3分）"},
                       {"type": "subsection", "name": "（一）阅读Ⅰ", "meta": "（本题共1小题，3分）"},
                       {"type": "instruction", "text": "阅读下面的文字，完成1题。"},
                       {"type": "material", "title": "材料标题", "author": "张三", "paragraphs": ["材料正文。"],
                        "note": "[注] 注释文字。", "source": "（摘自测试材料）"},
                       {"type": "question", "question": {"number": 1, "kind": "objective", "score": 3,
                        "stem": "下列说法正确的一项是", "options": ["A．选项一", "B．选项二", "C．选项三", "D．选项四"]}}]}


def effective(paragraph, attribute):
    value = getattr(paragraph.paragraph_format, attribute)
    return value if value is not None else getattr(paragraph.style.paragraph_format, attribute)


def test_reference_export_fonts_indents_and_subsection_runs(tmp_path):
    path, _, _ = build_documents(reference_exam(), ROOT / "templates/layout.yaml", tmp_path, "template")
    document = Document(path)
    paragraphs = {p.style.name.removeprefix("Exam_"): p for p in document.paragraphs}
    for role, font, size in [("exam_name", "SimSun", 16), ("subject_name", "SimHei", 22),
                             ("section_title", "SimHei", 12), ("material_title", "SimHei", 10.5),
                             ("material_author", "KaiTi", 10.5), ("material_body", "KaiTi", 10.5),
                             ("material_note", "FangSong", 9), ("material_source", "FangSong", 10.5)]:
        run = next(r for r in paragraphs[role].runs if r.text)
        assert run.font.size.pt == size, role
        for key in ("ascii", "hAnsi", "eastAsia", "cs"):
            assert run._r.rPr.rFonts.get(qn("w:" + key)) == font, role
    meta = paragraphs["exam_meta"]
    assert effective(meta, "alignment") == WD_ALIGN_PARAGRAPH.LEFT
    assert effective(meta, "first_line_indent").pt == 21
    assert effective(paragraphs["objective_stem"], "left_indent").pt == 0
    option = paragraphs["choice_option"]
    assert abs(effective(option, "left_indent").pt - 15.75) < 0.1
    assert abs(effective(option, "first_line_indent").pt + 17.85) < 0.1
    subsection = paragraphs["subsection"]
    assert subsection.runs[0].bold
    assert not subsection.runs[-1].bold
    section = document.sections[0]
    assert abs(section.header_distance.mm - 15) < 0.1
    assert abs(section.footer_distance.mm - 20) < 0.1


def test_explicit_format_wins_over_reference_defaults(tmp_path):
    raw = reference_exam()
    author = next(item for item in content_objects_for_block(raw["blocks"][3], 3) if item.role == "material_author")
    set_paragraph_format(raw, author, {"font": "宋体", "size_pt": 12})
    original = deepcopy(raw)
    path, _, _ = build_documents(raw, ROOT / "templates/layout.yaml", tmp_path, "custom")
    paragraph = next(p for p in Document(path).paragraphs if p.text == "张三")
    assert paragraph.runs[0].font.name == "SimSun"
    assert paragraph.runs[0].font.size.pt == 12
    assert raw["blocks"][3]["paragraph_formats"] == original["blocks"][3]["paragraph_formats"]


def test_nested_outline_inside_question_is_not_exam_section():
    raw = parse_plain_lines(["测试试卷", "一、阅读（6分）", "1．请根据材料完成下列提纲。（6分）",
                             "一、什么是廊桥？", "二、桥上有房子", "三、历史与文化", "2．请简要概括。（3分）"])
    assert [b["text"] for b in raw["blocks"] if b["type"] == "section_title"] == ["一、阅读（6分）"]
    first = next(b["question"] for b in raw["blocks"] if b["type"] == "question")
    assert len(first["embedded_segments"]) == 3
