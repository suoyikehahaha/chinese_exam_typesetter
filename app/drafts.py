"""Portable, atomic project files and private recovery copies for active windows."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from .models import ExamDocument
from .page_target import use_natural_pagination
from .resource_anchors import bind_source_resources
from .source_document import embed_sources, source_document, source_read_session


def read_draft(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list) or not isinstance(raw.get("metadata"), dict):
        raise ValueError("草稿缺少试卷信息或结构数据")
    ExamDocument.from_dict(raw)
    use_natural_pagination(raw["metadata"])
    bind_source_resources(raw)
    metadata = raw["metadata"]
    with source_read_session():
        for role in metadata.get("source_refs", {}):
            if role not in {"exam", "answer"}:
                raise ValueError("草稿包含无法识别的图表资源。")
            source_document(metadata, answer=role == "answer")
        if metadata.get("converted_docx_base64"):
            source_document(metadata)
    return raw


def save_draft(raw, path, *, keep_resources=False):
    target = Path(path)
    data = {**raw, "metadata": dict(raw.get("metadata", {}))}
    use_natural_pagination(data["metadata"])
    embed_sources(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".exam-draft-", suffix=".tmp", delete=False) as stream:
            staged = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        read_draft(staged)
        staged.replace(target)
        if keep_resources:
            for key in ("source_refs", "source_blobs"):
                if key in data["metadata"]:
                    raw["metadata"][key] = data["metadata"][key]
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return target


def _lock(stream, take):
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)


class DraftRecovery:
    """A live window owns its recovery file. Writes and discards run in FIFO order."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / (uuid4().hex + ".json")
        self._owner = None
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exam-recovery")
        self._last = None

    def _claim(self):
        if self._owner is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._owner = self.path.with_suffix(".lock").open("a+b")
            _lock(self._owner, True)

    def save(self, raw, on_error):
        snapshot = deepcopy(raw)
        def write():
            try:
                self._claim()
                save_draft(snapshot, self.path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                on_error(str(exc))
        if self._last is not None:
            self._last.cancel()
        self._last = self._writer.submit(write)

    def clear(self):
        if self._last is not None:
            self._last.cancel()
            self._writer.submit(self.path.unlink, missing_ok=True)

    @contextmanager
    def claim_abandoned(self, path):
        """Hold ownership across the read and the recovery decision."""
        lock_path = path.with_suffix(".lock")
        with lock_path.open("a+b") as stream:
            try:
                _lock(stream, True)
            except OSError:
                yield False
            else:
                try:
                    yield path.is_file()
                finally:
                    _lock(stream, False)
        if not path.exists():
            lock_path.unlink(missing_ok=True)

    def candidates(self):
        found = []
        for path in self.directory.glob("*.json"):
            if path != self.path:
                try:
                    found.append((path.stat().st_mtime_ns, path))
                except FileNotFoundError:
                    continue
        return [path for _, path in sorted(found, reverse=True)]

    def close(self):
        self._writer.shutdown(wait=True, cancel_futures=True)
        if self._owner is not None:
            self.path.unlink(missing_ok=True)
            _lock(self._owner, False)
            self._owner.close()
            self.path.with_suffix(".lock").unlink(missing_ok=True)
