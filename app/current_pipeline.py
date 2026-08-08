"""Single document build pipeline for runtime version 0.1.

The implementation keeps the mature low-level DOCX passes, yet owns their
ordering in one place. The desktop layer calls this function directly and no
longer relies on version modules mutating one another's globals.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .answer_overrides_v1 import apply_answer_overrides_v1
from .answer_typesetting_v1 import append_answer_to_docx, exam_blocks, render_answer_docx
from .chinese_typography_v2 import enable_chinese_typography_v2
from .config import load_layout
from .contextual_formatting_v5 import apply_contextual_formatting_v5
from .contextual_formatting_v6 import apply_contextual_formatting_v6
from .contextual_formatting_v9 import apply_contextual_formatting_v9
from .contextual_formatting_v10 import apply_contextual_formatting_v10
from .contextual_formatting_v11 import apply_contextual_formatting_v11
from .exam_format_rules_v3 import apply_exam_format_rules_v3
from .header_extras_v1 import apply_header_extras_v1
from .inline_formatting_v3 import apply_inline_formats_v3
from .models import ExamDocument
from .models.identity import ensure_block_ids
from .native_docx_objects import restore_native_objects
from .pagination import apply_pagination_guards
from .paragraph_formatting_v1 import apply_paragraph_formats_v1
from .pdf_exporter_silent import SilentPdfExporter
from .renderers import DocxRenderer
from .segmentation_formatting_v2 import apply_segmentation_formatting_v2
from .semantic_formatting_v4 import apply_semantic_formatting_v4
from .source_decorations_v1 import restore_source_decorations_v1
from .style_registry import StyleRegistry
from .validators import check_required_fonts
from .validators.exam_validator_v2 import validate_exam


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
    """Render one exam or answer document with an explicit pass sequence."""

    ensure_block_ids(raw_exam)
    layout = load_layout(layout_path)
    StyleRegistry(layout)
    exam = ExamDocument.from_dict(raw_exam)
    issues = validate_exam(exam)
    errors = [issue.message for issue in issues if issue.severity == "error"]
    if errors:
        raise ValueError("结构校验未通过：\n" + "\n".join(errors))

    required_fonts = sorted(set(str(value) for value in layout["fonts"].values()))
    font_result = check_required_fonts(required_fonts)
    missing = [name for name, installed in font_result.items() if not installed]
    if missing:
        raise RuntimeError("缺少字体：" + "、".join(missing))

    output = Path(output_dir)
    work = Path(temporary_dir) if temporary_dir else output
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    docx_dir = output if export_docx else work
    docx_path = docx_dir / f"{basename}.docx"

    if raw_exam.get("document_kind") == "answer":
        render_answer_docx(raw_exam, docx_path)
    else:
        exam_raw = deepcopy(raw_exam)
        exam_raw["blocks"] = exam_blocks(raw_exam)
        exam_document = ExamDocument.from_dict(exam_raw)
        DocxRenderer(layout, template_path).render(exam_document, docx_path)
        _run_exam_passes(docx_path, exam_raw)
        apply_contextual_formatting_v9(docx_path, exam_raw)
        append_answer_to_docx(docx_path, raw_exam)
        restore_source_decorations_v1(docx_path, raw_exam)

    apply_answer_overrides_v1(docx_path, raw_exam)
    apply_contextual_formatting_v10(docx_path, raw_exam)
    apply_contextual_formatting_v11(docx_path, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = SilentPdfExporter().export(
            docx_path,
            output / f"{basename}.pdf",
        )
    return (docx_path if export_docx else None), pdf_path, engine


def _run_exam_passes(docx_path: Path, raw_exam: dict[str, Any]) -> None:
    """Apply deterministic DOCX passes in one documented order."""

    apply_header_extras_v1(docx_path, raw_exam)
    apply_pagination_guards(docx_path)
    restore_native_objects(docx_path, raw_exam)
    apply_semantic_formatting_v4(docx_path)
    apply_exam_format_rules_v3(docx_path)
    apply_paragraph_formats_v1(docx_path, raw_exam)
    apply_inline_formats_v3(docx_path, raw_exam)
    enable_chinese_typography_v2(docx_path)
    apply_contextual_formatting_v5(docx_path, raw_exam)
    apply_contextual_formatting_v6(docx_path, raw_exam)
    apply_segmentation_formatting_v2(docx_path)
    restore_source_decorations_v1(docx_path, raw_exam)


__all__ = ["build_documents"]
