"""Production v0.8.6 with Guangzhou-style answer documents."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import tkinter as tk
from typing import Any

import desktop_app as base
import desktop_app_v070 as v070
import desktop_app_v085_release as v085_module
from app.answer_overrides_v1 import apply_answer_overrides_v1
from app.answer_typesetting_v1 import (
    answer_blocks,
    append_answer_to_docx,
    exam_blocks,
    render_answer_docx,
)
from app.contextual_formatting_v9 import apply_contextual_formatting_v9
from app.flexible_importers_v11 import import_exam
from app.pdf_exporter_silent import SilentPdfExporter
from app.preview_locator_v2 import build_preview_locators
from app.source_decorations_v1 import restore_source_decorations_v1
from desktop_app_v086_final import ProductionDesktopApp as ProductionDesktopAppV086


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.6"
v070.import_exam = import_exam
_build_exam_base = v085_module.build_documents_v85


def build_documents_v86_answer(
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
    """Build an exam, an answer, or a combined exam-and-answer DOCX."""

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = temporary_dir or output_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    docx_dir = output_dir if export_docx else work_dir
    docx_path = docx_dir / f"{basename}.docx"

    if raw_exam.get("document_kind") == "answer":
        render_answer_docx(raw_exam, docx_path)
    else:
        exam_raw = deepcopy(raw_exam)
        exam_raw["blocks"] = exam_blocks(raw_exam)
        built, _, _ = _build_exam_base(
            exam_raw,
            layout_path,
            docx_dir,
            basename,
            template_path=template_path,
            export_docx=True,
            export_pdf=False,
            temporary_dir=work_dir,
        )
        if built is None:
            raise RuntimeError("Word 文件未生成。")
        docx_path = built
        apply_contextual_formatting_v9(docx_path, exam_raw)
        append_answer_to_docx(docx_path, raw_exam)
        restore_source_decorations_v1(docx_path, raw_exam)

    apply_answer_overrides_v1(docx_path, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = SilentPdfExporter().export(
            docx_path,
            output_dir / f"{basename}.pdf",
        )
    return (docx_path if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v86_answer
v085_module.build_documents_v85 = build_documents_v86_answer
v085_module.build_preview_locators = build_preview_locators


class ProductionDesktopApp(ProductionDesktopAppV086):
    """Show answer structures in the same editable, live-preview workbench."""

    def _populate_tree(self) -> None:
        super()._populate_tree()
        roots = self.tree.get_children("")
        if not roots:
            return
        root = roots[0]
        if self.raw_exam.get("document_kind") == "answer":
            self.tree.item(root, text="答案信息")
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            kind = str(block.get("type", ""))
            if not kind.startswith("answer_"):
                continue
            label, category = self._answer_tree_label(block)
            if not self.tree.exists(f"block-{index}"):
                self.tree.insert(
                    root,
                    tk.END,
                    iid=f"block-{index}",
                    text=label,
                    values=(category, ""),
                )
        self.tree.item(root, open=True)

    @staticmethod
    def _answer_tree_label(block: dict[str, Any]) -> tuple[str, str]:
        kind = block.get("type")
        if kind == "answer_section":
            return str(block.get("text", "答案章节")), "答案章节"
        if kind == "answer_subsection":
            return (
                f"{block.get('name', '')}{block.get('meta', '')}",
                "答案模块",
            )
        if kind == "answer_question":
            return str(block.get("header", "答案题目")), "答案"
        if kind == "answer_table":
            return "作文等级评分表", "原表格"
        paragraphs = block.get("paragraphs", [])
        text = str(paragraphs[0].get("text", "")) if paragraphs else "答案说明"
        return text[:24], "答案说明"

    def _select_first_question(self) -> None:
        super()._select_first_question()
        if self.tree.selection():
            return
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            if str(block.get("type", "")).startswith("answer_"):
                iid = f"block-{index}"
                if self.tree.exists(iid):
                    self.tree.selection_set(iid)
                    self.tree.focus(iid)
                    self._on_tree_select(None)
                    return

    def _block_label(self, block: dict[str, Any]) -> str:
        if str(block.get("type", "")).startswith("answer_"):
            return self._answer_tree_label(block)[0]
        return super()._block_label(block)

    def _block_edit_text(self, block: dict[str, Any]) -> str:
        kind = block.get("type")
        if kind == "answer_section":
            return str(block.get("text", ""))
        if kind == "answer_subsection":
            return f"{block.get('name', '')}{block.get('meta', '')}"
        if kind in {"answer_question", "answer_text"}:
            values: list[str] = []
            if block.get("header"):
                values.append(str(block["header"]))
            values.extend(
                str(entry.get("text", ""))
                for entry in block.get("paragraphs", [])
            )
            return "\n".join(values)
        if kind == "answer_table":
            return "原答案中的作文等级评分表将保持表格结构。"
        return super()._block_edit_text(block)

    def _commit_nonquestion_content(
        self,
        block: dict[str, Any],
        content: str,
    ) -> None:
        kind = block.get("type")
        if kind == "answer_section":
            block["text"] = content
            return
        if kind == "answer_subsection":
            block["name"] = content
            block["meta"] = ""
            return
        if kind in {"answer_question", "answer_text"}:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if kind == "answer_question" and lines:
                block["header"] = lines.pop(0)
            old = list(block.get("paragraphs", []))
            paragraphs: list[dict[str, Any]] = []
            for index, text in enumerate(lines):
                role = (
                    str(old[index].get("role", "subjective_answer"))
                    if index < len(old)
                    else "subjective_answer"
                )
                paragraphs.append({"text": text, "role": role, "runs": []})
            block["paragraphs"] = paragraphs
            return
        if kind == "answer_table":
            return
        super()._commit_nonquestion_content(block, content)

    def _load_nonquestion_fields(self, block: dict[str, Any]) -> None:
        super()._load_nonquestion_fields(block)
        kind = block.get("type")
        defaults = {
            "answer_section": ("黑体", "12", "0", "左对齐"),
            "answer_subsection": ("宋体", "10.5", "2", "左对齐"),
            "answer_question": ("宋体", "10.5", "0", "左对齐"),
            "answer_text": ("楷体", "10.5", "2", "左对齐"),
            "answer_table": ("宋体", "10.5", "0", "居中"),
        }
        if kind in defaults:
            font, size, indent, alignment = defaults[kind]
            spec = block.get("format", {})
            self.font_var.set(str(spec.get("font", font)))
            self.size_var.set(str(spec.get("size_pt", size)))
            self.indent_var.set(
                str(spec.get("first_line_indent_chars", indent))
            )
            self.alignment_var.set(str(spec.get("alignment", alignment)))
            self.line_spacing_var.set(str(spec.get("line_spacing", 1.25)))

    def _tag_block_lines(self, block: dict[str, Any]) -> None:
        kind = block.get("type")
        if not str(kind).startswith("answer_"):
            super()._tag_block_lines(block)
            return
        if kind == "answer_section":
            self._add_semantic_tag(
                "semantic_answer_section",
                "1.0",
                "1.end",
                "SimHei",
                12,
                False,
            )
            return
        if kind == "answer_subsection":
            self._add_semantic_tag(
                "semantic_answer_subsection",
                "1.0",
                "1.end",
                "SimSun",
                10.5,
                True,
            )
            return
        if kind == "answer_table":
            self._add_semantic_tag(
                "semantic_answer_table",
                "1.0",
                "1.end",
                "SimSun",
                10.5,
                False,
                justify="center",
            )
            return
        line = 1
        if block.get("header"):
            self._add_semantic_tag(
                f"semantic_answer_header_{line}",
                f"{line}.0",
                f"{line}.end",
                "SimSun",
                10.5,
                False,
            )
            line += 1
        for entry in block.get("paragraphs", []):
            role = str(entry.get("role", "subjective_answer"))
            font, bold = {
                "objective_answer": ("SimSun", False),
                "subjective_answer": ("KaiTi", False),
                "mixed_answer": ("KaiTi", False),
                "answer_label": ("SimSun", False),
                "example_label": ("SimHei", False),
                "scoring_label": ("SimHei", False),
                "scoring_rule": ("SimSun", False),
                "translation_label": ("SimHei", False),
                "translation_body": ("SimSun", False),
                "composition": ("SimSun", False),
            }.get(role, ("KaiTi", False))
            self._add_semantic_tag(
                f"semantic_answer_{line}",
                f"{line}.0",
                f"{line}.end",
                font,
                10.5,
                bold,
            )
            line += 1

    def _tag_block_lines_material_roles(self, block: dict[str, Any]) -> None:
        """Reserved for future answer role extensions."""

    def _tag_block_lines(self, block: dict[str, Any]) -> None:  # type: ignore[no-redef]
        kind = block.get("type")
        if str(kind).startswith("answer_"):
            self._tag_answer_block_lines(block)
            return
        super()._tag_block_lines(block)
        if block.get("type") != "material":
            return
        line = 1 + int(bool(block.get("title"))) + int(bool(block.get("author")))
        roles = [str(value) for value in block.get("paragraph_roles", [])]
        for index, _text in enumerate(block.get("paragraphs", [])):
            role = roles[index] if index < len(roles) else "body"
            if role == "author":
                self._add_semantic_tag(
                    f"semantic_inline_author_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "FangSong",
                    10.5,
                    False,
                    justify="center",
                )
            line += 1

    def _tag_answer_block_lines(self, block: dict[str, Any]) -> None:
        kind = block.get("type")
        if kind == "answer_section":
            self._add_semantic_tag(
                "semantic_answer_section", "1.0", "1.end", "SimHei", 12, False
            )
            return
        if kind == "answer_subsection":
            self._add_semantic_tag(
                "semantic_answer_subsection",
                "1.0",
                "1.end",
                "SimSun",
                10.5,
                True,
            )
            return
        if kind == "answer_table":
            self._add_semantic_tag(
                "semantic_answer_table",
                "1.0",
                "1.end",
                "SimSun",
                10.5,
                False,
                justify="center",
            )
            return
        line = 1
        if block.get("header"):
            self._add_semantic_tag(
                f"semantic_answer_header_{line}",
                f"{line}.0",
                f"{line}.end",
                "SimSun",
                10.5,
                False,
            )
            line += 1
        for entry in block.get("paragraphs", []):
            role = str(entry.get("role", "subjective_answer"))
            font = {
                "objective_answer": "SimSun",
                "subjective_answer": "KaiTi",
                "mixed_answer": "KaiTi",
                "answer_label": "SimSun",
                "example_label": "SimHei",
                "scoring_label": "SimHei",
                "scoring_rule": "SimSun",
                "translation_label": "SimHei",
                "translation_body": "SimSun",
                "composition": "SimSun",
            }.get(role, "KaiTi")
            self._add_semantic_tag(
                f"semantic_answer_{line}",
                f"{line}.0",
                f"{line}.end",
                font,
                10.5,
                False,
            )
            line += 1


def preview_directory() -> Path:
    return Path(tempfile.mkdtemp(prefix="preview_answer_v086_"))


__all__ = ["ProductionDesktopApp", "build_documents_v86_answer"]
