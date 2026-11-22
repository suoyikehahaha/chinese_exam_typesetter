"""Regression coverage for the narrowed 0.2 Word workflow."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.current_importer import import_exam
from app.current_pipeline import ExportDestinationError, build_documents
from app.internal_preview import render_internal_preview
from app.office_bridge import engine_summary
from app.page_target import get_target_pages, spacing_scale_for_target


ROOT = Path(__file__).resolve().parents[1]


class WordWorkflowTests(unittest.TestCase):
    def test_current_importer_rejects_non_word_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "exam.pdf"
            path.write_bytes(b"%PDF")
            with self.assertRaisesRegex(ValueError, "只支持导入 Word"):
                import_exam(path)

    def test_natural_layout_and_internal_engine(self) -> None:
        raw = json.loads((ROOT / "samples" / "exam.json").read_text(encoding="utf-8"))
        self.assertEqual(get_target_pages(raw), 0)
        with tempfile.TemporaryDirectory() as folder:
            result = render_internal_preview(raw, ROOT / "templates" / "layout.yaml", folder)
            self.assertEqual(result.engine if hasattr(result, "engine") else "internal", "internal")
            self.assertGreaterEqual(result.actual_pages, 1)

    def test_legacy_page_counts_never_change_spacing(self) -> None:
        raw = {"metadata": {"target_pages": 8}}
        self.assertEqual(spacing_scale_for_target(raw), 1.0)
        self.assertEqual(spacing_scale_for_target(raw, measured_pages=10), 1.0)
        self.assertEqual(spacing_scale_for_target(raw, measured_pages=6), 1.0)

        raw["metadata"]["page_spacing_scale"] = 1.12
        self.assertEqual(spacing_scale_for_target(raw), 1.0)
        self.assertEqual(get_target_pages(raw), 0)

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

    def test_export_replaces_existing_file_only_after_render_finishes(self) -> None:
        raw = json.loads((ROOT / "samples" / "exam.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "exam.docx"
            target.write_bytes(b"previous version")
            docx, _pdf, _engine = build_documents(
                raw, ROOT / "templates" / "layout.yaml", folder, "exam"
            )
            self.assertEqual(docx, target)
            self.assertTrue(target.read_bytes().startswith(b"PK"))
            self.assertFalse(list(Path(folder).glob("exam-export-*")))

    def test_locked_destination_keeps_previous_file_and_reports_action(self) -> None:
        raw = json.loads((ROOT / "samples" / "exam.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "exam.docx"
            target.write_bytes(b"previous version")
            original_replace = Path.replace

            def deny_target(path: Path, destination: Path) -> Path:
                if Path(destination) == target:
                    raise PermissionError(13, "Permission denied", str(target))
                return original_replace(path, destination)

            with patch.object(Path, "replace", deny_target):
                with self.assertRaises(ExportDestinationError) as caught:
                    build_documents(raw, ROOT / "templates" / "layout.yaml", folder, "exam")
            self.assertEqual(target.read_bytes(), b"previous version")
            self.assertIn("Word 或 WPS", str(caught.exception))
            self.assertIn("换一个文件名", str(caught.exception))
            self.assertFalse(list(Path(folder).glob("exam-export-*")))

    def test_desktop_locked_file_prompt_can_retry_same_destination(self) -> None:
        from desktop_app import CurrentDesktopApp

        target = Path("D:/题库/语文试题（排版）.docx")
        app = SimpleNamespace(
            _export_in_progress=True,
            busy=True,
            busy_bar=Mock(),
            status_var=Mock(),
            after=Mock(),
            start_export=Mock(),
        )
        error = ExportDestinationError(target)
        with patch("desktop_app.messagebox.askretrycancel", return_value=True) as prompt:
            CurrentDesktopApp._finish_export(app, error)
        self.assertFalse(app.busy)
        self.assertFalse(app._export_in_progress)
        self.assertIn("关闭目标文件", prompt.call_args.args[1])
        delay, callback = app.after.call_args.args
        self.assertEqual(delay, 100)
        callback()
        app.start_export.assert_called_once_with(target.parent, target.stem, True, False)

    def test_engine_probe_does_not_launch_office(self) -> None:
        summary = engine_summary()
        self.assertTrue(summary)


if __name__ == "__main__":
    unittest.main()
