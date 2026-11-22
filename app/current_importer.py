"""Word-only importer facade used by the current desktop runtime."""

from __future__ import annotations

from pathlib import Path
import base64
import tempfile
from typing import Any

from .flexible_importers import import_exam as import_exam_legacy
from .models.identity import ensure_block_ids
from .page_target import use_natural_pagination
from .resource_anchors import bind_source_resources
from .layout_presets import DEFAULT_PRESET
from .office_bridge import convert_doc_to_docx


from .import_material_roles import _normalize_publication_notes, _normalize_same_line_title_authors


def import_exam(path: str | Path) -> dict[str, Any]:
    """Import a DOCX or legacy DOC through the current facade.

    Historical importers still exist for old projects, while the current
    desktop file picker intentionally exposes only Word documents.
    """

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in {".docx", ".doc"}:
        raise ValueError(
            "当前版本只支持导入 Word 文档（.docx 或 .doc）。"
            "PDF、TXT、Markdown 和 JSON 不属于当前桌面导入格式。"
        )
    if suffix == ".doc":
        with tempfile.TemporaryDirectory(prefix="exam-doc-convert-") as folder:
            converted = Path(folder) / f"{source.stem}.docx"
            convert_doc_to_docx(source, converted)
            result = import_exam_legacy(converted)
            metadata = result.setdefault("metadata", {})
            metadata["converted_docx_base64"] = base64.b64encode(converted.read_bytes()).decode("ascii")
            metadata["source_docx_path"] = str(source.resolve())
            if metadata.get("answer_source_docx_path"):
                metadata["answer_source_docx_path"] = str(source.resolve())
    else:
        result = import_exam_legacy(source)
    ensure_block_ids(result)
    result.setdefault("metadata", {})["source_format"] = suffix[1:]
    result["metadata"]["layout_preset"] = DEFAULT_PRESET
    diagnostics: list[dict[str, Any]] = list(result.get("diagnostics", []))
    _normalize_same_line_title_authors(result, diagnostics)
    _normalize_publication_notes(result)
    result["diagnostics"] = diagnostics
    use_natural_pagination(result["metadata"])
    bind_source_resources(result)
    return result


__all__ = ["import_exam"]
