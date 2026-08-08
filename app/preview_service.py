"""Cancellable, single-flight preview generation service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Any, Callable

import pypdfium2 as pdfium

from .preview_locator_v2 import build_preview_locators


@dataclass(frozen=True, slots=True)
class PreviewResult:
    """Immutable result delivered to the GUI thread."""

    generation: int
    pages: tuple[Path, ...]
    pdf_path: Path
    engine: str
    locators: dict[int, tuple[int, float]]


class PreviewService:
    """Run at most one preview task and discard stale generations."""

    def __init__(self, builder: Callable[..., tuple[Path | None, Path | None, str]]) -> None:
        self._builder = builder
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exam-preview")
        self._root = Path(tempfile.mkdtemp(prefix="exam-preview-service-"))
        self._lock = Lock()
        self._generation = 0
        self._cancel_event = Event()
        self._future: Future[PreviewResult] | None = None
        self._current_dir: Path | None = None
        self._closed = False

    def submit(
        self,
        raw_exam: dict[str, Any],
        layout_path: str | Path,
        template_path: str | Path | None,
        callback: Callable[[PreviewResult | Exception], None],
    ) -> int:
        """Submit a preview and invoke callback once on completion."""

        with self._lock:
            if self._closed:
                raise RuntimeError("预览服务已经关闭")
            self._generation += 1
            generation = self._generation
            self._cancel_event.set()
            self._cancel_event = Event()
            cancel_event = self._cancel_event
            task_dir = self._root / f"generation-{generation}"
            task_dir.mkdir(parents=True, exist_ok=True)

        def work() -> PreviewResult:
            if cancel_event.is_set():
                raise RuntimeError("预览任务已取消")
            _, pdf_path, engine = self._builder(
                raw_exam,
                Path(layout_path),
                task_dir,
                "preview",
                template_path=template_path,
                export_docx=True,
                export_pdf=True,
                temporary_dir=task_dir,
            )
            if cancel_event.is_set():
                raise RuntimeError("预览任务已取消")
            if pdf_path is None:
                raise RuntimeError("内部预览 PDF 未生成")
            pages = tuple(rasterize_pdf(pdf_path, task_dir / "pages"))
            if cancel_event.is_set():
                raise RuntimeError("预览任务已取消")
            locators = build_preview_locators(pdf_path, raw_exam)
            return PreviewResult(generation, pages, pdf_path, engine, locators)

        future = self._executor.submit(work)
        with self._lock:
            self._future = future

        def done(completed: Future[PreviewResult]) -> None:
            try:
                result: PreviewResult | Exception = completed.result()
            except Exception as exc:  # callback boundary converts task failures
                result = exc
            with self._lock:
                stale = self._closed or generation != self._generation
                if isinstance(result, PreviewResult) and not stale:
                    previous = self._current_dir
                    self._current_dir = task_dir
                else:
                    previous = None
            if previous is not None:
                shutil.rmtree(previous, ignore_errors=True)
            if not stale:
                callback(result)

        future.add_done_callback(done)
        return generation

    def cancel(self) -> None:
        """Cancel the current task; an already-running converter may finish safely."""

        with self._lock:
            self._generation += 1
            self._cancel_event.set()
            future = self._future
        if future is not None:
            future.cancel()

    def close(self) -> None:
        """Stop work and remove owned preview files."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(self._root, ignore_errors=True)


def rasterize_pdf(pdf_path: str | Path, output_dir: str | Path) -> list[Path]:
    """Rasterize all pages while closing PDFium resources deterministically."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf_path))
    pages: list[Path] = []
    try:
        for index, page in enumerate(document):
            bitmap = page.render(scale=1.65)
            target = output / f"page-{index + 1}.png"
            bitmap.to_pil().save(target)
            pages.append(target)
    finally:
        document.close()
    return pages


__all__ = ["PreviewResult", "PreviewService", "rasterize_pdf"]
