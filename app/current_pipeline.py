"""Single document build pipeline for runtime version 0.1.

The implementation keeps the mature low-level DOCX passes, yet owns their
ordering in one place. The desktop layer calls this function directly and no
longer relies on version modules mutating one another's globals.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .answer_overrides import apply_answer_overrides
from .answer_typesetting import append_answer_to_docx, exam_blocks, render_answer_docx
from .chinese_typography import enable_chinese_typography
from .config import load_layout
from .exam_context_formatting import apply_exam_context_formatting
from .exam_format_rules import apply_exam_format_rules
from .header_extras import apply_header_extras
from .header_normalization import apply_header_normalization
from .inline_formatting import apply_inline_formats
from .models import ExamDocument
from .models.identity import ensure_block_ids
from .native_docx_objects import restore_native_objects
from .page_layout import adjusted_layout
from .page_target import spacing_scale_for_target
from .pagination import apply_pagination_guards
from .paragraph_formatting import apply_paragraph_formats
from .renderers import DocxRenderer
from .run_formatting import protect_inline_properties
from .segmentation_formatting import apply_segmentation_formatting
from .semantic_formatting import apply_semantic_formatting
from .source_decorations import restore_source_decorations
from .style_registry import StyleRegistry
from .validators import check_required_fonts
from .validators import validate_exam


def _render_documents(
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
        append_answer_to_docx(docx_path, raw_exam)
        restore_source_decorations(docx_path, raw_exam)

    apply_answer_overrides(docx_path, raw_exam)
    apply_header_normalization(docx_path, raw_exam)

    return (docx_path if export_docx else None), None, "docx-only"


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
    """Build DOCX with target-page spacing, page overrides and run protection."""

    adjusted_exam = deepcopy(raw_exam)
    _adjust_inline_spacing(adjusted_exam, spacing_scale_for_target(adjusted_exam))
    output = Path(output_dir)
    work = Path(temporary_dir) if temporary_dir else output
    work.mkdir(parents=True, exist_ok=True)
    target_layout = work / "layout-targeted.yaml"
    target_layout.write_text(
        _dump_yaml(adjusted_layout(load_layout(layout_path), adjusted_exam)),
        encoding="utf-8",
    )
    try:
        docx_path, _unused_pdf, _unused_engine = _render_documents(
            adjusted_exam,
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
    if docx_path is not None:
        protect_inline_properties(docx_path)
    return docx_path, None, "docx-only"


def _run_exam_passes(docx_path: Path, raw_exam: dict[str, Any]) -> None:
    """Apply deterministic DOCX passes in one documented order."""

    apply_header_extras(docx_path, raw_exam)
    apply_pagination_guards(docx_path)
    restore_native_objects(docx_path, raw_exam)
    apply_semantic_formatting(docx_path)
    apply_exam_format_rules(docx_path)
    apply_paragraph_formats(docx_path, raw_exam)
    apply_inline_formats(docx_path, raw_exam)
    enable_chinese_typography(docx_path)
    apply_exam_context_formatting(docx_path, raw_exam)
    apply_segmentation_formatting(docx_path)
    restore_source_decorations(docx_path, raw_exam)


def _adjust_inline_spacing(raw_exam: dict[str, Any], scale: float) -> None:
    """Apply a bounded rhythm change to explicit paragraph overrides."""

    for block in raw_exam.get("blocks", []):
        specs: list[dict[str, Any]] = []
        if block.get("type") == "question":
            question = block.get("question", {})
            if isinstance(question.get("format"), dict):
                specs.append(question["format"])
            specs.extend(
                item
                for item in question.get("paragraph_formats", [])
                if isinstance(item, dict)
            )
        specs.extend(
            item
            for item in block.get("paragraph_formats", [])
            if isinstance(item, dict)
        )
        if isinstance(block.get("format"), dict):
            specs.append(block["format"])
        for spec in specs:
            if "line_spacing" in spec:
                spec["line_spacing"] = max(
                    0.95,
                    min(1.8, float(spec["line_spacing"]) * scale),
                )
            for key in ("space_before_pt", "space_after_pt"):
                if key in spec:
                    spec[key] = max(0.0, float(spec[key]) * scale)


def _dump_yaml(data: dict[str, Any]) -> str:
    """Serialize the small nested mapping accepted by the bundled YAML reader."""

    lines: list[str] = []

    def visit(mapping: dict[str, Any], indent: int) -> None:
        for key, value in mapping.items():
            prefix = " " * indent + f"{key}:"
            if isinstance(value, dict):
                lines.append(prefix)
                visit(value, indent + 2)
            else:
                lines.append(f"{prefix} {_yaml_scalar(value)}")

    visit(data, 0)
    return "\n".join(lines) + "\n"


def _yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    text = str(value)
    if any(character in text for character in (":", "#", "\n")):
        return '"' + text.replace('"', '\\"') + '"'
    return text


__all__ = ["build_documents"]
