"""Regression tests for the current runtime foundations."""

from __future__ import annotations

import unittest

from app.current_importer import import_exam as current_import_exam
from app.models.identity import ensure_block_ids
from app.style_registry import StyleRegistry
from app.version import APP_VERSION


class RuntimeV01Tests(unittest.TestCase):
    def test_version_matches_current_release(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.5")

    def test_block_ids_are_stable_and_unique(self) -> None:
        raw = {"blocks": [{"type": "instruction"}, {"type": "question", "question": {}}]}
        ensure_block_ids(raw)
        self.assertEqual([item["id"] for item in raw["blocks"]], ["block-1", "block-2"])
        self.assertEqual(raw["blocks"][1]["question"]["id"], "block-2-question")
        ensure_block_ids(raw)
        self.assertEqual(raw["blocks"][0]["id"], "block-1")

    def test_duplicate_ids_receive_suffix_without_data_loss(self) -> None:
        raw = {"blocks": [{"id": "same", "text": "a"}, {"id": "same", "text": "b"}]}
        ensure_block_ids(raw)
        self.assertEqual(raw["blocks"][0]["id"], "same")
        self.assertEqual(raw["blocks"][1]["id"], "same-2")
        self.assertEqual([item["text"] for item in raw["blocks"]], ["a", "b"])

    def test_same_line_title_author_is_normalized(self) -> None:
        import app.current_importer as importer

        original = importer.import_exam_legacy
        try:
            importer.import_exam_legacy = lambda _path: {
                "metadata": {},
                "blocks": [
                    {
                        "type": "material",
                        "paragraphs": ["卢沟桥之夜  林斤澜", "正文第一段。"],
                    }
                ],
            }
            result = current_import_exam("sample.docx")
        finally:
            importer.import_exam_legacy = original
        material = result["blocks"][0]
        self.assertEqual(material["title"], "卢沟桥之夜")
        self.assertEqual(material["author"], "林斤澜")
        self.assertEqual(material["paragraphs"], ["正文第一段。"])

    def test_style_registry_rejects_incomplete_layout(self) -> None:
        with self.assertRaises(ValueError):
            StyleRegistry({"styles": {"body": {"font": "SimSun", "size_pt": 10.5}}})


if __name__ == "__main__":
    unittest.main()
