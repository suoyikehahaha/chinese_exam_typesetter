"""DOCX-only v0.4 pipeline with target pages and page-margin overrides."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import load_layout
from .current_pipeline_v01 import build_documents as build_documents_base
from .current_pipeline_v02 import _adjust_inline_spacing, _dump_yaml
from .page_layout_v04 import adjusted_layout_v04
from .page_target_v01 import spacing_scale_for_target


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
) -> tuple[Path | None, None, str]:
    """Build a DOCX while keeping Office and WPS optional."""

    adjusted = deepcopy(raw_exam)
    _adjust_inline_spacing(adjusted, spacing_scale_for_target(adjusted))
    output = Path(output_dir)
    work = Path(temporary_dir) if temporary_dir else output
    work.mkdir(parents=True, exist_ok=True)
    target_layout = work / "layout-v04-targeted.yaml"
    target_layout.write_text(
        _dump_yaml(adjusted_layout_v04(load_layout(layout_path), adjusted)),
        encoding="utf-8",
    )
    try:
        docx_path, _ignored_pdf, _ignored_engine = build_documents_base(
            adjusted,
            target_layout,
            output,
            basename,
            template_path=template_path,
            export_docx=export_docx,
            export_pdf=False,
            temporary_dir=work,
        )
    finally:
        target_layout.unlink(missing_ok=True)
    return docx_path, None, "docx-only"


__all__ = ["build_documents"]
