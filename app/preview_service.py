"""Cancellable preview service using the current renderer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Any, Callable

from .internal_preview import render_internal_preview


@dataclass(frozen=True, slots=True)
class PreviewResult:
    generation: int
    pages: tuple[Path, ...]
    engine: str
    locators: dict[int, tuple[int, float]]
    target_pages: int
    actual_pages: int


class PreviewService:
    def __init__(self, _builder: Callable[..., Any] | None = None) -> None:
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
        _template_path: str | Path | None,
        callback: Callable[[PreviewResult | Exception], None],
    ) -> int:
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
            rendered = render_internal_preview(raw_exam, layout_path, task_dir / "pages")
            if cancel_event.is_set():
                raise RuntimeError("预览任务已取消")
            return PreviewResult(
                generation,
                rendered.pages,
                "internal",
                rendered.locators,
                rendered.target_pages,
                rendered.actual_pages,
            )

        future = self._executor.submit(work)
        with self._lock:
            self._future = future

        def done(completed: Future[PreviewResult]) -> None:
            try:
                result: PreviewResult | Exception = completed.result()
            except Exception as exc:  # noqa: BLE001
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
        with self._lock:
            self._generation += 1
            self._cancel_event.set()
            future = self._future
        if future is not None:
            future.cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(self._root, ignore_errors=True)


__all__ = ["PreviewResult", "PreviewService"]
