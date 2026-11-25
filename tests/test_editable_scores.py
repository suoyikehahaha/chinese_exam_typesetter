"""Coverage for the editable document and score model."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from app.inspector_model import content_objects_for_block
from app.score_summary import calculate_score_summary, format_score

ROOT = Path(__file__).resolve().parents[1]


class EditableScoresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = json.loads(
            (ROOT / "samples" / "exam.json").read_text(encoding="utf-8")
        )

    def test_sample_reaches_fixed_150_target(self) -> None:
        summary = calculate_score_summary(self.raw)
        self.assertTrue(summary.complete)
        self.assertEqual(format_score(summary.total), "150")
        self.assertEqual(summary.missing_questions, ())

    def test_missing_and_over_target_are_reported(self) -> None:
        questions = [
            block["question"]
            for block in self.raw["blocks"]
            if block.get("type") == "question"
        ]
        questions[0]["score"] = None
        summary = calculate_score_summary(self.raw)
        self.assertIn("1", summary.missing_questions)
        questions[0]["score"] = 10
        summary = calculate_score_summary(self.raw)
        self.assertLess(summary.difference, 0)

    def test_subquestion_scores_replace_parent_for_accounting(self) -> None:
        question = next(
            block["question"]
            for block in self.raw["blocks"]
            if block.get("type") == "question" and block["question"]["number"] == 13
        )
        question["score"] = 99
        question["subquestion_scores"] = [4, 4]
        summary = calculate_score_summary(self.raw)
        self.assertEqual(format_score(summary.total), "150")

    def test_question_objects_preserve_composition_structure(self) -> None:
        index, block = next(
            (i, block)
            for i, block in enumerate(self.raw["blocks"])
            if block.get("type") == "question" and block["question"]["number"] == 23
        )
        items = content_objects_for_block(block, index)
        targets = {item.target for item in items}
        self.assertIn("composition_prompt", targets)
        self.assertIn("composition_requirements", targets)

    def test_current_entry_imports_without_initializing_qt(self) -> None:
        import desktop_app
        from app.version import APP_VERSION

        self.assertEqual(desktop_app.APP_VERSION, APP_VERSION)
        self.assertNotIn("tkinter", sys.modules)


if __name__ == "__main__":
    unittest.main()
