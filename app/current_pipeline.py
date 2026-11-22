"""Build the final editable DOCX from the current exam or reference answers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZipFile

from .answer_overrides import apply_answer_overrides
from .answer_typesetting import append_answer_to_docx, exam_blocks, compose_answer
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
from .pagination import apply_pagination_guards
from .paragraph_formatting import apply_paragraph_formats
from .renderers import DocxRenderer
from .run_formatting import protect_inline_properties
from .resource_anchors import project_source_resources
from .reference_profile import apply_reference_profile, prepare_profile_exam
from .segmentation_formatting import apply_segmentation_formatting
from .semantic_formatting import apply_semantic_formatting
from .source_document import source_read_session
from .source_decorations import restore_source_decorations
from .style_registry import StyleRegistry
from .validators import check_required_fonts
from .validators import validate_exam
from .word_document import WordTarget


class ExportDestinationError(RuntimeError):
    """The completed DOCX could not replace the selected destination."""

    def __init__(self, target: Path) -> None:
        self.target = target
        super().__init__(
            f"无法写入“{target}”。该文件可能正在 Word 或 WPS 中打开，"
            "也可能没有写入权限。请关闭占用该文件的程序后重试，"
            "或取消后换一个文件名保存。"
        )


def _render_documents(
    raw_exam: dict[str, Any],
    layout: dict,
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
        document = compose_answer(raw_exam, layout)
    else:
        exam_raw = {**raw_exam, "blocks": exam_blocks(raw_exam)}
        exam_document = ExamDocument.from_dict(exam_raw)
        document = DocxRenderer(layout, template_path).compose(exam_document, exam_raw["blocks"])
        _run_exam_passes(document, exam_raw)
        append_answer_to_docx(document, raw_exam)

    restore_native_objects(document, raw_exam)
    restore_source_decorations(document, raw_exam)
    apply_answer_overrides(document, raw_exam)
    apply_header_normalization(document, raw_exam)
    apply_reference_profile(document, raw_exam)
    if raw_exam.get("document_kind") != "answer":
        apply_paragraph_formats(document, raw_exam)
        apply_inline_formats(document, raw_exam)

    protect_inline_properties(document)
    document.save(docx_path)
    return (docx_path if export_docx else None), None, "docx-only"


@source_read_session()
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
    """Build DOCX with natural pagination, page overrides and run protection."""

    output = Path(output_dir)
    work = Path(temporary_dir) if temporary_dir else output
    work.mkdir(parents=True, exist_ok=True)
    target = output / f"{basename}.docx"
    try:
        output.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ExportDestinationError(target) from exc
    # Keep the completed staging file on the destination volume so the final
    # replacement remains atomic even when the application and export folder
    # are on different drives.
    staging_parent = output if export_docx else work
    try:
        staging_context = TemporaryDirectory(prefix="exam-export-", dir=staging_parent)
    except PermissionError as exc:
        raise ExportDestinationError(target) from exc
    with staging_context as staging_folder:
        task_work = Path(staging_folder)
        adjusted_exam = prepare_profile_exam(raw_exam)
        project_source_resources(adjusted_exam)
        layout = adjusted_layout(load_layout(layout_path), adjusted_exam)
        docx_path, _, _ = _render_documents(
            adjusted_exam, layout, task_work, basename, template_path=template_path,
            export_docx=export_docx, temporary_dir=task_work,
        )
        if docx_path is None:
            return None, None, "docx-only"
        with ZipFile(docx_path) as package:
            if package.testzip() is not None or "word/document.xml" not in package.namelist():
                raise ValueError("生成的 Word 文件完整性检查未通过，已保留原有导出文件。")
        if not export_docx:
            return None, None, "docx-only"
        try:
            docx_path.replace(target)
        except PermissionError as exc:
            raise ExportDestinationError(target) from exc
        return target, None, "docx-only"


def _run_exam_passes(docx_path: WordTarget, raw_exam: dict[str, Any]) -> None:
    """Apply deterministic DOCX passes in one documented order."""

    apply_header_extras(docx_path, raw_exam)
    apply_pagination_guards(docx_path)
    restore_native_objects(docx_path, raw_exam, defer_missing=True)
    apply_semantic_formatting(docx_path)
    apply_exam_format_rules(docx_path)
    enable_chinese_typography(docx_path)
    apply_exam_context_formatting(docx_path, raw_exam)
    apply_segmentation_formatting(docx_path)


__all__ = ["ExportDestinationError", "build_documents"]
