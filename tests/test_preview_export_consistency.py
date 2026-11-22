"""Compare actual preview drawing calls with the generated Word paragraphs."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from docx import Document
from PIL import ImageDraw
import pytest

from app.current_pipeline import build_documents
from app.font_resolver import load_preview_font
from app.inspector_model import content_objects_for_block, set_inline_format, set_paragraph_format
from app.internal_preview import render_internal_preview
from tests.test_reference_profile import reference_exam

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "templates/layout.yaml"


def draw_and_export(raw, tmp_path):
    calls = []
    original = ImageDraw.ImageDraw.text
    def record(drawing, xy, text, *args, **kwargs):
        calls.append((xy, str(text), kwargs.get("font")))
        return original(drawing, xy, text, *args, **kwargs)
    with patch.object(ImageDraw.ImageDraw, "text", record):
        preview = render_internal_preview(raw, LAYOUT, tmp_path / "preview")
    path, _, _ = build_documents(raw, LAYOUT, tmp_path, "result")
    return calls, Document(path), preview


def assert_font(calls, token, name, size):
    matches = [font for _, text, font in calls if token in text]
    assert matches, token
    expected = load_preview_font(name, size)
    assert all(font.path == expected.path and font.size == expected.size for font in matches), token


def test_default_profile_and_format_edits_reach_preview_and_export(tmp_path):
    raw = reference_exam()
    raw["metadata"].pop("layout_preset")
    raw["metadata"]["confidentiality_text"] = "开考前保密"
    targets = [(0, "section_title", "一、阅读", "仿宋", 14),
               (2, "instruction", "阅读下面", "楷体", 14),
               (3, "material_note", "注释文字", "宋体", 14),
               (3, "material_source", "摘自测试", "黑体", 14)]
    for index, role, _token, font, size in targets:
        item = next(x for x in content_objects_for_block(raw["blocks"][index], index) if x.role == role)
        set_paragraph_format(raw, item, {"font": font, "size_pt": size})
    question = raw["blocks"][4]["question"]
    question["option_layout"] = "two_column"
    item = content_objects_for_block(raw["blocks"][4], 4)[0]
    set_inline_format(raw, item, 2, 4, {"font": "黑体", "size_pt": 16, "bold": False})
    calls, doc, preview = draw_and_export(raw, tmp_path)
    for _index, _role, token, font, size in targets:
        assert_font(calls, token, font, size)
        paragraph = next(p for p in doc.paragraphs if token in p.text)
        assert all(r.font.size.pt == size for r in paragraph.runs if r.text)
    assert_font(calls, "说法", "黑体", 16)
    stem = next(p for p in doc.paragraphs if p.text.startswith("1．下列"))
    assert any(r.text == "说法" and r.font.size.pt == 16 for r in stem.runs)
    assert "开考前保密" in "".join(text for _, text, _ in calls)
    assert doc.paragraphs[0].text == "开考前保密"
    positions = {text: xy for xy, text, _ in calls if text in question["options"]}
    assert positions["A．选项一"][1] == positions["B．选项二"][1]
    assert positions["A．选项一"][0] < positions["B．选项二"][0]
    assert positions["C．选项三"][1] > positions["A．选项一"][1]
    assert len(doc.tables) == 1 and preview.pages
    choice = doc.tables[0].cell(0, 0).paragraphs[0]
    assert abs(choice.paragraph_format.left_indent.pt - 15.75) < 0.1
    assert abs(choice.paragraph_format.first_line_indent.pt + 17.85) < 0.1


def test_poetry_source_survives_export_and_preview(tmp_path):
    raw = reference_exam()
    raw["blocks"][3]["type"] = "poetry"
    calls, document, _ = draw_and_export(raw, tmp_path)
    source = raw["blocks"][3]["source"]
    assert any(p.text == source for p in document.paragraphs)
    assert source in "".join(text for _, text, _ in calls)


def test_answer_title_table_and_selected_paragraph_agree(tmp_path):
    source = tmp_path / "source-answer.docx"
    original = Document()
    original.add_table(rows=1, cols=1).cell(0, 0).text = "答案表格完整保留"
    original.save(source)
    raw = {"document_kind": "answer", "metadata": {"exam_name": "编辑后的答案标题", "subject_name": "参考答案", "meta_text": "",
           "answer_title_lines": ["旧标题", "旧副标题"], "answer_source_docx_path": str(source), "target_pages": 0,
           "page_overrides": {"margin_left_mm": 28}},
           "blocks": [{"type": "answer_question", "number": 1, "header": "1．答案",
                       "paragraphs": [{"text": "评分细则文字", "role": "scoring_rule"},
                                      {"text": "答案正文文字", "role": "subjective_answer"}]},
                      {"type": "answer_table", "source_table_index": 0}]}
    item = content_objects_for_block(raw["blocks"][0], 0)[1]
    set_paragraph_format(raw, item, {"font": "仿宋", "size_pt": 14})
    calls, doc, _ = draw_and_export(raw, tmp_path)
    text = "".join(value for _, value, _ in calls)
    assert "编辑后的答案标题" in text and "旧标题" not in text
    assert doc.paragraphs[0].text == "编辑后的答案标题"
    assert doc.tables[0].cell(0, 0).text == "答案表格完整保留"
    assert "答案表格完整保留" in text and "[[ANSWER_TABLE" not in text
    assert_font(calls, "评分细则文字", "仿宋", 14)
    paragraph = next(p for p in doc.paragraphs if p.text == "评分细则文字")
    assert paragraph.runs[0].font.name == "FangSong" and paragraph.runs[0].font.size.pt == 14
    assert abs(doc.sections[0].left_margin.mm - 28) < 0.1


def test_appended_answer_title_starts_new_preview_page(tmp_path):
    raw = reference_exam()
    raw["document_kind"] = "exam_with_answer"
    raw["metadata"]["answer_title_lines"] = ["附加答案标题", "参考答案及解析"]
    raw["blocks"].append({"type": "answer_question", "number": 1, "header": "1．A",
                          "paragraphs": [{"text": "答案解析完整内容", "role": "analysis_body"}]})
    calls, doc, preview = draw_and_export(raw, tmp_path)
    drawn = "".join(text for _, text, _ in calls)
    assert "附加答案标题" in drawn and any(p.text == "附加答案标题" for p in doc.paragraphs)
    assert preview.locators[len(raw["blocks"]) - 1][0] > preview.locators[0][0]


def test_subsection_meta_inline_edit_does_not_format_name(tmp_path):
    raw = reference_exam()
    item = content_objects_for_block(raw["blocks"][1], 1)[1]
    set_inline_format(raw, item, 0, len(item.text), {"font": "楷体", "size_pt": 14, "bold": False})
    calls, doc, _ = draw_and_export(raw, tmp_path)
    assert_font(calls, "本题共", "楷体", 14)
    paragraph = next(p for p in doc.paragraphs if p.text.startswith("（一）"))
    assert paragraph.runs[0].font.size.pt == 10.5
    assert paragraph.runs[-1].font.size.pt == 14


def test_missing_native_resource_reports_reimport_instead_of_marker(tmp_path):
    raw = reference_exam()
    raw["blocks"][3]["paragraphs"] = ["[[NATIVE_TABLE:0]]"]
    with pytest.raises(ValueError, match="恢复原文件的位置.*重新导入"):
        render_internal_preview(raw, LAYOUT, tmp_path)


def test_inline_edit_preserves_drawing_in_same_paragraph(tmp_path):
    from PIL import Image
    from app.inline_formatting import apply_inline_formats
    image = tmp_path / "picture.png"
    Image.new("RGB", (80, 40), "red").save(image)
    doc = Document()
    paragraph = doc.add_paragraph("图文正文")
    paragraph.add_run().add_picture(str(image))
    path = tmp_path / "picture.docx"
    doc.save(path)
    raw = {"blocks": [{"type": "material", "paragraphs": ["图文正文"],
            "inline_formats": [{"target": "block", "target_index": 0, "start": 0, "end": 2,
                                "font": "楷体", "size_pt": 14, "bold": True}]}]}
    apply_inline_formats(path, raw)
    result = Document(path)
    assert len(result.inline_shapes) == 1
    assert result.paragraphs[0].text == "图文正文"


def test_legacy_pagination_preserves_explicit_spacing_in_preview_and_word(tmp_path):
    raw = reference_exam()
    raw["metadata"].update(target_pages=8, page_spacing_scale=0.8, source_docx_path="original.docx")
    raw["blocks"][3]["paragraphs"] = ["用于检查行距的连续文字。" * 16]
    item = next(x for x in content_objects_for_block(raw["blocks"][3], 3) if x.target == "paragraph")
    set_paragraph_format(raw, item, {"font": "楷体", "size_pt": 10.5, "line_spacing": 2.5, "space_before_pt": 10, "space_after_pt": 14})
    with patch('app.exporters.pdf_exporter.PdfExporter._export_with_libreoffice', side_effect=AssertionError('unexpected Office calibration')):
        calls, document, _ = draw_and_export(raw, tmp_path / "scaled")
    paragraph = next(p for p in document.paragraphs if "用于检查行距" in p.text)
    assert abs(paragraph.paragraph_format.line_spacing - 2.5) < 0.01
    assert paragraph.paragraph_format.space_before.pt == 10
    assert paragraph.paragraph_format.space_after.pt == 14
    baseline = deepcopy(raw)
    baseline["metadata"]["page_spacing_scale"] = 1
    baseline_calls, _, _ = draw_and_export(baseline, tmp_path / "baseline")
    def line_gap(values):
        positions = [xy[1] for xy, text, _ in values if "检查行距" in text]
        return positions[1] - positions[0]
    assert line_gap(calls) == line_gap(baseline_calls)
    assert raw["blocks"][3]["paragraph_formats"][0]["line_spacing"] == 2.5

def test_long_paper_exceeds_eight_pages_without_changing_text_or_page_images(tmp_path):
    import hashlib
    raw = reference_exam()
    raw["blocks"][3]["paragraphs"] = ["长卷自然分页校验。" * 14 for _ in range(110)]
    def image_hashes(model, folder):
        preview = render_internal_preview(model, LAYOUT, folder)
        assert preview.actual_pages == len(preview.pages) > 8
        return [hashlib.sha256(page.read_bytes()).digest() for page in preview.pages]
    clean = image_hashes(raw, tmp_path / "clean")
    raw["metadata"].update(target_pages=1, page_spacing_scale=0.72)
    assert image_hashes(raw, tmp_path / "legacy") == clean
