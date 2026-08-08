"""Windows workbench v0.8.2 with adaptive validation and faster startup."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import desktop_app as base
import desktop_app_v070 as v070
from app.chinese_typography_v2 import enable_chinese_typography_v2
from app.config import load_layout
from app.contextual_formatting_v5 import apply_contextual_formatting_v5
from app.exam_format_rules_v3 import apply_exam_format_rules_v3
from app.flexible_importers_v8 import import_exam
from app.header_extras_v1 import apply_header_extras_v1
from app.inline_formatting_v3 import apply_inline_formats_v3
from app.models import ExamDocument
from app.native_docx_objects import restore_native_objects
from app.pagination import apply_pagination_guards
from app.paragraph_formatting_v1 import apply_paragraph_formats_v1
from app.pdf_exporter_silent import SilentPdfExporter
from app.renderers import DocxRenderer
from app.segmentation_formatting_v2 import apply_segmentation_formatting_v2
from app.semantic_formatting_v4 import apply_semantic_formatting_v4
from app.source_decorations_v1 import restore_source_decorations_v1
from app.validators import check_required_fonts
from app.validators.exam_validator_v2 import validate_exam
from desktop_app_v081_final import FinalProductionDesktopApp


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.2"
v070.import_exam = import_exam


def build_documents_v82(
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
    """Render a protected, editable Word document and its internal preview."""

    layout = load_layout(layout_path)
    exam = ExamDocument.from_dict(raw_exam)
    issues = validate_exam(exam)
    errors = [item.message for item in issues if item.severity == "error"]
    if errors:
        raise ValueError("结构校验未通过：\n" + "\n".join(errors))
    font_result = check_required_fonts(sorted(set(layout["fonts"].values())))
    missing = [name for name, installed in font_result.items() if not installed]
    if missing:
        raise RuntimeError("缺少字体：" + "、".join(missing))

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = temporary_dir or output_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    docx_work = (output_dir if export_docx else work_dir) / f"{basename}.docx"
    DocxRenderer(layout, template_path).render(exam, docx_work)
    apply_header_extras_v1(docx_work, raw_exam)
    apply_pagination_guards(docx_work)
    restore_native_objects(docx_work, raw_exam)
    apply_semantic_formatting_v4(docx_work)
    apply_exam_format_rules_v3(docx_work)
    apply_paragraph_formats_v1(docx_work, raw_exam)
    apply_inline_formats_v3(docx_work, raw_exam)
    enable_chinese_typography_v2(docx_work)
    apply_contextual_formatting_v5(docx_work, raw_exam)
    apply_segmentation_formatting_v2(docx_work)
    restore_source_decorations_v1(docx_work, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = SilentPdfExporter().export(
            docx_work,
            output_dir / f"{basename}.pdf",
        )
    return (docx_work if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v82


class AdaptiveDesktopApp(FinalProductionDesktopApp):
    """Show the editor first and generate the initial preview after it is ready."""

    def __init__(self) -> None:
        self._startup_preview_deferred = True
        self._startup_preview_pending = False
        super().__init__()
        self.status_var.set("工作台已就绪，正在准备首次预览。")
        self.after(1300, self._release_startup_preview)

    def request_preview(self) -> None:
        if self._startup_preview_deferred:
            self._startup_preview_pending = True
            return
        super().request_preview()

    def _release_startup_preview(self) -> None:
        self._startup_preview_deferred = False
        if self._startup_preview_pending:
            self._startup_preview_pending = False
            super().request_preview()


def run_cli() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", metavar="OUTPUT")
    parser.add_argument("--import-test", metavar="FILE")
    args, _ = parser.parse_known_args()
    if args.self_test:
        return base.self_test(Path(args.self_test))
    if args.import_test:
        data = import_exam(Path(args.import_test))
        print(
            [
                block["question"]["number"]
                for block in data["blocks"]
                if block.get("type") == "question"
            ]
        )
        return 0
    AdaptiveDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
