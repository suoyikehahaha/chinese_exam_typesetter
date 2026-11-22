"""Read a read-only Word resource once per operation, including legacy embedded DOCs."""
import base64
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document

_cache = ContextVar("exam_source_documents", default=None)


@contextmanager
def source_read_session():
    if _cache.get() is not None:
        yield
        return
    token = _cache.set({})
    try:
        yield
    finally:
        _cache.reset(token)


def embed_sources(raw):
    metadata = raw["metadata"]
    if metadata.get("converted_docx_base64"):
        return
    refs = dict(metadata.get("source_refs", {}))
    blobs = dict(metadata.get("source_blobs", {}))
    known = {}
    for role, key in (("exam", "source_docx_path"), ("answer", "answer_source_docx_path")):
        if role in refs:
            if refs[role] not in blobs:
                raise ValueError("草稿图表资源不完整，请重新导入原文件。")
            continue
        path = Path(str(metadata.get(key) or ""))
        if not path.is_file() or path.suffix.lower() != ".docx":
            required = (role == "exam" and metadata.get("native_objects") and raw.get("document_kind") != "answer"
                        or role == "answer" and (any(b.get("type") == "answer_table" for b in raw["blocks"])
                                                  or raw.get("document_kind") == "answer" and metadata.get("native_objects")))
            if required:
                raise ValueError("保存草稿需要原始 Word 图表资源，请恢复原文件位置后重试。")
            continue
        path = path.resolve()
        if path not in known:
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest not in blobs:
                try:
                    with ZipFile(BytesIO(payload)) as package:
                        if "word/document.xml" not in package.namelist():
                            raise ValueError(f"{path.name} 缺少 Word 正文。")
                except BadZipFile as exc:
                    raise ValueError(f"{path.name} 不是完整的 Word 文件。") from exc
                blobs[digest] = base64.b64encode(payload).decode("ascii")
            known[path] = digest
        refs[role] = known[path]
    if refs:
        metadata.update(source_refs=refs, source_blobs=blobs)


def source_document(metadata: dict, *, answer: bool = False):
    role = "answer" if answer else "exam"
    digest = metadata.get("source_refs", {}).get(role)
    embedded = metadata.get("converted_docx_base64")
    path = Path(str(metadata.get("answer_source_docx_path" if answer else "source_docx_path") or ""))
    key = ("resource", digest) if digest else (("converted", id(embedded)) if embedded else ("path", path))
    cache = _cache.get()
    if cache is not None and key in cache:
        return cache[key]
    if digest or embedded:
        try:
            encoded = metadata.get("source_blobs", {})[digest] if digest else embedded
            payload = base64.b64decode(encoded, validate=True)
            if digest and hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError("资源校验失败")
            document = Document(BytesIO(payload))
        except (KeyError, ValueError, OSError, BadZipFile) as exc:
            raise ValueError("草稿中的 Word 资源已损坏，请重新导入原始文档。") from exc
    else:
        document = Document(path) if path.is_file() else None
    if cache is not None:
        cache[key] = document
    return document
