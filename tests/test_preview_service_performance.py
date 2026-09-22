from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch
from app.preview_service import PreviewService
from app.internal_preview_core import InternalPreviewResult
from app.preview_cancellation import check_cancelled


class PreviewServicePerformanceTests(TestCase):
    def test_identical_requests_reuse_images_but_return_new_generation(self):
        with TemporaryDirectory() as folder:
            layout = Path(folder) / 'layout.yaml'
            layout.write_text('styles: {}', encoding='utf-8')
            calls = []
            def render(raw, layout_path, output):
                calls.append(raw)
                output.mkdir(parents=True)
                page = output / 'page-1.png'
                page.write_bytes(b'fixture')
                return InternalPreviewResult((page,), {}, 0, 1)
            service = PreviewService()
            try:
                with patch('app.preview_service.render_internal_preview', side_effect=render):
                    results = []
                    for _ in range(2):
                        done = Event()
                        service.submit({'metadata': {'document_scope': 'practice'}, 'blocks': []}, layout, None, lambda r: (results.append(r), done.set()))
                        self.assertTrue(done.wait(5))
                    self.assertEqual(len(calls), 1)
                    self.assertGreater(results[1].generation, results[0].generation)
                    self.assertTrue(results[1].pages[0].is_file())
            finally:
                service.close()

    def test_new_request_cancels_old_and_cleans_stale_output(self):
        with TemporaryDirectory() as folder:
            layout = Path(folder) / 'layout.yaml'
            layout.write_text('styles: {}', encoding='utf-8')
            started, release, finished = Event(), Event(), Event()
            callbacks = []
            def render(raw, layout_path, output):
                if raw.get('old'):
                    started.set()
                    release.wait(5)
                    check_cancelled()
                output.mkdir(parents=True)
                page = output / 'page-1.png'
                page.write_bytes(b'fixture')
                return InternalPreviewResult((page,), {}, 0, 1)
            service = PreviewService()
            try:
                with patch('app.preview_service.render_internal_preview', side_effect=render):
                    service.submit({'old': True, 'metadata': {'target_pages': 0}}, layout, None, callbacks.append)
                    self.assertTrue(started.wait(5))
                    service.submit({'metadata': {'target_pages': 0}}, layout, None, lambda r: (callbacks.append(r), finished.set()))
                    release.set()
                    self.assertTrue(finished.wait(5))
                    self.assertEqual(len(callbacks), 1)
                    self.assertFalse((service._root / 'generation-1').exists())
            finally:
                release.set()
                service.close()
