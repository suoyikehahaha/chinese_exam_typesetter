"""Cancellable preview service using the current renderer."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import json
import hashlib
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Any, Callable

from .internal_preview import render_internal_preview
from .page_target import get_target_pages, spacing_scale_for_target
from .preview_cancellation import cancel_event as worker_cancel_event


@dataclass(frozen=True, slots=True)
class PreviewResult:
    generation: int
    pages: tuple[Path, ...]
    engine: str
    locators: dict[int, tuple[int, float]]
    target_pages: int
    actual_pages: int
    spacing_scale: float
    warnings: tuple[str, ...] = ()


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
        self._cached_key: str | None = None
        self._cached_result: PreviewResult | None = None

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
            previous_future = self._future

        if previous_future is not None:
            previous_future.cancel()
        # Freeze at submission, not when a queued worker eventually starts.
        prepared = deepcopy(raw_exam)
        signature = deepcopy(prepared)
        signature.setdefault("metadata", {}).pop("page_spacing_scale", None)
        source = Path(str(signature["metadata"].get("source_docx_path", "")))
        source_stamp = None
        if source.is_file():
            stat = source.stat()
            source_stamp = (stat.st_size, stat.st_mtime_ns)
        cache_key = hashlib.sha256((json.dumps(signature, sort_keys=True, ensure_ascii=False) + repr(source_stamp)).encode("utf-8") + Path(layout_path).read_bytes()).hexdigest()

        def work() -> PreviewResult:
            if cancel_event.is_set():
                raise RuntimeError("预览任务已取消")
            cached = self._cached_result
            if self._cached_key == cache_key and cached and all(path.is_file() for path in cached.pages):
                pages = []
                for index, path in enumerate(cached.pages):
                    if cancel_event.is_set():
                        raise RuntimeError("预览任务已取消")
                    target = task_dir / f"page-{index + 1}.png"
                    shutil.copy2(path, target)
                    pages.append(target)
                return PreviewResult(generation, tuple(pages), cached.engine, dict(cached.locators), cached.target_pages, cached.actual_pages, cached.spacing_scale, cached.warnings)
            metadata = prepared.setdefault("metadata", {})
            current_scale = spacing_scale_for_target(prepared)
            target_pages = get_target_pages(prepared)
            best: tuple[int, Any, float] | None = None
            document_kind = str(prepared.get("document_kind", "exam"))
            iterations = 1 if target_pages == 0 or document_kind in {"answer", "exam_with_answer"} else 4
            for iteration in range(iterations):
                metadata["page_spacing_scale"] = current_scale
                token = worker_cancel_event.set(cancel_event)
                try:
                    rendered = render_internal_preview(prepared, layout_path, task_dir / f"pages-{iteration}")
                finally:
                    worker_cancel_event.reset(token)
                if cancel_event.is_set():
                    raise RuntimeError("预览任务已取消")
                distance = abs(rendered.actual_pages - target_pages)
                if best is None or distance < best[0]:
                    best = (distance, rendered, current_scale)
                if distance == 0:
                    break
                next_scale = spacing_scale_for_target(
                    prepared,
                    measured_pages=rendered.actual_pages,
                    current_scale=current_scale,
                )
                if abs(next_scale - current_scale) < 0.0001:
                    break
                current_scale = next_scale
            if best is None:
                raise RuntimeError("预览未生成任何页面")
            _distance, rendered, selected_scale = best
            return PreviewResult(
                generation,
                rendered.pages,
                "internal",
                rendered.locators,
                rendered.target_pages,
                rendered.actual_pages,
                selected_scale,
                getattr(rendered, "warnings", ()),
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
                    self._cached_key = cache_key
                    self._cached_result = result
                else:
                    previous = None
            if previous is not None:
                shutil.rmtree(previous, ignore_errors=True)
            if stale or isinstance(result, Exception):
                shutil.rmtree(task_dir, ignore_errors=True)
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
