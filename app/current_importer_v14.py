"""Current Word importer with automatic-numbering support."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

from .current_importer import _normalize_publication_notes, _normalize_same_line_title_authors
from .flexible_importers_v13 import import_exam as import_exam_numbered
from .models.identity import ensure_block_ids
from .office_bridge_v01 import convert_doc_to_docx


def import_exam(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in {".docx", ".doc"}:
        raise ValueError("当前版本只支持导入 Word 文档（.docx 或 .doc）。")
    if suffix == ".doc":
        with tempfile.TemporaryDirectory(prefix="exam-doc-convert-") as folder:
            converted = Path(folder) / f"{source.stem}.docx"
            convert_doc_to_docx(source, converted)
            result = import_exam_numbered(converted)
    else:
        result = import_exam_numbered(source)
    ensure_block_ids(result)
    result.setdefault("metadata", {})["source_format"] = suffix[1:]
    diagnostics: list[dict[str, Any]] = list(result.get("diagnostics", []))
    _normalize_same_line_title_authors(result, diagnostics)
    _normalize_publication_notes(result)
    result["diagnostics"] = diagnostics
    return result


__all__ = ["import_exam"]
