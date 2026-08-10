from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.inspector_model import (
    build_object_locators,
    content_objects_for_block,
    metadata_content_objects,
    objects_in_scope,
    paragraph_format_for,
    set_content_object_text,
    set_paragraph_format,
)
from app.page_layout import adjusted_layout


ROOT = Path(__file__).resolve().parents[1]


def sample_exam() -> dict:
    return json.loads((ROOT / "samples" / "exam.json").read_text(encoding="utf-8"))


class InspectorModelTests(unittest.TestCase):
    def test_question_is_split_into_stem_and_options(self) -> None:
        raw = sample_exam()
        block_index = next(
            index
            for index, block in enumerate(raw["blocks"])
            if block.get("type") == "question"
        )
        items = content_objects_for_block(raw["blocks"][block_index], block_index)
        self.assertEqual(items[0].role, "question_stem")
        self.assertEqual([item.role for item in items[1:5]], ["choice_option"] * 4)
        self.assertEqual(len({item.key for item in items}), len(items))

    def test_editing_one_option_keeps_its_siblings(self) -> None:
        raw = sample_exam()
        block_index = next(
            index
            for index, block in enumerate(raw["blocks"])
            if block.get("type") == "question"
        )
        items = content_objects_for_block(raw["blocks"][block_index], block_index)
        option = next(item for item in items if item.target == "option" and item.target_index == 1)
        original = list(raw["blocks"][block_index]["question"]["options"])
        set_content_object_text(raw, option, "B．修改后的选项")
        updated = raw["blocks"][block_index]["question"]["options"]
        self.assertEqual(updated[1], "B．修改后的选项")
        self.assertEqual(updated[0], original[0])
        self.assertEqual(updated[2:], original[2:])

    def test_paragraph_override_targets_only_one_material_paragraph(self) -> None:
        raw = sample_exam()
        block_index = next(
            index
            for index, block in enumerate(raw["blocks"])
            if block.get("type") == "material" and len(block.get("paragraphs", [])) > 1
        )
        items = content_objects_for_block(raw["blocks"][block_index], block_index)
        body = [item for item in items if item.role == "material_body"]
        set_paragraph_format(raw, body[1], {"font": "宋体", "size_pt": 12, "bold": True})
        first = paragraph_format_for(raw, body[0])
        second = paragraph_format_for(raw, body[1])
        self.assertEqual(first["font"], "楷体")
        self.assertEqual(second["font"], "宋体")
        self.assertEqual(second["size_pt"], 12)

    def test_batch_scope_current_question_does_not_cross_questions(self) -> None:
        raw = sample_exam()
        block_index = next(
            index
            for index, block in enumerate(raw["blocks"])
            if block.get("type") == "question" and len(block.get("question", {}).get("options", [])) == 4
        )
        selected = next(
            item
            for item in content_objects_for_block(raw["blocks"][block_index], block_index)
            if item.role == "choice_option"
        )
        targets = objects_in_scope(raw, selected, "当前题目")
        self.assertEqual(len(targets), 4)
        self.assertTrue(all(item.block_index == block_index for item in targets))

    def test_object_locators_include_header_and_every_question_line(self) -> None:
        raw = sample_exam()
        anchors = {-1: (0, 0.0)}
        for index, _block in enumerate(raw["blocks"]):
            anchors[index] = (index // 10, (index % 10) / 10)
        locators = build_object_locators(raw, anchors)
        for item in metadata_content_objects(raw):
            self.assertIn(item.key, locators)
        question_index = next(index for index, block in enumerate(raw["blocks"]) if block.get("type") == "question")
        for item in content_objects_for_block(raw["blocks"][question_index], question_index):
            self.assertIn(item.key, locators)

    def test_page_margin_overrides_are_clamped_and_applied(self) -> None:
        raw = sample_exam()
        raw["metadata"]["page_overrides"] = {
            "margin_top_mm": 24,
            "margin_bottom_mm": 16,
            "margin_left_mm": 60,
        }
        layout = {
            "page": {
                "margin_top_mm": 20,
                "margin_bottom_mm": 18,
                "margin_left_mm": 22,
                "margin_right_mm": 18,
            },
            "defaults": {"body_line_spacing": 1.25},
            "styles": {},
        }
        adjusted = adjusted_layout(layout, raw)
        self.assertEqual(adjusted["page"]["margin_top_mm"], 24)
        self.assertEqual(adjusted["page"]["margin_bottom_mm"], 16)
        self.assertEqual(adjusted["page"]["margin_left_mm"], 45)


if __name__ == "__main__":
    unittest.main()
