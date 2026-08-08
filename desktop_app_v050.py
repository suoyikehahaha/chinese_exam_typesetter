"""工作台 v0.5.0，连续预览、同步编辑与语义格式。"""

from __future__ import annotations

from copy import deepcopy
import argparse
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

import desktop_app as base
from app.block_overrides import apply_block_overrides
from app.config import load_layout
from app.editor_importers import import_exam
from app.exporters import PdfExporter
from app.models import ExamDocument
from app.pagination import apply_pagination_guards
from app.question_overrides import apply_question_overrides
from app.renderers import DocxRenderer
from app.semantic_formatting import apply_semantic_formatting
from app.validators import check_required_fonts, validate_exam
from desktop_app_v040 import FlexibleDesktopApp


base.VERSION = "0.5.0"


def build_documents_v5(
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
    """生成文件，并在 PDF 导出前应用语义与用户覆盖格式。"""

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
    apply_semantic_formatting(docx_work)
    apply_block_overrides(docx_work, raw_exam)
    apply_question_overrides(docx_work, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = PdfExporter().export(
            docx_work,
            output_dir / f"{basename}.pdf",
        )
    return (docx_work if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v5


class AdvancedDesktopApp(FlexibleDesktopApp):
    """连续页面预览和自动同步编辑器。"""

    def __init__(self) -> None:
        self.loading_fields = False
        self.live_after_id: str | None = None
        self.right_after_id: str | None = None
        self.preview_pending = False
        self.preview_photos: list[ImageTk.PhotoImage] = []
        self.page_y_positions: list[int] = []
        self.preview_total_height = 1
        self.selected_block_type: str | None = None
        super().__init__()
        self._install_live_bindings()
        self.after(100, self._select_first_question)

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="试卷结构").pack(anchor=tk.W)
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=False, pady=(6, 10))
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("kind", "score"),
            show="tree headings",
            height=11,
        )
        self.tree.heading("#0", text="内容")
        self.tree.heading("kind", text="类型")
        self.tree.heading("score", text="分值")
        self.tree.column("#0", width=255, stretch=True)
        self.tree.column("kind", width=60, anchor=tk.CENTER)
        self.tree.column("score", width=45, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient=tk.VERTICAL,
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        ttk.Separator(parent).pack(fill=tk.X, pady=(0, 8))
        self.selection_title_var = tk.StringVar(value="请选择结构项")
        ttk.Label(
            parent,
            textvariable=self.selection_title_var,
            style="Title.TLabel",
        ).pack(anchor=tk.W, pady=(0, 6))

        shell = ttk.Frame(parent)
        shell.pack(fill=tk.BOTH, expand=True)
        self.detail_canvas = tk.Canvas(shell, highlightthickness=0)
        detail_scroll = ttk.Scrollbar(
            shell,
            orient=tk.VERTICAL,
            command=self.detail_canvas.yview,
        )
        self.detail_canvas.configure(yscrollcommand=detail_scroll.set)
        self.detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_frame = ttk.Frame(self.detail_canvas, padding=(4, 2, 10, 12))
        self.detail_window = self.detail_canvas.create_window(
            (0, 0),
            window=self.detail_frame,
            anchor=tk.NW,
        )
        self.detail_frame.bind(
            "<Configure>",
            lambda _event: self.detail_canvas.configure(
                scrollregion=self.detail_canvas.bbox("all")
            ),
        )
        self.detail_canvas.bind(
            "<Configure>",
            lambda event: self.detail_canvas.itemconfigure(
                self.detail_window,
                width=event.width,
            ),
        )
        self._build_combined_editor(self.detail_frame)

    def _build_combined_editor(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="内容与题型").grid(
            row=0,
            column=0,
            columnspan=4,
            sticky=tk.W,
            pady=(2, 6),
        )
        ttk.Label(tab, text="题型").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.kind_combo = ttk.Combobox(
            tab,
            textvariable=self.kind_var,
            values=("客观题", "主观题", "结构内容"),
            state="readonly",
            width=12,
        )
        self.kind_combo.grid(row=1, column=1, sticky=tk.W, pady=4)
        ttk.Label(tab, text="分值").grid(
            row=1,
            column=2,
            sticky=tk.W,
            padx=(16, 0),
        )
        self.score_entry = ttk.Entry(tab, textvariable=self.score_var, width=9)
        self.score_entry.grid(row=1, column=3, sticky=tk.W)
        ttk.Label(tab, text="选项排列").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.option_combo = ttk.Combobox(
            tab,
            textvariable=self.option_layout_var,
            values=("四行单列", "两行两列"),
            state="readonly",
            width=12,
        )
        self.option_combo.grid(row=2, column=1, sticky=tk.W, pady=4)
        ttk.Label(tab, text="内容").grid(
            row=3,
            column=0,
            columnspan=4,
            sticky=tk.W,
            pady=(7, 4),
        )
        self.stem_text = tk.Text(
            tab,
            height=6,
            wrap=tk.WORD,
            font=("SimSun", 10),
            relief=tk.SOLID,
            borderwidth=1,
            padx=8,
            pady=8,
        )
        self.stem_text.grid(row=4, column=0, columnspan=4, sticky=tk.NSEW)

        ttk.Separator(tab).grid(
            row=5,
            column=0,
            columnspan=4,
            sticky=tk.EW,
            pady=10,
        )
        ttk.Label(tab, text="对应格式").grid(
            row=6,
            column=0,
            columnspan=4,
            sticky=tk.W,
            pady=(0, 5),
        )
        self._small_combo(tab, 7, "字体", self.font_var, base.FONT_CHOICES)
        self._small_entry(tab, 8, "字号", self.size_var, "五号 10.5")
        self._small_entry(tab, 9, "首行缩进", self.indent_var, "字符")
        self._small_combo(
            tab,
            10,
            "对齐",
            self.alignment_var,
            base.ALIGNMENT_CHOICES,
        )
        self._small_entry(tab, 11, "行距", self.line_spacing_var, "倍")
        self._small_entry(tab, 12, "段前", self.space_before_var, "磅")
        self._small_entry(tab, 13, "段后", self.space_after_var, "磅")

        ttk.Separator(tab).grid(
            row=14,
            column=0,
            columnspan=4,
            sticky=tk.EW,
            pady=10,
        )
        ttk.Label(tab, text="选择项格式").grid(
            row=15,
            column=0,
            columnspan=4,
            sticky=tk.W,
            pady=(0, 5),
        )
        self._small_combo(tab, 16, "字体", self.option_font_var, base.FONT_CHOICES)
        self._small_entry(tab, 17, "字号", self.option_size_var, "五号 10.5")
        self._small_entry(tab, 18, "左缩进", self.option_left_var, "字符")
        self._small_entry(tab, 19, "悬挂", self.option_hanging_var, "字符")

        flags = ttk.Frame(tab)
        flags.grid(row=20, column=0, columnspan=4, sticky=tk.W, pady=(8, 4))
        ttk.Checkbutton(
            flags,
            text="与下一段同页",
            variable=self.keep_next_var,
            command=self.schedule_live_preview,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            flags,
            text="题目前分页",
            variable=self.page_break_var,
            command=self.schedule_live_preview,
        ).pack(side=tk.LEFT, padx=(18, 0))
        ttk.Label(
            tab,
            text="修改停止约 1.2 秒后，右侧预览会自动更新。",
            foreground="#666666",
        ).grid(row=21, column=0, columnspan=4, sticky=tk.W, pady=(8, 4))
        ttk.Button(
            tab,
            text="立即应用",
            style="Primary.TButton",
            command=self._apply_now,
        ).grid(row=22, column=0, columnspan=4, sticky=tk.E, pady=(4, 0))
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(3, weight=1)

    def _small_entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        hint: str,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row,
            column=1,
            sticky=tk.EW,
            pady=3,
        )
        ttk.Label(parent, text=hint, foreground="#777777").grid(
            row=row,
            column=2,
            sticky=tk.W,
            padx=(7, 0),
        )

    def _small_combo(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
        ttk.Combobox(
            parent,
            textvariable=variable,
            values=values,
            state="readonly",
        ).grid(row=row, column=1, sticky=tk.EW, pady=3)

    def _build_preview_panel(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(bar, text="整卷预览", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(bar, text="上一页", command=self.previous_page).pack(
            side=tk.LEFT,
            padx=(20, 4),
        )
        ttk.Button(bar, text="下一页", command=self.next_page).pack(side=tk.LEFT)
        ttk.Button(bar, text="缩小", command=lambda: self.change_zoom(-0.08)).pack(
            side=tk.LEFT,
            padx=(18, 4),
        )
        ttk.Button(bar, text="放大", command=lambda: self.change_zoom(0.08)).pack(
            side=tk.LEFT
        )
        ttk.Label(bar, textvariable=self.page_status_var).pack(side=tk.RIGHT)

        self.preview_tabs = ttk.Notebook(parent)
        self.preview_tabs.pack(fill=tk.BOTH, expand=True)
        page_tab = ttk.Frame(self.preview_tabs)
        text_tab = ttk.Frame(self.preview_tabs, padding=12)
        self.preview_tabs.add(page_tab, text="连续页面")
        self.preview_tabs.add(text_tab, text="选中文字编辑")

        self.canvas = tk.Canvas(
            page_tab,
            background="#D9DDE3",
            highlightthickness=0,
        )
        y_scroll = ttk.Scrollbar(
            page_tab,
            orient=tk.VERTICAL,
            command=self.canvas.yview,
        )
        x_scroll = ttk.Scrollbar(
            page_tab,
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
        page_tab.columnconfigure(0, weight=1)
        page_tab.rowconfigure(0, weight=1)
        self.canvas.bind("<MouseWheel>", self._on_preview_wheel)
        self.canvas.bind("<Configure>", self._schedule_page_redraw)
        self.canvas.bind(
            "<Double-Button-1>",
            lambda _event: self.preview_tabs.select(text_tab),
        )

        ttk.Label(
            text_tab,
            text="当前结构项文本",
            style="Title.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            text_tab,
            text="可在下面选择、复制或修改文字。修改停止后会同步到左侧并更新页面。",
            foreground="#555555",
            wraplength=650,
        ).pack(anchor=tk.W, pady=(4, 8))
        self.right_text = tk.Text(
            text_tab,
            wrap=tk.WORD,
            font=("SimSun", 12),
            padx=12,
            pady=12,
            undo=True,
        )
        self.right_text.pack(fill=tk.BOTH, expand=True)
        self.right_text.bind("<KeyRelease>", self._schedule_right_commit)
        ttk.Button(
            text_tab,
            text="立即同步",
            style="Primary.TButton",
            command=self._commit_right_text,
        ).pack(anchor=tk.E, pady=(8, 0))

    def _install_live_bindings(self) -> None:
        variables = (
            self.kind_var,
            self.score_var,
            self.option_layout_var,
            self.font_var,
            self.size_var,
            self.indent_var,
            self.alignment_var,
            self.line_spacing_var,
            self.space_before_var,
            self.space_after_var,
            self.option_font_var,
            self.option_size_var,
            self.option_left_var,
            self.option_hanging_var,
        )
        for variable in variables:
            variable.trace_add("write", lambda *_args: self.schedule_live_preview())
        self.stem_text.bind("<KeyRelease>", lambda _event: self.schedule_live_preview())

    def _on_tree_select(self, _event: object) -> None:
        self.apply_current_question(silent=True)
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        if not iid.startswith("block-"):
            self.selected_block_index = None
            return
        index = int(iid.split("-", 1)[1])
        block = self.raw_exam["blocks"][index]
        self.selected_block_index = index
        self.selected_block_type = str(block.get("type", ""))
        self.loading_fields = True
        try:
            if block.get("type") == "question":
                question = block["question"]
                self.selection_title_var.set(f"第 {question['number']} 题")
                self._load_question_fields(question)
                self.kind_combo.configure(state="readonly")
                self.score_entry.configure(state=tk.NORMAL)
                self.option_combo.configure(state="readonly")
            else:
                self.selection_title_var.set(self._block_label(block))
                self._load_nonquestion_fields(block)
                self.kind_combo.configure(state=tk.DISABLED)
                self.score_entry.configure(state=tk.DISABLED)
                self.option_combo.configure(state=tk.DISABLED)
            self._load_right_text(block)
        finally:
            self.loading_fields = False

    def _load_nonquestion_fields(self, block: dict[str, Any]) -> None:
        block_type = block.get("type")
        defaults = {
            "section_title": ("黑体", 12, 0, "左对齐"),
            "subsection": ("宋体", 10.5, 0, "左对齐"),
            "instruction": ("宋体", 10.5, 2, "左对齐"),
            "material": ("楷体", 10.5, 2, "左对齐"),
            "poetry": ("楷体", 10.5, 0, "居中"),
        }
        font, size, indent, alignment = defaults.get(
            str(block_type),
            ("宋体", 10.5, 0, "左对齐"),
        )
        spec = block.get("format", {})
        self.kind_var.set("结构内容")
        self.score_var.set("")
        self.option_layout_var.set("四行单列")
        self.stem_text.delete("1.0", tk.END)
        self.stem_text.insert("1.0", self._block_edit_text(block))
        self.font_var.set(str(spec.get("font", font)))
        self.size_var.set(str(spec.get("size_pt", size)))
        self.indent_var.set(str(spec.get("first_line_indent_chars", indent)))
        self.alignment_var.set(str(spec.get("alignment", alignment)))
        self.line_spacing_var.set(str(spec.get("line_spacing", 1.25)))
        self.space_before_var.set(str(spec.get("space_before_pt", 0)))
        self.space_after_var.set(str(spec.get("space_after_pt", 0)))
        self.keep_next_var.set(bool(spec.get("keep_with_next", False)))
        self.page_break_var.set(bool(spec.get("page_break_before", False)))
        self.option_font_var.set("宋体")
        self.option_size_var.set("10.5")
        self.option_left_var.set("1.5")
        self.option_hanging_var.set("1.7")

    def _block_label(self, block: dict[str, Any]) -> str:
        return str(
            block.get("text")
            or block.get("name")
            or block.get("title")
            or {
                "material": "阅读材料",
                "poetry": "诗歌",
                "instruction": "阅读提示",
            }.get(block.get("type"), "结构内容")
        )

    def _block_edit_text(self, block: dict[str, Any]) -> str:
        block_type = block.get("type")
        if block_type in {"section_title", "instruction"}:
            return str(block.get("text", ""))
        if block_type == "subsection":
            return str(block.get("name", "")) + str(block.get("meta", ""))
        if block_type in {"material", "poetry"}:
            values: list[str] = []
            for key in ("title", "author"):
                if block.get(key):
                    values.append(str(block[key]))
            values.extend(str(item) for item in block.get("paragraphs", []))
            for key in ("note", "source"):
                if block.get(key):
                    values.append(str(block[key]))
            return "\n".join(values)
        return ""

    def _load_right_text(self, block: dict[str, Any]) -> None:
        self.right_text.delete("1.0", tk.END)
        if block.get("type") == "question":
            self.right_text.insert("1.0", str(block["question"].get("stem", "")))
        else:
            self.right_text.insert("1.0", self._block_edit_text(block))

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
            question["stem"] = content
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
            iid = f"block-{self.selected_block_index}"
            if self.tree.exists(iid):
                kind = "客观" if question["kind"] == "objective" else "主观"
                self.tree.item(
                    iid,
                    text=f"{question['number']}．{question['stem'][:18]}",
                    values=(kind, "" if question["score"] is None else question["score"]),
                )
        else:
            self._commit_nonquestion_content(block, content)
            block["format"] = spec
        if not silent:
            self.status_var.set("内容和格式已同步。")
        return True

    def _current_format_spec(self) -> dict[str, Any]:
        return {
            "font": self.font_var.get(),
            "size_pt": float(self.size_var.get()),
            "first_line_indent_chars": float(self.indent_var.get()),
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

    def _commit_nonquestion_content(
        self,
        block: dict[str, Any],
        content: str,
    ) -> None:
        block_type = block.get("type")
        if block_type in {"section_title", "instruction"}:
            block["text"] = content
        elif block_type == "subsection":
            block["name"] = content
            block["meta"] = ""
        elif block_type in {"material", "poetry"}:
            block["paragraphs"] = [
                line.strip()
                for line in content.splitlines()
                if line.strip()
            ]
            for key in ("title", "author", "note", "source"):
                block[key] = ""

    def _apply_now(self) -> None:
        if self.apply_current_question():
            self.request_preview()

    def schedule_live_preview(self) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        if self.live_after_id:
            self.after_cancel(self.live_after_id)
        self.live_after_id = self.after(1200, self._live_preview_now)

    def _live_preview_now(self) -> None:
        self.live_after_id = None
        if self.apply_current_question(silent=True):
            self._mirror_left_to_right()
            self.request_preview()

    def request_preview(self) -> None:
        if self.busy:
            self.preview_pending = True
            self.status_var.set("已记录新修改，当前预览完成后自动更新。")
            return
        self.preview_pending = False
        super().request_preview()

    def _poll_messages(self) -> None:
        was_busy = self.busy
        base.DesktopApp._poll_messages(self)
        if was_busy and not self.busy and self.preview_pending:
            self.preview_pending = False
            self.after(250, self.request_preview)

    def _schedule_right_commit(self, _event: object) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        if self.right_after_id:
            self.after_cancel(self.right_after_id)
        self.right_after_id = self.after(900, self._commit_right_text)

    def _commit_right_text(self) -> None:
        self.right_after_id = None
        if self.selected_block_index is None:
            return
        text = self.right_text.get("1.0", tk.END).strip()
        block = self.raw_exam["blocks"][self.selected_block_index]
        self.loading_fields = True
        try:
            self.stem_text.delete("1.0", tk.END)
            self.stem_text.insert("1.0", text)
        finally:
            self.loading_fields = False
        if block.get("type") == "question":
            block["question"]["stem"] = text
        else:
            self._commit_nonquestion_content(block, text)
        self.schedule_live_preview()

    def _mirror_left_to_right(self) -> None:
        if self.right_text.focus_get() == self.right_text:
            return
        text = self.stem_text.get("1.0", tk.END).strip()
        self.loading_fields = True
        try:
            self.right_text.delete("1.0", tk.END)
            self.right_text.insert("1.0", text)
        finally:
            self.loading_fields = False

    def _show_current_page(self) -> None:
        if not self.preview_pages:
            return
        self.preview_photos = []
        self.page_y_positions = []
        self.canvas.delete("all")
        images: list[tuple[ImageTk.PhotoImage, int, int]] = []
        max_width = 0
        y = 28
        for path in self.preview_pages:
            source = Image.open(path)
            width = max(360, int(source.width * self.zoom))
            height = int(source.height * width / source.width)
            resized = source.resize((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(resized)
            self.preview_photos.append(photo)
            images.append((photo, width, height))
            self.page_y_positions.append(y)
            max_width = max(max_width, width)
            y += height + 34
        canvas_width = max(self.canvas.winfo_width(), max_width + 90)
        center_x = canvas_width // 2
        y = 28
        for index, (photo, width, height) in enumerate(images):
            self.canvas.create_rectangle(
                center_x - width // 2 + 6,
                y + 6,
                center_x + width // 2 + 6,
                y + height + 6,
                fill="#ADB3BB",
                outline="",
            )
            self.canvas.create_image(center_x, y, image=photo, anchor=tk.N)
            self.canvas.create_text(
                center_x,
                y + height + 17,
                text=f"第 {index + 1} 页",
                fill="#555555",
            )
            y += height + 34
        self.preview_total_height = y
        self.canvas.configure(
            scrollregion=(0, 0, canvas_width, self.preview_total_height)
        )
        self.page_status_var.set(
            f"共 {len(self.preview_pages)} 页　{int(self.zoom * 100)}%"
        )

    def _schedule_page_redraw(self, _event: object) -> None:
        if hasattr(self, "_redraw_after") and self._redraw_after:
            self.after_cancel(self._redraw_after)
        self._redraw_after = self.after(180, self._show_current_page)

    def _on_preview_wheel(self, event: Any) -> str:
        self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")
        self.after(50, self._update_visible_page)
        return "break"

    def _update_visible_page(self) -> None:
        if not self.page_y_positions:
            return
        top = self.canvas.canvasy(0)
        index = min(
            range(len(self.page_y_positions)),
            key=lambda item: abs(self.page_y_positions[item] - top),
        )
        self.preview_page_index = index
        self.page_status_var.set(
            f"第 {index + 1} / {len(self.preview_pages)} 页　"
            f"{int(self.zoom * 100)}%"
        )

    def previous_page(self) -> None:
        if not self.preview_pages:
            return
        self.preview_page_index = max(0, self.preview_page_index - 1)
        self._scroll_to_page(self.preview_page_index)

    def next_page(self) -> None:
        if not self.preview_pages:
            return
        self.preview_page_index = min(
            len(self.preview_pages) - 1,
            self.preview_page_index + 1,
        )
        self._scroll_to_page(self.preview_page_index)

    def _scroll_to_page(self, index: int) -> None:
        if not self.page_y_positions:
            return
        fraction = self.page_y_positions[index] / max(1, self.preview_total_height)
        self.canvas.yview_moveto(fraction)
        self._update_visible_page()

    def change_zoom(self, delta: float) -> None:
        self.zoom = min(1.35, max(0.35, self.zoom + delta))
        self._show_current_page()
        self._scroll_to_page(self.preview_page_index)

    def _select_first_question(self) -> None:
        for item in self.tree.get_children():
            for child in self.tree.get_children(item):
                index = int(child.split("-", 1)[1]) if child.startswith("block-") else -1
                if index >= 0 and self.raw_exam["blocks"][index].get("type") == "question":
                    self.tree.selection_set(child)
                    self.tree.focus(child)
                    self.tree.see(child)
                    self._on_tree_select(None)
                    return

    def import_new_exam(self) -> None:
        before = self.current_exam_path
        super().import_new_exam()
        if self.current_exam_path != before:
            self.after(100, self._select_first_question)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", metavar="OUTPUT")
    parser.add_argument("--import-test", metavar="FILE")
    parser.add_argument("--semantic-test", metavar="OUTPUT")
    args, _ = parser.parse_known_args()
    if args.self_test:
        return base.self_test(Path(args.self_test))
    if args.import_test:
        data = import_exam(Path(args.import_test))
        numbers = [
            block["question"]["number"]
            for block in data["blocks"]
            if block.get("type") == "question"
        ]
        print(numbers)
        return 0
    AdvancedDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
