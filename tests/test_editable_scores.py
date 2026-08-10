"""Coverage for the editable document and score model."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.editable_a4_canvas import block_editor_text, question_editor_lines
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

    def test_question_serialization_preserves_structural_lines(self) -> None:
        block = next(
            block
            for block in self.raw["blocks"]
            if block.get("type") == "question" and block["question"]["number"] == 23
        )
        lines, mapping = question_editor_lines(block["question"])
        self.assertEqual(len(lines), len(mapping))
        self.assertIn("composition_prompt", {item["target"] for item in mapping})
        self.assertEqual(block_editor_text(block), "\n".join(lines))

    def test_current_entry_imports_without_starting_tk(self) -> None:
        import desktop_app

        self.assertEqual(desktop_app.APP_VERSION, "0.1.0")

    def test_pagination_signature_skips_rebuild(self) -> None:
        from app.editable_a4_canvas import EditableA4Canvas

        canvas = object.__new__(EditableA4Canvas)
        canvas._pagination_signature = None
        canvas.selected_key = None
        canvas.raw_exam = {}
        calls: list[dict] = []

        def render(raw_exam, **kwargs):
            calls.append({"raw_exam": raw_exam, "kwargs": kwargs})

        canvas.render = render
        raw = {"blocks": []}
        locators = {0: (0, 0.25)}
        canvas.set_pagination(raw, locators, 1)
        canvas.set_pagination(raw, locators, 1)

        self.assertEqual(len(calls), 1)
        self.assertIs(canvas.raw_exam, raw)

    def test_surface_layout_is_idempotent(self) -> None:
        from app.editable_a4_canvas import EditableA4Canvas

        class FakeCanvas:
            def __init__(self) -> None:
                self.configured: list[tuple[object, ...]] = []
                self.position = [0.0, 0.0]
            def winfo_width(self) -> int:
                return 800
            def itemconfigure(self, *_args, **kwargs) -> None:
                self.configured.append((*_args, kwargs))
            def coords(self, *_args):
                return list(self.position)
            def bbox(self, *_args):
                return (0, 0, 800, 1200)
            def configure(self, **kwargs) -> None:
                self.configured.append((kwargs,))
        class FakeSurface:
            def winfo_reqwidth(self) -> int:
                return 620

        surface = object.__new__(EditableA4Canvas)
        surface.canvas = FakeCanvas()
        surface.surface = FakeSurface()
        surface.surface_window = 1
        surface._last_surface_width = 0
        surface._last_scrollregion = None

        # First pass establishes width and scroll region.
        surface._center_surface()
        surface.canvas.position = [0.0, 0.0]
        surface._update_scrollregion()
        count = len(surface.canvas.configured)

        # Repeating the same geometry produces no new canvas mutation.
        surface._center_surface()
        surface._update_scrollregion()
        self.assertEqual(len(surface.canvas.configured), count)


if __name__ == "__main__":
    unittest.main()
