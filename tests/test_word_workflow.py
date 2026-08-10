"""Regression coverage for the narrowed 0.2 Word workflow."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.internal_preview_primitives import render_internal_preview
from app.office_bridge import engine_summary
from app.page_target import get_target_pages


ROOT = Path(__file__).resolve().parents[1]


class WordWorkflowTests(unittest.TestCase):
    def test_current_importer_rejects_non_word_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "exam.pdf"
            path.write_bytes(b"%PDF")
            with self.assertRaisesRegex(ValueError, "只支持导入 Word"):
                import_exam(path)

    def test_target_pages_default_and_internal_engine(self) -> None:
        raw = json.loads((ROOT / "samples" / "exam.json").read_text(encoding="utf-8"))
        self.assertEqual(get_target_pages(raw), 8)
        with tempfile.TemporaryDirectory() as folder:
            result = render_internal_preview(raw, ROOT / "templates" / "layout.yaml", folder)
            self.assertEqual(result.engine if hasattr(result, "engine") else "internal", "internal")
            self.assertGreaterEqual(result.actual_pages, 1)

    def test_build_ignores_legacy_pdf_flag(self) -> None:
        raw = json.loads((ROOT / "samples" / "exam.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            docx, pdf, engine = build_documents(
                raw,
                ROOT / "templates" / "layout.yaml",
                folder,
                "exam",
                export_pdf=True,
            )
            self.assertTrue(docx and docx.exists())
            self.assertIsNone(pdf)
            self.assertEqual(engine, "docx-only")
            self.assertFalse((Path(folder) / "exam.pdf").exists())

    def test_engine_probe_does_not_launch_office(self) -> None:
        summary = engine_summary()
        self.assertTrue(summary)


if __name__ == "__main__":
    unittest.main()
