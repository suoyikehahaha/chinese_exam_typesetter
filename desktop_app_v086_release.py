"""Production v0.8.6 with flexible notices and semantic material headings."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

import desktop_app as base
import desktop_app_v070 as v070
from app.contextual_formatting_v7 import apply_contextual_formatting_v7
from app.flexible_importers_v10 import import_exam
from app.pdf_exporter_silent import SilentPdfExporter
from desktop_app_v085_release import (
    ProductionDesktopApp as ProductionDesktopAppV085,
    build_documents_v85,
)


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.6"
v070.import_exam = import_exam


def build_documents_v86(
    raw_exam: dict[str, Any],
    layout_path: Path,
    output_dir: Path,
    basename: str,
    *,
    template_path: Path | None = None,
    export_docx: bool = True,
    export_pdf: bool = True,
    temporary_dir: Path | None = None,
) -> tuple[Path | None, Path | None, str]:
    """Build a DOCX and apply the final context-aware pass before preview."""

    work_dir = temporary_dir or output_dir
    docx_dir = output_dir if export_docx else work_dir
    docx_path, _, _ = build_documents_v85(
        raw_exam,
        layout_path,
        docx_dir,
        basename,
        template_path=template_path,
        export_docx=True,
        export_pdf=False,
        temporary_dir=work_dir,
    )
    if docx_path is None:
        raise RuntimeError("Word 文件未生成。")
    apply_contextual_formatting_v7(docx_path, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = SilentPdfExporter().export(
            docx_path,
            output_dir / f"{basename}.pdf",
        )
    return (docx_path if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v86


class ProductionDesktopApp(ProductionDesktopAppV085):
    """Display semantic subheadings and sources in the left editor."""

    def _tag_block_lines(self, block: dict[str, Any]) -> None:
        super()._tag_block_lines(block)
        if block.get("type") != "material":
            return
        line = 1 + int(bool(block.get("title"))) + int(bool(block.get("author")))
        roles = [str(value) for value in block.get("paragraph_roles", [])]
        for index, _paragraph in enumerate(block.get("paragraphs", [])):
            role = roles[index] if index < len(roles) else "body"
            if role == "subheading":
                self._add_semantic_tag(
                    f"semantic_role_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "SimHei",
                    10.5,
                    False,
                    justify="center",
                )
            elif role == "source":
                self._add_semantic_tag(
                    f"semantic_role_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "FangSong",
                    10.5,
                    False,
                    justify="right",
                )
            elif role == "label":
                self._add_semantic_tag(
                    f"semantic_role_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "SimHei",
                    10.5,
                    False,
                )
            line += 1


def preview_directory() -> Path:
    """Return a temporary preview directory for diagnostics and tests."""

    return Path(tempfile.mkdtemp(prefix="preview_v086_"))


__all__ = ["ProductionDesktopApp", "build_documents_v86"]
