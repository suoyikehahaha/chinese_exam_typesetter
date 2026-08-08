"""Windows workbench v0.8.0 with Word-like formatting controls."""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Any

import desktop_app as base
from app.chinese_typography_v2 import enable_chinese_typography_v2
from app.config import load_layout
from app.exam_format_rules_v3 import apply_exam_format_rules_v3
from app.flexible_importers_v5 import import_exam
from app.inline_formatting_v3 import apply_inline_formats_v3
from app.models import ExamDocument
from app.native_docx_objects import restore_native_objects
from app.pagination import apply_pagination_guards
from app.paragraph_formatting_v1 import apply_paragraph_formats_v1
from app.pdf_exporter_silent import SilentPdfExporter
from app.renderers import DocxRenderer
from app.semantic_formatting_v4 import apply_semantic_formatting_v4
from app.validators import check_required_fonts, validate_exam
from desktop_app_v070 import FinalDesktopAppV7
from desktop_app_v070_final import ReleaseDesktopApp


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.0"


def build_documents_v8(
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
    """Render a Word document and an optional silent preview PDF."""

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
    restore_native_objects(docx_work, raw_exam)
    apply_semantic_formatting_v4(docx_work)
    apply_exam_format_rules_v3(docx_work)
    apply_paragraph_formats_v1(docx_work, raw_exam)
    apply_inline_formats_v3(docx_work, raw_exam)
    enable_chinese_typography_v2(docx_work)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = SilentPdfExporter().export(
            docx_work,
            output_dir / f"{basename}.pdf",
        )
    return (docx_work if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v8


class WordLikeDesktopApp(ReleaseDesktopApp):
    """Compact editor with selection-aware character and paragraph settings."""

    def __init__(self) -> None:
        self.current_paragraph_formats: list[dict[str, Any]] = []
        self.format_after_id: str | None = None
        super().__init__()
        self.stem_text.configure(exportselection=False)
        self.stem_text.bind(
            "<ButtonRelease-1>",
            lambda _event: self.after(20, self._selection_status),
            add="+",
        )

    def _create_variables(self) -> None:
        super()._create_variables()
        self.bold_var = tk.BooleanVar(value=False)
        self.left_indent_var = tk.StringVar(value="0")
        self.right_indent_var = tk.StringVar(value="0")
        self.special_indent_var = tk.StringVar(value="无")
        self.special_indent_amount_var = tk.StringVar(value="0")

    def _build_local_format_controls(self) -> None:
        return

    def _build_combined_editor(self, tab: ttk.Frame) -> None:
        header = ttk.Frame(tab)
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))
        ttk.Label(header, text="内容与格式", style="Title.TLabel").pack(
            side=tk.LEFT
        )
        ttk.Button(header, text="撤回", command=self.undo_action).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(header, text="前进", command=self.redo_action).pack(
            side=tk.RIGHT
        )

        identity = ttk.Frame(tab)
        identity.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        ttk.Label(identity, text="题型").pack(side=tk.LEFT)
        self.kind_combo = ttk.Combobox(
            identity,
            textvariable=self.kind_var,
            values=("客观题", "主观题", "结构内容"),
            state="readonly",
            width=10,
        )
        self.kind_combo.pack(side=tk.LEFT, padx=(5, 14))
        ttk.Label(identity, text="分值").pack(side=tk.LEFT)
        self.score_entry = ttk.Entry(identity, textvariable=self.score_var, width=7)
        self.score_entry.pack(side=tk.LEFT, padx=(5, 14))
        ttk.Label(identity, text="选项排列").pack(side=tk.LEFT)
        self.option_combo = ttk.Combobox(
            identity,
            textvariable=self.option_layout_var,
            values=("四行单列", "两行两列"),
            state="readonly",
            width=10,
        )
        self.option_combo.pack(side=tk.LEFT, padx=(5, 0))

        self.stem_text = tk.Text(
            tab,
            height=10,
            wrap=tk.WORD,
            font=("SimSun", 10),
            relief=tk.SOLID,
            borderwidth=1,
            padx=9,
            pady=8,
            exportselection=False,
        )
        self.stem_text.grid(row=2, column=0, sticky=tk.NSEW)
        self.selection_hint_var = tk.StringVar(
            value="未选择文字时，设置作用于当前结构项。"
        )
        ttk.Label(
            tab,
            textvariable=self.selection_hint_var,
            foreground="#5B6470",
        ).grid(row=3, column=0, sticky=tk.W, pady=(4, 7))

        font_box = ttk.LabelFrame(tab, text="字体", padding=(9, 6))
        font_box.grid(row=4, column=0, sticky=tk.EW, pady=(0, 6))
        ttk.Label(font_box, text="字体").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(
            font_box,
            textvariable=self.font_var,
            values=base.FONT_CHOICES,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky=tk.W, padx=(5, 12))
        ttk.Label(font_box, text="大小").grid(row=0, column=2, sticky=tk.W)
        ttk.Combobox(
            font_box,
            textvariable=self.size_var,
            values=("9", "10.5", "12", "15", "16", "18", "22"),
            width=7,
        ).grid(row=0, column=3, sticky=tk.W, padx=(5, 12))
        ttk.Checkbutton(
            font_box,
            text="加粗",
            variable=self.bold_var,
        ).grid(row=0, column=4, sticky=tk.W)
        ttk.Label(font_box, text="选择项字体").grid(
            row=1, column=0, sticky=tk.W, pady=(6, 0)
        )
        ttk.Combobox(
            font_box,
            textvariable=self.option_font_var,
            values=base.FONT_CHOICES,
            state="readonly",
            width=10,
        ).grid(row=1, column=1, sticky=tk.W, padx=(5, 12), pady=(6, 0))
        ttk.Label(font_box, text="大小").grid(
            row=1, column=2, sticky=tk.W, pady=(6, 0)
        )
        ttk.Combobox(
            font_box,
            textvariable=self.option_size_var,
            values=("9", "10.5", "12"),
            width=7,
        ).grid(row=1, column=3, sticky=tk.W, padx=(5, 12), pady=(6, 0))

        indent_box = ttk.LabelFrame(tab, text="缩进", padding=(9, 6))
        indent_box.grid(row=5, column=0, sticky=tk.EW, pady=(0, 6))
        ttk.Label(indent_box, text="文本之前").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(
            indent_box,
            textvariable=self.left_indent_var,
            width=7,
        ).grid(row=0, column=1, sticky=tk.W, padx=(5, 4))
        ttk.Label(indent_box, text="字符").grid(row=0, column=2, sticky=tk.W)
        ttk.Label(indent_box, text="文本之后").grid(
            row=0, column=3, sticky=tk.W, padx=(14, 0)
        )
        ttk.Entry(
            indent_box,
            textvariable=self.right_indent_var,
            width=7,
        ).grid(row=0, column=4, sticky=tk.W, padx=(5, 4))
        ttk.Label(indent_box, text="字符").grid(row=0, column=5, sticky=tk.W)
        ttk.Label(indent_box, text="特殊格式").grid(
            row=1, column=0, sticky=tk.W, pady=(6, 0)
        )
        ttk.Combobox(
            indent_box,
            textvariable=self.special_indent_var,
            values=("无", "首行", "悬挂"),
            state="readonly",
            width=8,
        ).grid(row=1, column=1, sticky=tk.W, padx=(5, 4), pady=(6, 0))
        ttk.Label(indent_box, text="度量值").grid(
            row=1, column=3, sticky=tk.W, padx=(14, 0), pady=(6, 0)
        )
        ttk.Entry(
            indent_box,
            textvariable=self.special_indent_amount_var,
            width=7,
        ).grid(row=1, column=4, sticky=tk.W, padx=(5, 4), pady=(6, 0))
        ttk.Label(indent_box, text="字符").grid(
            row=1, column=5, sticky=tk.W, pady=(6, 0)
        )
        ttk.Label(indent_box, text="对齐").grid(
            row=2, column=0, sticky=tk.W, pady=(6, 0)
        )
        ttk.Combobox(
            indent_box,
            textvariable=self.alignment_var,
            values=base.ALIGNMENT_CHOICES,
            state="readonly",
            width=10,
        ).grid(row=2, column=1, sticky=tk.W, padx=(5, 4), pady=(6, 0))
        ttk.Label(indent_box, text="选择项文本之前").grid(
            row=3, column=0, sticky=tk.W, pady=(6, 0)
        )
        ttk.Entry(
            indent_box,
            textvariable=self.option_left_var,
            width=7,
        ).grid(row=3, column=1, sticky=tk.W, padx=(5, 4), pady=(6, 0))
        ttk.Label(indent_box, text="悬挂").grid(
            row=3, column=3, sticky=tk.W, padx=(14, 0), pady=(6, 0)
        )
        ttk.Entry(
            indent_box,
            textvariable=self.option_hanging_var,
            width=7,
        ).grid(row=3, column=4, sticky=tk.W, padx=(5, 4), pady=(6, 0))
        ttk.Label(indent_box, text="字符").grid(
            row=3, column=5, sticky=tk.W, pady=(6, 0)
        )

        spacing_box = ttk.LabelFrame(tab, text="间距", padding=(9, 6))
        spacing_box.grid(row=6, column=0, sticky=tk.EW, pady=(0, 6))
        ttk.Label(spacing_box, text="段前").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(
            spacing_box,
            textvariable=self.space_before_var,
            width=7,
        ).grid(row=0, column=1, sticky=tk.W, padx=(5, 4))
        ttk.Label(spacing_box, text="磅").grid(row=0, column=2, sticky=tk.W)
        ttk.Label(spacing_box, text="段后").grid(
            row=0, column=3, sticky=tk.W, padx=(14, 0)
        )
        ttk.Entry(
            spacing_box,
            textvariable=self.space_after_var,
            width=7,
        ).grid(row=0, column=4, sticky=tk.W, padx=(5, 4))
        ttk.Label(spacing_box, text="磅").grid(row=0, column=5, sticky=tk.W)
        ttk.Label(spacing_box, text="行距").grid(
            row=1, column=0, sticky=tk.W, pady=(6, 0)
        )
        ttk.Combobox(
            spacing_box,
            textvariable=self.line_spacing_var,
            values=("1.0", "1.05", "1.25", "1.5", "2.0"),
            width=7,
        ).grid(row=1, column=1, sticky=tk.W, padx=(5, 4), pady=(6, 0))
        ttk.Label(spacing_box, text="倍").grid(
            row=1, column=2, sticky=tk.W, pady=(6, 0)
        )

        flags = ttk.Frame(tab)
        flags.grid(row=7, column=0, sticky=tk.EW, pady=(2, 0))
        ttk.Checkbutton(
            flags,
            text="与下一段同页",
            variable=self.keep_next_var,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            flags,
            text="题目前分页",
            variable=self.page_break_var,
        ).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Label(
            flags,
            text="停止修改约1.2秒后，右侧自动更新。",
            foreground="#666666",
        ).pack(side=tk.RIGHT)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

    def _install_live_bindings(self) -> None:
        general_variables = (
            self.kind_var,
            self.score_var,
            self.option_layout_var,
            self.option_font_var,
            self.option_size_var,
            self.option_left_var,
            self.option_hanging_var,
            self.keep_next_var,
            self.page_break_var,
        )
        format_variables = (
            self.font_var,
            self.size_var,
            self.bold_var,
            self.left_indent_var,
            self.right_indent_var,
            self.special_indent_var,
            self.special_indent_amount_var,
            self.alignment_var,
            self.line_spacing_var,
            self.space_before_var,
            self.space_after_var,
        )
        for variable in general_variables:
            variable.trace_add(
                "write",
                lambda *_args: self.schedule_live_preview(),
            )
        for variable in format_variables:
            variable.trace_add("write", lambda *_args: self._format_changed())
        self.stem_text.bind(
            "<KeyRelease>",
            lambda _event: self.schedule_live_preview(),
        )

    def _current_format_spec(self) -> dict[str, Any]:
        special = self.special_indent_var.get()
        amount = float(self.special_indent_amount_var.get())
        first_line = amount if special == "首行" else (-amount if special == "悬挂" else 0)
        return {
            "font": self.font_var.get(),
            "size_pt": float(self.size_var.get()),
            "bold": self.bold_var.get(),
            "left_indent_chars": float(self.left_indent_var.get()),
            "right_indent_chars": float(self.right_indent_var.get()),
            "special_indent": special,
            "special_indent_chars": amount,
            "first_line_indent_chars": first_line,
            "alignment": self.alignment_var.get(),
            "line_spacing": float(self.line_spacing_var.get()),
            "space_before_pt": float(self.space_before_var.get()),
            "space_after_pt": float(self.space_after_var.get()),
            "keep_with_next": self.keep_next_var.get(),
            "page_break_before": self.page_break_var.get(),
            "option_font": self.option_font_var.get(),
            "option_size_pt": float(self.option_size_var.get()),
            "option_left_indent_chars": float(self.option_left_var.get()),
            "option_hanging_indent_chars": float(self.option_hanging_var.get()),
        }

    def _load_question_fields(self, question: dict[str, Any]) -> None:
        FinalDesktopAppV7._load_question_fields(self, question)
        self.current_paragraph_formats = [
            dict(value) for value in question.get("paragraph_formats", [])
        ]
        spec = question.get("format", {})
        default_special = "悬挂"
        default_amount = 1.5
        self._load_word_controls(spec, default_special, default_amount)

    def _load_nonquestion_fields(self, block: dict[str, Any]) -> None:
        FinalDesktopAppV7._load_nonquestion_fields(self, block)
        self.current_paragraph_formats = [
            dict(value) for value in block.get("paragraph_formats", [])
        ]
        spec = block.get("format", {})
        first = float(spec.get("first_line_indent_chars", self.indent_var.get() or 0))
        default_special = "首行" if first > 0 else ("悬挂" if first < 0 else "无")
        self._load_word_controls(spec, default_special, abs(first))

    def _load_word_controls(
        self,
        spec: dict[str, Any],
        default_special: str,
        default_amount: float,
    ) -> None:
        self.bold_var.set(bool(spec.get("bold", False)))
        self.left_indent_var.set(str(spec.get("left_indent_chars", 0)))
        self.right_indent_var.set(str(spec.get("right_indent_chars", 0)))
        special = str(spec.get("special_indent", default_special))
        self.special_indent_var.set(special)
        self.special_indent_amount_var.set(
            str(spec.get("special_indent_chars", default_amount))
        )

    def apply_current_question(self, *, silent: bool = False) -> bool:
        applied = super().apply_current_question(silent=silent)
        if not applied or self.selected_block_index is None:
            return applied
        block = self.raw_exam["blocks"][self.selected_block_index]
        owner = block["question"] if block.get("type") == "question" else block
        owner["paragraph_formats"] = [
            dict(value) for value in self.current_paragraph_formats
        ]
        return True

    def _format_changed(self) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        if self._has_selection():
            try:
                self._record_history()
                self._apply_selection_format()
                self._save_selected_paragraph_formats()
                self.selection_dirty = True
                self.apply_current_question(silent=True)
                self.selection_hint_var.set(
                    "已应用到选中文字及其所在段落，正在更新右侧预览。"
                )
            except ValueError:
                return
        self.schedule_live_preview()

    def _has_selection(self) -> bool:
        return bool(self.stem_text.tag_ranges(tk.SEL))

    def _apply_selection_format(self) -> None:
        start = self.stem_text.index(tk.SEL_FIRST)
        end = self.stem_text.index(tk.SEL_LAST)
        size = float(self.size_var.get())
        self._remove_inline_tags(start, end)
        name = f"inlinefmt_{len(self.inline_tag_specs) + 1}"
        spec = {
            "font": self.font_var.get(),
            "size_pt": size,
            "bold": self.bold_var.get(),
        }
        self.inline_tag_specs[name] = spec
        weight = "bold" if self.bold_var.get() else "normal"
        self.stem_text.tag_configure(
            name,
            font=(self.font_var.get(), max(7, int(round(size))), weight),
        )
        self.stem_text.tag_add(name, start, end)
        self.stem_text.tag_add(tk.SEL, start, end)

    def _save_selected_paragraph_formats(self) -> None:
        spec = self._current_format_spec()
        selected_lines = self._selected_line_numbers()
        for line_number in selected_lines:
            if line_number >= len(self.current_line_map):
                continue
            mapping = dict(self.current_line_map[line_number])
            entry = {
                **mapping,
                "font": spec["font"],
                "size_pt": spec["size_pt"],
                "bold": spec["bold"],
                "left_indent_chars": spec["left_indent_chars"],
                "right_indent_chars": spec["right_indent_chars"],
                "special_indent": spec["special_indent"],
                "special_indent_chars": spec["special_indent_chars"],
                "alignment": spec["alignment"],
                "line_spacing": spec["line_spacing"],
                "space_before_pt": spec["space_before_pt"],
                "space_after_pt": spec["space_after_pt"],
            }
            key = (entry.get("target"), int(entry.get("target_index", 0)))
            self.current_paragraph_formats = [
                value
                for value in self.current_paragraph_formats
                if (
                    value.get("target"),
                    int(value.get("target_index", 0)),
                )
                != key
            ]
            self.current_paragraph_formats.append(entry)

    def _selected_line_numbers(self) -> list[int]:
        start = self.stem_text.index(tk.SEL_FIRST)
        end = self.stem_text.index(tk.SEL_LAST)
        start_line, _start_col = map(int, start.split("."))
        end_line, end_col = map(int, end.split("."))
        if end_col == 0 and end_line > start_line:
            end_line -= 1
        return list(range(start_line - 1, end_line))

    def _collect_inline_formats(self) -> list[dict[str, Any]]:
        return super()._collect_inline_formats()

    def _render_inline_tags(self, entries: list[dict[str, Any]]) -> None:
        for name in list(self.inline_tag_specs):
            self.stem_text.tag_delete(name)
        self.inline_tag_specs = {}
        for index, entry in enumerate(entries):
            line = int(entry.get("line", 0)) + 1
            name = f"inlinefmt_{index + 1}"
            spec = {
                "font": str(entry.get("font", "宋体")),
                "size_pt": float(entry.get("size_pt", 10.5)),
                "bold": bool(entry.get("bold", False)),
            }
            self.inline_tag_specs[name] = spec
            weight = "bold" if spec["bold"] else "normal"
            self.stem_text.tag_configure(
                name,
                font=(
                    spec["font"],
                    max(7, int(round(spec["size_pt"]))),
                    weight,
                ),
            )
            self.stem_text.tag_add(
                name,
                f"{line}.{int(entry.get('start', 0))}",
                f"{line}.{int(entry.get('end', 0))}",
            )

    def apply_selected_text_format(self) -> None:
        if not self._has_selection():
            self.selection_hint_var.set("请先在上方内容框中选择文字。")
            return
        self._format_changed()

    def clear_selected_text_format(self) -> None:
        if not self._has_selection():
            self.selection_hint_var.set("请先在上方内容框中选择文字。")
            return
        self._record_history()
        start = self.stem_text.index(tk.SEL_FIRST)
        end = self.stem_text.index(tk.SEL_LAST)
        self._remove_inline_tags(start, end)
        self.current_paragraph_formats = [
            value
            for value in self.current_paragraph_formats
            if int(value.get("target_index", -1))
            not in self._selected_line_numbers()
        ]
        self.apply_current_question(silent=True)
        self.schedule_live_preview()

    def _selection_status(self) -> None:
        if self._has_selection():
            self.selection_hint_var.set(
                "已选择文字，调整字体、缩进或间距会实时作用到所选内容。"
            )
        else:
            self.selection_hint_var.set(
                "未选择文字时，设置作用于当前结构项。"
            )


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
    WordLikeDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
