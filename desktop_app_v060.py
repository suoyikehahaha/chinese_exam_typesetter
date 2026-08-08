"""工作台 v0.6.0，字符级编辑、历史记录和原生对象保留。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

import desktop_app as base
from app.block_overrides import apply_block_overrides
from app.chinese_typography import enable_chinese_typography
from app.config import load_layout
from app.exporters import PdfExporter
from app.flexible_importers_v3 import import_exam, save_exam
from app.inline_formatting_v2 import apply_inline_formats_v2
from app.models import ExamDocument
from app.native_docx_objects import restore_native_objects
from app.pagination import apply_pagination_guards
from app.question_overrides import apply_question_overrides
from app.renderers import DocxRenderer
from app.semantic_formatting_v2 import apply_semantic_formatting_v2
from app.validators import check_required_fonts, validate_exam
from desktop_app_v050_final import FinalDesktopApp


base.VERSION = "0.6.0"


def build_documents_v6(
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
    """生成带原生对象、中文排版和字符级格式的文件。"""

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
    apply_pagination_guards(docx_work)
    apply_block_overrides(docx_work, raw_exam)
    apply_question_overrides(docx_work, raw_exam)
    restore_native_objects(docx_work, raw_exam)
    apply_semantic_formatting_v2(docx_work)
    apply_inline_formats_v2(docx_work, raw_exam)
    enable_chinese_typography(docx_work)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = PdfExporter().export(
            docx_work,
            output_dir / f"{basename}.pdf",
        )
    return (docx_work if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v6


class RichDesktopApp(FinalDesktopApp):
    """左侧字符级编辑，右侧整卷预览。"""

    def __init__(self) -> None:
        self.char_font_var = tk.StringVar if False else None
        self.char_size_var = tk.StringVar if False else None
        self.undo_stack: list[dict[str, Any]] = []
        self.redo_stack: list[dict[str, Any]] = []
        self.history_transaction_open = False
        self.current_line_map: list[dict[str, Any]] = []
        self.inline_tag_specs: dict[str, dict[str, Any]] = {}
        super().__init__()
        self.char_font_var = tk.StringVar(value="宋体")
        self.char_size_var = tk.StringVar(value="10.5")
        self._build_local_format_controls()
        self.stem_text.configure(undo=True, autoseparators=True, maxundo=-1)
        self.stem_text.bind("<KeyPress>", self._before_text_edit, add="+")
        self.bind_all("<Control-z>", self.undo_action)
        self.bind_all("<Control-y>", self.redo_action)

    def _build_local_format_controls(self) -> None:
        frame = ttk.LabelFrame(
            self.detail_frame,
            text="选中文字格式",
            padding=8,
        )
        frame.grid(row=23, column=0, columnspan=4, sticky=tk.EW, pady=(12, 2))
        ttk.Label(frame, text="字体").grid(row=0, column=0, padx=(0, 5))
        ttk.Combobox(
            frame,
            textvariable=self.char_font_var,
            values=base.FONT_CHOICES,
            state="readonly",
            width=9,
        ).grid(row=0, column=1)
        ttk.Label(frame, text="字号").grid(row=0, column=2, padx=(12, 5))
        ttk.Entry(frame, textvariable=self.char_size_var, width=7).grid(row=0, column=3)
        ttk.Button(
            frame,
            text="应用到选中文字",
            style="Primary.TButton",
            command=self.apply_selected_text_format,
        ).grid(row=0, column=4, padx=(12, 4))
        ttk.Button(
            frame,
            text="清除局部格式",
            command=self.clear_selected_text_format,
        ).grid(row=0, column=5, padx=4)
        ttk.Button(frame, text="撤回", command=self.undo_action).grid(
            row=1, column=4, padx=(12, 4), pady=(7, 0)
        )
        ttk.Button(frame, text="前进", command=self.redo_action).grid(
            row=1, column=5, padx=4, pady=(7, 0)
        )
        ttk.Label(
            frame,
            text="先在上方内容框中选择文字，再应用字体和字号。",
            foreground="#666666",
        ).grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(7, 0))

    def _build_preview_panel(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(bar, text="整卷预览", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(bar, text="上一页", command=self.previous_page).pack(
            side=tk.LEFT, padx=(20, 4)
        )
        ttk.Button(bar, text="下一页", command=self.next_page).pack(side=tk.LEFT)
        ttk.Button(bar, text="缩小", command=lambda: self.change_zoom(-0.08)).pack(
            side=tk.LEFT, padx=(18, 4)
        )
        ttk.Button(bar, text="放大", command=lambda: self.change_zoom(0.08)).pack(
            side=tk.LEFT
        )
        ttk.Label(bar, textvariable=self.page_status_var).pack(side=tk.RIGHT)
        page_frame = ttk.Frame(parent)
        page_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(
            page_frame,
            background="#D9DDE3",
            highlightthickness=0,
        )
        y_scroll = ttk.Scrollbar(
            page_frame,
            orient=tk.VERTICAL,
            command=self.canvas.yview,
        )
        x_scroll = ttk.Scrollbar(
            page_frame,
            orient=tk.HORIZONTAL,
            command=self.canvas.xview,
        )
        self.canvas.configure(
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
        )
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)
        y_scroll.grid(row=0, column=1, sticky=tk.NS)
        x_scroll.grid(row=1, column=0, sticky=tk.EW)
        page_frame.columnconfigure(0, weight=1)
        page_frame.rowconfigure(0, weight=1)
        self.canvas.bind("<MouseWheel>", self._on_preview_wheel)
        self.canvas.bind("<Configure>", self._schedule_page_redraw)

    def _load_right_text(self, block: dict[str, Any]) -> None:
        return

    def _mirror_left_to_right(self) -> None:
        return

    def _load_question_fields(self, question: dict[str, Any]) -> None:
        super()._load_question_fields(question)
        lines, mapping = self._question_editor_lines(question)
        self.loading_fields = True
        try:
            self.stem_text.delete("1.0", tk.END)
            self.stem_text.insert("1.0", "\n".join(lines))
            self.current_line_map = mapping
            self._render_inline_tags(question.get("inline_formats", []))
        finally:
            self.loading_fields = False

    def _load_nonquestion_fields(self, block: dict[str, Any]) -> None:
        super()._load_nonquestion_fields(block)
        lines = self._block_edit_text(block).splitlines()
        self.current_line_map = [
            {"target": "block", "target_index": index}
            for index in range(len(lines))
        ]
        self._render_inline_tags(block.get("inline_formats", []))

    def _question_editor_lines(
        self,
        question: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        lines = [str(question.get("stem", ""))]
        mapping = [{"target": "stem", "target_index": 0}]
        for index, text in enumerate(question.get("options", [])):
            lines.append(str(text))
            mapping.append({"target": "option", "target_index": index})
        for index, segments in enumerate(question.get("embedded_segments", [])):
            lines.append("".join(str(item.get("text", "")) for item in segments))
            mapping.append({"target": "embedded", "target_index": index})
        if question.get("segmentation_text"):
            lines.append(str(question["segmentation_text"]))
            mapping.append({"target": "segmentation", "target_index": 0})
        for index, text in enumerate(question.get("subquestions", [])):
            lines.append(str(text))
            mapping.append({"target": "subquestion", "target_index": index})
        for key in (
            "composition_material",
            "composition_prompt",
            "composition_requirements",
        ):
            for index, text in enumerate(question.get(key, [])):
                lines.append(str(text))
                mapping.append({"target": key, "target_index": index})
        return lines, mapping

    def apply_current_question(self, *, silent: bool = False) -> bool:
        if self.loading_fields or self.selected_block_index is None:
            return False
        block = self.raw_exam["blocks"][self.selected_block_index]
        content = self.stem_text.get("1.0", tk.END).strip()
        try:
            spec = self._current_format_spec()
        except ValueError:
            if not silent:
                messagebox.showerror(base.APP_TITLE, "格式参数需要填写数字。")
            return False
        if block.get("type") == "question":
            question = block["question"]
            self._commit_question_lines(question, content.splitlines())
            question["kind"] = (
                "objective" if self.kind_var.get() == "客观题" else "subjective"
            )
            try:
                question["score"] = base._optional_float(self.score_var.get())
            except ValueError:
                if not silent:
                    messagebox.showerror(base.APP_TITLE, "分值需要填写数字。")
                return False
            question["option_layout"] = (
                "two_column"
                if self.option_layout_var.get() == "两行两列"
                else "vertical"
            )
            question["format"] = spec
            question["inline_formats"] = self._collect_inline_formats()
        else:
            self._commit_nonquestion_content(block, content)
            block["format"] = spec
            block["inline_formats"] = self._collect_inline_formats()
        if not silent:
            self.status_var.set("内容和格式已同步。")
        return True

    def _commit_question_lines(
        self,
        question: dict[str, Any],
        lines: list[str],
    ) -> None:
        for index, mapping in enumerate(self.current_line_map):
            if index >= len(lines):
                break
            value = lines[index]
            target = mapping["target"]
            target_index = int(mapping["target_index"])
            if target == "stem":
                question["stem"] = value
            elif target == "option":
                question["options"][target_index] = value
            elif target == "embedded":
                question["embedded_segments"][target_index] = [
                    {"text": value, "role": "body"}
                ]
            elif target == "segmentation":
                question["segmentation_text"] = value
            else:
                question[target][target_index] = value

    def _commit_nonquestion_content(
        self,
        block: dict[str, Any],
        content: str,
    ) -> None:
        lines = content.splitlines()
        mapping: list[tuple[str, int | None]] = []
        for key in ("title", "author"):
            if block.get(key):
                mapping.append((key, None))
        mapping.extend(("paragraphs", index) for index, _ in enumerate(block.get("paragraphs", [])))
        for key in ("note", "source"):
            if block.get(key):
                mapping.append((key, None))
        if len(lines) == len(mapping):
            for value, (key, index) in zip(lines, mapping):
                if index is None:
                    block[key] = value
                else:
                    block[key][index] = value
            return
        super()._commit_nonquestion_content(block, content)

    def apply_selected_text_format(self) -> None:
        try:
            start = self.stem_text.index(tk.SEL_FIRST)
            end = self.stem_text.index(tk.SEL_LAST)
            size = float(self.char_size_var.get())
        except tk.TclError:
            messagebox.showinfo(base.APP_TITLE, "请先在内容框中选择文字。")
            return
        except ValueError:
            messagebox.showerror(base.APP_TITLE, "局部字号需要填写数字。")
            return
        self._record_history()
        self._remove_inline_tags(start, end)
        name = f"inlinefmt_{len(self.inline_tag_specs) + 1}"
        spec = {"font": self.char_font_var.get(), "size_pt": size}
        self.inline_tag_specs[name] = spec
        self.stem_text.tag_configure(
            name,
            font=(self.char_font_var.get(), max(7, int(round(size)))),
        )
        self.stem_text.tag_add(name, start, end)
        self.selection_dirty = True
        self.apply_current_question(silent=True)
        self.history_transaction_open = False
        self.request_preview()

    def clear_selected_text_format(self) -> None:
        try:
            start = self.stem_text.index(tk.SEL_FIRST)
            end = self.stem_text.index(tk.SEL_LAST)
        except tk.TclError:
            messagebox.showinfo(base.APP_TITLE, "请先在内容框中选择文字。")
            return
        self._record_history()
        self._remove_inline_tags(start, end)
        self.selection_dirty = True
        self.apply_current_question(silent=True)
        self.history_transaction_open = False
        self.request_preview()

    def _remove_inline_tags(self, start: str, end: str) -> None:
        for name in list(self.inline_tag_specs):
            self.stem_text.tag_remove(name, start, end)

    def _collect_inline_formats(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name, spec in self.inline_tag_specs.items():
            ranges = self.stem_text.tag_ranges(name)
            for start, end in zip(ranges[0::2], ranges[1::2]):
                start_line, start_col = map(int, str(start).split("."))
                end_line, end_col = map(int, str(end).split("."))
                for line in range(start_line, end_line + 1):
                    if line - 1 >= len(self.current_line_map):
                        continue
                    line_text = self.stem_text.get(f"{line}.0", f"{line}.end")
                    left = start_col if line == start_line else 0
                    right = end_col if line == end_line else len(line_text)
                    if left >= right:
                        continue
                    entry = dict(self.current_line_map[line - 1])
                    entry.update(
                        {
                            "line": line - 1,
                            "start": left,
                            "end": right,
                            **spec,
                        }
                    )
                    result.append(entry)
        return result

    def _render_inline_tags(self, entries: list[dict[str, Any]]) -> None:
        for name in list(self.inline_tag_specs):
            self.stem_text.tag_delete(name)
        self.inline_tag_specs = {}
        for index, entry in enumerate(entries):
            line = int(entry.get("line", 0)) + 1
            name = f"inlinefmt_{index + 1}"
            self.inline_tag_specs[name] = {
                "font": str(entry.get("font", "宋体")),
                "size_pt": float(entry.get("size_pt", 10.5)),
            }
            self.stem_text.tag_configure(
                name,
                font=(
                    self.inline_tag_specs[name]["font"],
                    max(7, int(round(self.inline_tag_specs[name]["size_pt"]))),
                ),
            )
            self.stem_text.tag_add(
                name,
                f"{line}.{int(entry.get('start', 0))}",
                f"{line}.{int(entry.get('end', 0))}",
            )

    def _before_text_edit(self, event: Any) -> None:
        if event.keysym not in {
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
        }:
            self._record_history()

    def schedule_live_preview(self) -> None:
        if not self.loading_fields and self.selected_block_index is not None:
            self._record_history()
        super().schedule_live_preview()

    def _live_preview_now(self) -> None:
        super()._live_preview_now()
        self.history_transaction_open = False

    def _record_history(self) -> None:
        if self.loading_fields or self.history_transaction_open:
            return
        self.undo_stack.append(deepcopy(self.raw_exam))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.history_transaction_open = True

    def undo_action(self, _event: object | None = None) -> str:
        if not self.undo_stack:
            return "break"
        if self.history_transaction_open:
            self.apply_current_question(silent=True)
        self.redo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.undo_stack.pop()
        self.history_transaction_open = False
        self._restore_history_selection()
        self.request_preview()
        return "break"

    def redo_action(self, _event: object | None = None) -> str:
        if not self.redo_stack:
            return "break"
        self.undo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.redo_stack.pop()
        self.history_transaction_open = False
        self._restore_history_selection()
        self.request_preview()
        return "break"

    def _restore_history_selection(self) -> None:
        index = self.selected_block_index
        self._populate_tree()
        if index is not None and self.tree.exists(f"block-{index}"):
            self.tree.selection_set(f"block-{index}")
            self.tree.focus(f"block-{index}")
            self._on_tree_select(None)

    def import_new_exam(self) -> None:
        path = filedialog.askopenfilename(
            title="导入新试题",
            filetypes=[
                ("支持的试题", "*.json *.docx *.pdf *.txt *.md"),
                ("结构化 JSON", "*.json"),
                ("Word 文档", "*.docx"),
                ("PDF 文档", "*.pdf"),
                ("文本或 Markdown", "*.txt *.md"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.raw_exam = import_exam(path)
        except Exception as exc:
            messagebox.showerror(base.APP_TITLE, str(exc))
            return
        self.current_exam_path = Path(path)
        self.selected_block_index = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._populate_tree()
        self._load_global_fields()
        self.status_var.set(f"已导入：{Path(path).name}")
        self.after(100, self._select_first_question)
        self.request_preview()

    def save_project(self) -> None:
        self.apply_current_question(silent=True)
        path = filedialog.asksaveasfilename(
            title="保存结构化项目",
            defaultextension=".json",
            filetypes=[("结构化试题", "*.json")],
            initialfile="exam.json",
        )
        if path:
            save_exam(self.raw_exam, path)
            self.current_exam_path = Path(path)
            self.status_var.set(f"项目已保存：{path}")


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
    RichDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
