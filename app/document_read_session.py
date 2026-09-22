"""Reuse DOCX reads within one import, without retaining user documents globally."""
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from docx import Document as open_document

_documents = ContextVar("import_documents", default=None)


def Document(path=None):
    cache = _documents.get()
    if cache is None or not isinstance(path, (str, Path)):
        return open_document(path)
    key = str(Path(path).resolve())
    if key not in cache:
        cache[key] = open_document(path)
    return cache[key]


def document_read_session(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _documents.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _documents.reset(token)
    return wrapped
