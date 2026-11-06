"""Converted DOC resources outlive the conversion folder and saved draft."""

import json
from pathlib import Path
import shutil
from unittest.mock import patch

from docx import Document
from PIL import Image
import pytest

from app.current_importer import import_exam
from app.current_pipeline import build_documents

ROOT = Path(__file__).resolve().parents[1]


def test_converted_doc_keeps_picture_and_table_after_temporary_cleanup(tmp_path):
    picture = tmp_path / "picture.png"
    Image.new("RGB", (20, 20), "blue").save(picture)
    converted = tmp_path / "fixture.docx"
    doc = Document()
    doc.add_paragraph("一、阅读")
    doc.add_paragraph("1．请分析下列材料。（6分）")
    doc.add_picture(str(picture))
    doc.add_table(rows=1, cols=2).cell(0, 0).text = "材料表格"
    doc.save(converted)
    original = tmp_path / "old.doc"
    original.write_bytes(b"conversion fixture")
    paths = []

    def conversion(_source, target):
        paths.append(target)
        shutil.copy2(converted, target)

    with patch("app.current_importer.convert_doc_to_docx", side_effect=conversion):
        raw = import_exam(original)
    assert not paths[0].exists()
    raw = json.loads(json.dumps(raw))
    raw["metadata"]["target_pages"] = 0
    result, _, _ = build_documents(raw, ROOT / "templates/layout.yaml", tmp_path, "output")
    document = Document(result)
    assert len(document.inline_shapes) == 1
    assert len(document.tables) == 1
    assert document.tables[0].cell(0, 0).text == "材料表格"


def test_missing_native_source_does_not_silently_export_placeholder(tmp_path):
    raw = {"metadata": {"exam_name": "资源检查", "meta_text": "", "document_scope": "practice", "target_pages": 0,
                        "source_docx_path": str(tmp_path / "missing.docx"),
                        "native_objects": [{"kind": "drawing", "marker": "[[NATIVE_DRAWING:0]]"}]},
           "blocks": [{"type": "material", "paragraphs": ["[[NATIVE_DRAWING:0]]"]},
                      {"type": "question", "question": {"number": 1, "kind": "subjective", "stem": "请分析", "score": 6}}]}
    with pytest.raises(ValueError, match="图片或表格"):
        build_documents(raw, ROOT / "templates/layout.yaml", tmp_path, "output")
    assert not (tmp_path / "output.docx").exists()
