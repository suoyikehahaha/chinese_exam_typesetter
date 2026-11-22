"""Cancellable preview service using the current renderer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import json
import struct
import hashlib
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Any, Callable

from .source_document import source_read_session
from .internal_preview import render_internal_preview
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
    page_info: tuple[tuple[int, int, str], ...] = ()


def _page_info(pages):
    sizes = []
    for path in pages:
        payload = Path(path).read_bytes()
        if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
            return ()
        sizes.append((*struct.unpack(">II", payload[16:24]), hashlib.sha256(payload).hexdigest()))
    return tuple(sizes)


@source_read_session()
def render_quick_preview(prepared, layout_path, task_dir, generation, cancel_event):
    token = worker_cancel_event.set(cancel_event)
    try:
        rendered = render_internal_preview(prepared, layout_path, task_dir / "pages")
    finally:
        worker_cancel_event.reset(token)
    if cancel_event.is_set():
        raise RuntimeError("预览任务已取消")
    return PreviewResult(generation, rendered.pages, "internal", rendered.locators, 0, rendered.actual_pages,
                         1.0, getattr(rendered, "warnings", ()), _page_info(rendered.pages))


class PreviewService:
    def __init__(self, _builder: Callable[..., Any] | None = None) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exam-preview")
        self._proof_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exam-print")
        self._root = Path(tempfile.mkdtemp(prefix="exam-preview-service-"))
        self._lock = Lock()
        self._generation = 0
        self._cancel_event = Event()
        self._future: Future[PreviewResult] | None = None
        self._buffers: dict[int, Path] = {}
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
        *, print_proof=False,
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
            previous_future = self._future

        if previous_future is not None:
            previous_future.cancel()
        # Freeze at submission, not when a queued worker eventually starts.
        prepared = deepcopy(raw_exam)
        cache_key = None
        def work() -> PreviewResult:
            nonlocal cache_key
            if cancel_event.is_set():
                raise RuntimeError("预览任务已取消")
            metadata = prepared.get("metadata", {})
            signature = {**prepared, "metadata": {k: v for k, v in metadata.items()
                                                  if k not in {"target_pages", "page_spacing_scale", "source_blobs"}}}
            source_stamp = []
            for key in ("source_docx_path", "answer_source_docx_path"):
                source = Path(metadata.get(key) or "")
                try:
                    stat = source.stat() if source.is_file() and not metadata.get("source_refs") else None
                except OSError:
                    stat = None
                source_stamp.append((stat.st_size, stat.st_mtime_ns) if stat else None)
            content = json.dumps(signature, sort_keys=True, ensure_ascii=False).encode("utf-8")
            mode = "print" if print_proof else "internal"
            cache_key = hashlib.sha256(content + repr((mode, source_stamp)).encode() + Path(layout_path).read_bytes()).hexdigest()
            with self._lock:
                cached = self._cached_result if self._cached_key == cache_key else None
            if cached and all(p.is_file() for p in cached.pages):
                return replace(cached, generation=generation)
            task_dir.mkdir(parents=True, exist_ok=True)
            if not print_proof:
                return render_quick_preview(prepared, layout_path, task_dir, generation, cancel_event)
            from .print_proof import render_print_proof
            pages, engine = render_print_proof(prepared, layout_path, _template_path, task_dir, cancel_event)
            return PreviewResult(generation, pages, engine, {}, 0, len(pages), 1.0, page_info=_page_info(pages))

        executor = self._proof_executor if print_proof else self._executor
        future = executor.submit(work)
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
                    if self._cached_result is None or result.pages != self._cached_result.pages:
                        self._current_dir = task_dir
                    self._buffers[generation] = self._current_dir
                    self._cached_key = cache_key
                    self._cached_result = result
            if stale or isinstance(result, Exception):
                shutil.rmtree(task_dir, ignore_errors=True)
            if not stale:
                callback(result)

        future.add_done_callback(done)
        return generation

    def acknowledge(self, generation: int) -> None:
        """Only retire older page files after Tk has installed the replacement."""
        with self._lock:
            if generation not in self._buffers:
                return
            older = {path for gen, path in self._buffers.items() if gen < generation}
            self._buffers = {gen: path for gen, path in self._buffers.items() if gen >= generation}
            retired = older.difference(self._buffers.values())
        for path in retired:
            shutil.rmtree(path, ignore_errors=True)

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
        self._proof_executor.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(self._root, ignore_errors=True)


__all__ = ["PreviewResult", "PreviewService"]
