"""Windows workbench v0.8.1 production entry."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import desktop_app as base
import desktop_app_v070 as v070
from app.chinese_typography_v2 import enable_chinese_typography_v2
from app.config import load_layout
from app.exam_format_rules_v3 import apply_exam_format_rules_v3
from app.flexible_importers_v7 import import_exam
from app.header_extras_v1 import apply_header_extras_v1
from app.inline_formatting_v3 import apply_inline_formats_v3
from app.models import ExamDocument
from app.native_docx_objects import restore_native_objects
from app.pagination import apply_pagination_guards
from app.paragraph_formatting_v1 import apply_paragraph_formats_v1
from app.pdf_exporter_silent import SilentPdfExporter
from app.renderers import DocxRenderer
from app.semantic_formatting_v4 import apply_semantic_formatting_v4
from app.source_decorations_v1 import restore_source_decorations_v1
from app.validators import check_required_fonts, validate_exam
from desktop_app_v080_production import ProductionDesktopApp


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.1"
v070.import_exam = import_exam


def build_documents_v81(
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
    """Render the final protected and editable Word document."""

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
    restore_source_decorations_v1(docx_work, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = SilentPdfExporter().export(
            docx_work,
            output_dir / f"{basename}.pdf",
        )
    return (docx_work if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v81


def main() -> int:
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
    ProductionDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
