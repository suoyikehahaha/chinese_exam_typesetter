"""Tests for resilient Word and LibreOffice preview conversion."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.pdf_exporter_silent_v01 import SilentPdfExporter


class PdfExporterResilienceTests(unittest.TestCase):
    def test_word_cleanup_script_swallows_quit_rpc_errors(self) -> None:
        script = SilentPdfExporter._build_word_script(
            Path(r"C:\exam'source.docx"),
            Path(r"C:\preview.pdf"),
        )
        self.assertIn("try {$word.Quit($false)} catch {}", script)
        self.assertIn("FinalReleaseComObject($word)", script)
        self.assertIn("FinalReleaseComObject($doc)", script)

    def test_valid_pdf_is_accepted_when_word_returns_cleanup_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.docx"
            target = root / "preview.pdf"
            source.write_bytes(b"docx placeholder")

            def fake_run(*_args, **_kwargs):
                target.write_bytes(b"%PDF-1.7\nvalid preview content that is long enough\n")
                return type("Completed", (), {"returncode": 1, "stderr": "RPC cleanup error", "stdout": ""})()

            with patch.object(SilentPdfExporter, "_find_powershell", return_value=Path("powershell.exe")):
                with patch("app.pdf_exporter_silent_v01.subprocess.run", side_effect=fake_run):
                    SilentPdfExporter._export_with_word(source, target)

            self.assertTrue(target.exists())
            self.assertTrue(SilentPdfExporter._is_valid_pdf(target))

    def test_missing_libreoffice_reports_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.docx"
            target = root / "preview.pdf"
            source.write_bytes(b"docx placeholder")
            with patch("app.pdf_exporter_silent_v01._find_libreoffice", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "LibreOffice"):
                    SilentPdfExporter._export_with_libreoffice(source, target)


if __name__ == "__main__":
    unittest.main()
