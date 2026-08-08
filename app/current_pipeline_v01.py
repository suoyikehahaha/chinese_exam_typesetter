"""0.1 pipeline wrapper with final run-property protection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .current_pipeline import build_documents as build_documents_base
from .pdf_exporter_silent import SilentPdfExporter
from .run_formatting_v01 import protect_inline_properties


def build_documents(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
    basename: str,
    *,
    template_path: str | Path | None = None,
    export_docx: bool = True,
    export_pdf: bool = False,
    temporary_dir: str | Path | None = None,
) -> tuple[Path | None, Path | None, str]:
    """Build DOCX once, protect run properties, then optionally create PDF."""

    docx_path, _unused_pdf, _unused_engine = build_documents_base(
        raw_exam,
        layout_path,
        output_dir,
        basename,
        template_path=template_path,
        export_docx=export_docx,
        export_pdf=False,
        temporary_dir=temporary_dir,
    )
    if docx_path is None:
        return None, None, "docx-only"
    protect_inline_properties(docx_path)
    if not export_pdf:
        return docx_path, None, "docx-only"
    target = Path(output_dir) / f"{basename}.pdf"
    pdf_path, engine = SilentPdfExporter().export(docx_path, target)
    return docx_path, pdf_path, engine


__all__ = ["build_documents"]
