"""Tests for the cancellable preview service."""

from __future__ import annotations

from pathlib import Path
import tempfile
from threading import Event
import time
import unittest

from pypdf import PdfWriter

from app.preview_service import PreviewService, PreviewResult


class PreviewServiceTests(unittest.TestCase):
    def test_service_delivers_result_and_cleans_on_close(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with source.open("wb") as handle:
                writer.write(handle)

            def builder(_raw, _layout, output, basename, **_kwargs):
                target = Path(output) / f"{basename}.pdf"
                target.write_bytes(source.read_bytes())
                return None, target, "test"

            service = PreviewService(builder)
            received: list[PreviewResult | Exception] = []
            done = Event()
            service.submit({}, "layout.yaml", None, lambda value: (received.append(value), done.set()))
            self.assertTrue(done.wait(10))
            self.assertEqual(len(received), 1)
            self.assertIsInstance(received[0], PreviewResult)
            result = received[0]
            assert isinstance(result, PreviewResult)
            self.assertEqual(len(result.pages), 1)
            service.close()
            self.assertFalse(result.pages[0].exists())

    def test_new_submission_invalidates_previous_generation(self) -> None:
        started = Event()
        release = Event()

        def builder(_raw, _layout, output, basename, **_kwargs):
            started.set()
            release.wait(5)
            target = Path(output) / f"{basename}.pdf"
            target.write_bytes(b"%PDF-1.4")
            return None, target, "test"

        service = PreviewService(builder)
        received: list[PreviewResult | Exception] = []
        service.submit({}, "layout.yaml", None, received.append)
        self.assertTrue(started.wait(5))
        service.submit({}, "layout.yaml", None, received.append)
        release.set()
        time.sleep(0.3)
        service.close()
        self.assertLessEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
