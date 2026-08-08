"""Production v0.8.7 with article-note and Windows-state refinements."""

from __future__ import annotations

from pathlib import Path
import tempfile
import tkinter as tk
from typing import Any

import desktop_app as base
import desktop_app_v070 as v070
import desktop_app_v085_release as v085_module
from app.contextual_formatting_v10 import apply_contextual_formatting_v10
from app.flexible_importers_v12 import import_exam
from app.pdf_exporter_silent import SilentPdfExporter
from app.windows_activation_v1 import (
    install_activation_palette,
    remove_duplicate_update_button,
)
from desktop_app_v086_answer_release import (
    ProductionDesktopApp as ProductionDesktopAppV086Answer,
    build_documents_v86_answer,
)


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.7"
v070.import_exam = import_exam


def build_documents_v87(
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
    """Build documents and apply final publication-note formatting."""

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = temporary_dir or output_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    built, _, _ = build_documents_v86_answer(
        raw_exam,
        layout_path,
        output_dir,
        basename,
        template_path=template_path,
        export_docx=export_docx,
        export_pdf=False,
        temporary_dir=work_dir,
    )
    docx_path = built or work_dir / f"{basename}.docx"
    if not docx_path.exists():
        raise RuntimeError("Word 文件未生成。")
    apply_contextual_formatting_v10(docx_path, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = SilentPdfExporter().export(
            docx_path,
            output_dir / f"{basename}.pdf",
        )
    return (docx_path if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v87
v085_module.build_documents_v85 = build_documents_v87


class ProductionDesktopApp(ProductionDesktopAppV086Answer):
    """Use one update entry and Windows-like activation feedback."""

    def __init__(self) -> None:
        super().__init__()
        install_activation_palette(self)

    def _build_ui(self) -> None:
        super()._build_ui()
        remove_duplicate_update_button(self)

    def _tag_block_lines(self, block: dict[str, Any]) -> None:
        super()._tag_block_lines(block)
        if block.get("type") != "material":
            return
        line = 1 + int(bool(block.get("title"))) + int(bool(block.get("author")))
        roles = [str(value) for value in block.get("paragraph_roles", [])]
        for index, _text in enumerate(block.get("paragraphs", [])):
            role = roles[index] if index < len(roles) else "body"
            if role == "publication_note":
                self._add_semantic_tag(
                    f"semantic_publication_note_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "SimSun",
                    10.5,
                    False,
                    justify="right",
                )
            line += 1


def preview_directory() -> Path:
    return Path(tempfile.mkdtemp(prefix="preview_v087_"))


__all__ = ["ProductionDesktopApp", "build_documents_v87"]
