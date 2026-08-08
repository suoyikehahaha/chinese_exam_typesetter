"""Production v0.8.5 with adaptive parsing and synchronized preview navigation."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

import desktop_app as base
import desktop_app_v070 as v070
from app.chinese_typography_v2 import enable_chinese_typography_v2
from app.config import load_layout
from app.contextual_formatting_v5 import apply_contextual_formatting_v5
from app.contextual_formatting_v6 import apply_contextual_formatting_v6
from app.exam_format_rules_v3 import apply_exam_format_rules_v3
from app.flexible_importers_v9 import import_exam
from app.header_extras_v1 import apply_header_extras_v1
from app.inline_formatting_v3 import apply_inline_formats_v3
from app.models import ExamDocument
from app.native_docx_objects import restore_native_objects
from app.pagination import apply_pagination_guards
from app.paragraph_formatting_v1 import apply_paragraph_formats_v1
from app.pdf_exporter_silent import SilentPdfExporter
from app.preview_locator_v1 import build_preview_locators
from app.renderers import DocxRenderer
from app.segmentation_formatting_v2 import apply_segmentation_formatting_v2
from app.semantic_formatting_v4 import apply_semantic_formatting_v4
from app.source_decorations_v1 import restore_source_decorations_v1
from app.validators import check_required_fonts
from app.validators.exam_validator_v2 import validate_exam
from desktop_app_v084_fixed import ProductionDesktopApp as ProductionDesktopAppV084


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.5"
v070.import_exam = import_exam


def build_documents_v85(
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
    """Render the editable Word file and optional internal PDF preview."""

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
    apply_contextual_formatting_v6(docx_work, raw_exam)
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


base.build_documents = build_documents_v85


FONT_TO_TK = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
    "SimSun": "SimSun",
    "SimHei": "SimHei",
    "KaiTi": "KaiTi",
    "FangSong": "FangSong",
}
FONT_TO_UI = {
    "SimSun": "宋体",
    "SimHei": "黑体",
    "KaiTi": "楷体",
    "FangSong": "仿宋",
}


class ProductionDesktopApp(ProductionDesktopAppV084):
    """Keep structure, editor controls and page preview visibly synchronized."""

    def __init__(self) -> None:
        self._preview_block_locators: dict[int, tuple[int, float]] = {}
        self._pending_preview_block: int | None = None
        self._editor_tag_specs: dict[str, tuple[str, float, bool]] = {}
        super().__init__()
        self.stem_text.bind("<ButtonRelease-1>", self._cursor_style_event, add="+")

    def _setup_styles(self) -> None:
        super()._setup_styles()
        style = ttk.Style(self)
        style.configure(
            "Prominent.Status.TLabel",
            background="#E6F2FF",
            foreground="#004578",
            font=("Segoe UI", 10, "bold"),
            padding=(8, 4),
        )
        style.configure(
            "Prominent.Attribution.TLabel",
            background="#E6F2FF",
            foreground="#3B5266",
            font=("Segoe UI", 9),
            padding=(6, 4),
        )
        style.configure("Prominent.Status.TFrame", background="#E6F2FF")
        style.configure(
            "EditorHint.TLabel",
            background="#FFF4CE",
            foreground="#6A4500",
            font=("Segoe UI", 9, "bold"),
            padding=(7, 4),
        )

    def _build_ui(self) -> None:
        super()._build_ui()
        for widget in self._walk_widgets(self):
            if not isinstance(widget, ttk.Label):
                continue
            text = str(widget.cget("text"))
            textvariable = str(widget.cget("textvariable"))
            if text.startswith("本人制作｜"):
                widget.configure(
                    text="个人制作｜公众号：蓑衣微言｜拒绝商用",
                    style="Prominent.Attribution.TLabel",
                )
                if isinstance(widget.master, ttk.Frame):
                    widget.master.configure(style="Prominent.Status.TFrame")
            elif textvariable == str(self.status_var):
                widget.configure(style="Prominent.Status.TLabel")
                if isinstance(widget.master, ttk.Frame):
                    widget.master.configure(style="Prominent.Status.TFrame")
            elif hasattr(self, "selection_hint_var") and textvariable == str(
                self.selection_hint_var
            ):
                widget.configure(style="EditorHint.TLabel")

    @staticmethod
    def _walk_widgets(widget: tk.Misc) -> list[tk.Misc]:
        result: list[tk.Misc] = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(ProductionDesktopApp._walk_widgets(child))
        return result

    def _release_startup_preview(self) -> None:
        self._startup_preview_deferred = False
        if self._startup_preview_pending:
            self._startup_preview_pending = False
            self.request_preview()

    def request_preview(self) -> None:
        if self._startup_preview_deferred:
            self._startup_preview_pending = True
            return
        if self.busy:
            self.status_var.set("当前预览仍在生成，完成后可继续修改。")
            return
        self.apply_current_question(silent=True)
        selection = self.tree.selection()
        if selection and selection[0].startswith("block-"):
            self._pending_preview_block = int(selection[0].split("-", 1)[1])
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在排版并建立结构定位，请稍候……")
        raw = deepcopy(self.raw_exam)
        template = self.template_path
        iteration_dir = Path(tempfile.mkdtemp(prefix="preview_", dir=self.temp_dir))

        def worker() -> None:
            try:
                _, pdf_path, engine = build_documents_v85(
                    raw,
                    self.layout_path,
                    iteration_dir,
                    "preview",
                    template_path=template,
                    export_docx=True,
                    export_pdf=True,
                    temporary_dir=iteration_dir,
                )
                if pdf_path is None:
                    raise RuntimeError("内部预览未生成。")
                pages = base.rasterize_pdf(pdf_path, iteration_dir / "pages")
                self._preview_block_locators = build_preview_locators(pdf_path, raw)
                self.messages.put(("preview", (pages, engine)))
            except Exception as exc:
                self.messages.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_tree_select(self, event: object) -> None:
        super()._on_tree_select(event)
        selection = self.tree.selection()
        if not selection or not selection[0].startswith("block-"):
            return
        index = int(selection[0].split("-", 1)[1])
        self._pending_preview_block = index
        self._apply_editor_visual_styles()
        self._jump_to_block(index)

    def _jump_to_block(self, block_index: int) -> None:
        locator = self._preview_block_locators.get(block_index)
        if locator is None or not self.preview_pages:
            return
        page_index, vertical = locator
        self.preview_page_index = min(page_index, len(self.preview_pages) - 1)
        self._pending_preview_block = None
        super()._show_current_page()
        self.after(20, lambda: self.canvas.yview_moveto(vertical))
        self.status_var.set(
            f"已定位到第 {self.preview_page_index + 1} 页的对应内容。"
        )

    def _show_current_page(self) -> None:
        pending = self._pending_preview_block
        if pending is not None and pending in self._preview_block_locators:
            self._jump_to_block(pending)
            return
        super()._show_current_page()

    def previous_page(self) -> None:
        self._pending_preview_block = None
        super().previous_page()

    def next_page(self) -> None:
        self._pending_preview_block = None
        super().next_page()

    def _load_question_fields(self, question: dict[str, Any]) -> None:
        super()._load_question_fields(question)
        self.after_idle(self._apply_editor_visual_styles)

    def _load_nonquestion_fields(self, block: dict[str, Any]) -> None:
        super()._load_nonquestion_fields(block)
        self.after_idle(self._apply_editor_visual_styles)

    def _format_changed(self) -> None:
        super()._format_changed()
        if not self.loading_fields and self.selected_block_index is not None:
            self.after_idle(self._apply_editor_visual_styles)

    def _apply_editor_visual_styles(self) -> None:
        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        for tag in list(self.stem_text.tag_names()):
            if tag.startswith("semantic_"):
                self.stem_text.tag_delete(tag)
        self._editor_tag_specs = {}
        self.stem_text.configure(font=("SimSun", 11))
        if block.get("type") == "question":
            self._tag_question_lines(block["question"])
        else:
            self._tag_block_lines(block)
        for tag in self.stem_text.tag_names():
            if tag.startswith("inlinefmt_"):
                self.stem_text.tag_raise(tag)
        self.stem_text.tag_raise(tk.SEL)

    def _tag_question_lines(self, question: dict[str, Any]) -> None:
        for line_index, mapping in enumerate(self.current_line_map, start=1):
            target = str(mapping.get("target", "stem"))
            if target in {
                "stem",
                "option",
                "subquestion",
                "composition_prompt",
                "composition_requirements",
            }:
                font, size, bold = "SimSun", 10.5, False
            elif target == "segmentation":
                font, size, bold = "KaiTi", 10.5, False
            else:
                font, size, bold = "KaiTi", 10.5, False
            self._add_semantic_tag(
                f"semantic_line_{line_index}",
                f"{line_index}.0",
                f"{line_index}.end",
                font,
                size,
                bold,
            )
            if target == "segmentation":
                text = self.stem_text.get(f"{line_index}.0", f"{line_index}.end")
                for character_index, character in enumerate(text):
                    if character in "ABCDEFGH":
                        self._add_semantic_tag(
                            f"semantic_marker_{line_index}_{character_index}",
                            f"{line_index}.{character_index}",
                            f"{line_index}.{character_index + 1}",
                            "SimSun",
                            10.5,
                            False,
                        )
            if target == "embedded":
                target_index = int(mapping.get("target_index", 0))
                segments = question.get("embedded_segments", [])
                if target_index < len(segments):
                    cursor = 0
                    for segment_index, segment in enumerate(segments[target_index]):
                        text = str(segment.get("text", ""))
                        if segment.get("role") == "label":
                            self._add_semantic_tag(
                                f"semantic_label_{line_index}_{segment_index}",
                                f"{line_index}.{cursor}",
                                f"{line_index}.{cursor + len(text)}",
                                "SimSun",
                                10.5,
                                False,
                            )
                        cursor += len(text)

    def _tag_block_lines(self, block: dict[str, Any]) -> None:
        block_type = str(block.get("type", ""))
        if block_type == "section_title":
            self._add_semantic_tag(
                "semantic_section", "1.0", "1.end", "SimHei", 12, False
            )
            return
        if block_type == "subsection":
            text = self.stem_text.get("1.0", "1.end")
            marker = text.find("（本题共")
            if marker < 0:
                marker = text.find("(本题共")
            if marker < 0:
                marker = len(text)
            self._add_semantic_tag(
                "semantic_subsection_name",
                "1.0",
                f"1.{marker}",
                "SimSun",
                10.5,
                True,
            )
            self._add_semantic_tag(
                "semantic_subsection_meta",
                f"1.{marker}",
                "1.end",
                "SimSun",
                10.5,
                False,
            )
            return
        if block_type == "instruction":
            self._add_semantic_tag(
                "semantic_instruction", "1.0", "1.end", "SimSun", 10.5, False
            )
            return

        line = 1
        if block.get("title"):
            self._add_semantic_tag(
                f"semantic_title_{line}",
                f"{line}.0",
                f"{line}.end",
                "SimHei",
                10.5,
                False,
                justify="center",
            )
            line += 1
        if block.get("author"):
            self._add_semantic_tag(
                f"semantic_author_{line}",
                f"{line}.0",
                f"{line}.end",
                "FangSong",
                10.5,
                False,
                justify="center",
            )
            line += 1
        body_justify = "center" if block_type == "poetry" else "left"
        for _paragraph in block.get("paragraphs", []):
            self._add_semantic_tag(
                f"semantic_body_{line}",
                f"{line}.0",
                f"{line}.end",
                "KaiTi",
                10.5,
                False,
                justify=body_justify,
            )
            line += 1
        if block.get("note"):
            self._add_semantic_tag(
                f"semantic_note_{line}",
                f"{line}.0",
                f"{line}.end",
                "FangSong",
                9,
                False,
            )
            line += 1
        if block.get("source"):
            self._add_semantic_tag(
                f"semantic_source_{line}",
                f"{line}.0",
                f"{line}.end",
                "FangSong",
                10.5,
                False,
                justify="right",
            )

    def _add_semantic_tag(
        self,
        name: str,
        start: str,
        end: str,
        font: str,
        size: float,
        bold: bool,
        *,
        justify: str = "left",
    ) -> None:
        self._editor_tag_specs[name] = (font, size, bold)
        self.stem_text.tag_configure(
            name,
            font=(font, max(7, int(round(size))), "bold" if bold else "normal"),
            justify=justify,
        )
        self.stem_text.tag_add(name, start, end)

    def _cursor_style_event(self, event: tk.Event) -> None:
        self.after(
            25,
            lambda: self._sync_controls_from_cursor(
                self.stem_text.index(f"@{event.x},{event.y}")
            ),
        )

    def _sync_controls_from_cursor(self, index: str) -> None:
        if self.stem_text.tag_ranges(tk.SEL):
            return
        semantic_tags = [
            tag
            for tag in self.stem_text.tag_names(index)
            if tag in self._editor_tag_specs
        ]
        if not semantic_tags:
            return
        font, size, bold = self._editor_tag_specs[semantic_tags[-1]]
        self.loading_fields = True
        try:
            self.font_var.set(FONT_TO_UI.get(font, font))
            self.size_var.set(str(size))
            self.bold_var.set(bold)
        finally:
            self.loading_fields = False


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
    ProductionDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
