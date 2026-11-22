"""Formatting passes share a document in memory, while path callers still save their edits."""
from pathlib import Path
from docx import Document
from docx.document import Document as WordDocument

WordTarget = str | Path | WordDocument


def edit_document(target: WordTarget) -> WordDocument:
    return target if isinstance(target, WordDocument) else Document(target)


def save_document(document: WordDocument, target: WordTarget) -> None:
    if not isinstance(target, WordDocument):
        document.save(target)
