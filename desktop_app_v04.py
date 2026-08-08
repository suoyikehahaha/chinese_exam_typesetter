"""Version 0.4 contextual inspector with a stable read-only page preview."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Iterable

import desktop_app as legacy_base
import desktop_app_current as current_runtime
import desktop_app_current_v01 as current_v02
import desktop_app_v03 as v03
import app.internal_preview_v02 as internal_preview_v02
from app.config import load_layout
from app.current_pipeline_v04 import build_documents
from app.inspector_model_v04 import (
    ContentObject,
    build_object_locators,
    content_objects_for_block,
    default_format_for,
    format_owner,
    inline_formats_for,
    metadata_content_objects,
    objects_in_scope,
    paragraph_format_for,
    remove_paragraph_format,
    set_content_object_text,
    set_inline_format,
    set_paragraph_format,
    summary_text,
)
from app.page_layout_v04 import PAGE_KEYS, adjusted_layout_v04
from app.read_only_preview_v04 import ReadOnlyPreviewV04
from app.score_summary_v03 import TARGET_SCORE, ScoreSummary, calculate_score_summary, format_score, parse_score


APP_VERSION = "0.4.0"
APP_TITLE = current_v02.APP_TITLE
legacy_base.VERSION = APP_VERSION
legacy_base.build_documents = build_documents
current_runtime.APP_VERSION = APP_VERSION
current_runtime.build_documents = build_documents
current_v02.APP_VERSION = APP_VERSION
current_v02.build_documents = build_documents
v03.APP_VERSION = APP_VERSION
internal_preview_v02.adjusted_layout = adjusted_layout_v04


FONT_CHOICES = ("宋体", "黑体", "楷体", "仿宋")
ALIGNMENT_CHOICES = ("左对齐", "居中", "右对齐", "两端对齐")
SPECIAL_INDENTS = ("无", "首行", "悬挂")
BATCH_SCOPES = ("当前题目", "当前大题", "整份试卷")


class CollapsibleGroup(ttk.Frame):
    """A compact property group with an optional template reset action."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        *,
        expanded: bool = True,
        reset_command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, style="InspectorGroup.TFrame")
        self.title = title
        self.expanded = expanded
        header = ttk.Frame(self, style="InspectorHeader.TFrame", padding=(6, 5))
        header.pack(fill=tk.X)
        self.toggle_button = ttk.Button(
            header,
            text="▾" if expanded else "▸",
            width=2,
            command=self.toggle,
            style="InspectorToggle.TButton",
        )
        self.toggle_button.pack(side=tk.LEFT)
        ttk.Label(
            header,
            text=title,
            style="InspectorHeader.TLabel",
        ).pack(side=tk.LEFT, padx=(4, 0))
        if reset_command is not None:
            ttk.Button(
                header,
                text="恢复模板值",
                command=reset_command,
                style="InspectorReset.TButton",
            ).pack(side=tk.RIGHT)
        self.body = ttk.Frame(self, padding=(8, 7, 8, 8), style="InspectorGroup.TFrame")
        if expanded:
            self.body.pack(fill=tk.X)

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.toggle_button.configure(text="▾" if expanded else "▸")
        if expanded:
            self.body.pack(fill=tk.X)
        else:
            self.body.pack_forget()


class ExportWarningDialog(tk.Toplevel):
    """Nonblocking validation choice shown immediately before DOCX export."""

    def __init__(self, parent: tk.Misc, lines: Iterable[str]) -> None:
        super().__init__(parent)
        self.result = False
        self.title("导出前提醒")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="当前试卷还有需要检查的项目",
            font=("Microsoft YaHei UI", 11, "bold"),
            foreground="#8A5700",
        ).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text="\n".join(lines),
            justify=tk.LEFT,
            wraplength=470,
        ).pack(anchor=tk.W, pady=(9, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="返回检查", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            buttons,
            text="继续导出",
            style="Primary.TButton",
            command=self._continue,
        ).pack(side=tk.RIGHT, padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self.geometry(f"520x{max(220, self.winfo_reqheight())}")

    def _continue(self) -> None:
        self.result = True
        self.destroy()


class CurrentDesktopApp(v03.CurrentDesktopApp):
    """Context-sensitive left inspector paired with a read-only A4 preview."""

    def __init__(self) -> None:
        self.selected_content_object: ContentObject | None = None
        self._content_objects: list[ContentObject] = []
        self._text_commit_after: str | None = None
        self._global_commit_after: str | None = None
        self._search_after: str | None = None
        self._ui_sash_initialized = False
        self._warning_refreshing = False
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.after_idle(self._restore_editor_sash)

    def _create_variables(self) -> None:
        super()._create_variables()
        self.search_var = tk.StringVar()
        self.warning_var = tk.StringVar()
        self.batch_scope_var = tk.StringVar(value="当前题目")
        self.margin_top_var = tk.StringVar(value="20")
        self.margin_bottom_var = tk.StringVar(value="18")
        self.margin_left_var = tk.StringVar(value="22")
        self.margin_right_var = tk.StringVar(value="18")
        if not hasattr(self, "bold_var"):
            self.bold_var = tk.BooleanVar(value=False)
        if not hasattr(self, "left_indent_var"):
            self.left_indent_var = tk.StringVar(value="0")
        if not hasattr(self, "right_indent_var"):
            self.right_indent_var = tk.StringVar(value="0")
        if not hasattr(self, "special_indent_var"):
            self.special_indent_var = tk.StringVar(value="无")
        if not hasattr(self, "special_indent_amount_var"):
            self.special_indent_amount_var = tk.StringVar(value="0")

    def _setup_styles(self) -> None:
        super()._setup_styles()
        style = ttk.Style(self)
        style.configure("InspectorGroup.TFrame", background="#FFFFFF")
        style.configure("InspectorHeader.TFrame", background="#F3F5F7")
        style.configure(
            "InspectorHeader.TLabel",
            background="#F3F5F7",
            foreground="#202124",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure("InspectorToggle.TButton", padding=(1, 1))
        style.configure("InspectorReset.TButton", padding=(5, 2), font=("Microsoft YaHei UI", 8))
        style.configure("ScoreStrip.TFrame", background="#EEF5FC")
        style.configure(
            "ScoreStrip.TLabel",
            background="#EEF5FC",
            foreground="#17324D",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure("Object.Treeview", rowheight=26)
        style.configure("Property.TSpinbox", arrowsize=12)

    def _build_local_format_controls(self) -> None:
        """The v0.4 inspector owns selection-aware formatting controls."""

        return

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        top_actions = ttk.Frame(parent)
        top_actions.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(top_actions, text="试卷结构", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(top_actions, text="撤回", command=self.undo_action).pack(side=tk.RIGHT)
        ttk.Button(top_actions, text="前进", command=self.redo_action).pack(side=tk.RIGHT, padx=(0, 6))

        score_strip = ttk.Frame(parent, style="ScoreStrip.TFrame", padding=(8, 6))
        score_strip.pack(fill=tk.X, pady=(0, 5))
        self.score_total_label = ttk.Label(score_strip, text="当前 0 / 150 分", style="ScoreStrip.TLabel")
        self.score_total_label.pack(side=tk.LEFT)
        self.score_delta_label = ttk.Label(score_strip, text="待录入分值", style="ScoreStrip.TLabel")
        self.score_delta_label.pack(side=tk.RIGHT)

        self.warning_banner = tk.Label(
            parent,
            textvariable=self.warning_var,
            background="#FFF4CE",
            foreground="#6A4500",
            anchor=tk.W,
            justify=tk.LEFT,
            padx=8,
            pady=5,
            wraplength=470,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.warning_banner.pack(fill=tk.X, pady=(0, 6))

        self.editor_pane = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        self.editor_pane.pack(fill=tk.BOTH, expand=True)
        structure_panel = ttk.Frame(self.editor_pane)
        inspector_panel = ttk.Frame(self.editor_pane)
        self.editor_pane.add(structure_panel, weight=35)
        self.editor_pane.add(inspector_panel, weight=65)
        self.editor_pane.bind("<ButtonRelease-1>", self._save_editor_sash, add="+")

        search_row = ttk.Frame(structure_panel)
        search_row.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(search_row, text="查找").pack(side=tk.LEFT)
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(search_row, text="全部展开", command=lambda: self._set_tree_open(True)).pack(side=tk.LEFT)
        ttk.Button(search_row, text="全部收起", command=lambda: self._set_tree_open(False)).pack(side=tk.LEFT, padx=(4, 0))

        tree_frame = ttk.Frame(structure_panel)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("kind", "score"),
            show="tree headings",
            height=10,
        )
        self.tree.heading("#0", text="内容")
        self.tree.heading("kind", text="类型")
        self.tree.heading("score", text="分值")
        self.tree.column("#0", width=250, stretch=True)
        self.tree.column("kind", width=58, anchor=tk.CENTER)
        self.tree.column("score", width=48, anchor=tk.CENTER)
        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("warning", background="#FFF4CE", foreground="#6A4500")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self.selection_title_var = tk.StringVar(value="请选择结构节点")
        ttk.Label(
            inspector_panel,
            textvariable=self.selection_title_var,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor=tk.W, pady=(5, 5))
        shell = ttk.Frame(inspector_panel)
        shell.pack(fill=tk.BOTH, expand=True)
        self.detail_canvas = tk.Canvas(shell, highlightthickness=0, background="#FFFFFF")
        detail_scroll = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=detail_scroll.set)
        self.detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_frame = ttk.Frame(self.detail_canvas, padding=(2, 2, 8, 12), style="InspectorGroup.TFrame")
        self.detail_window = self.detail_canvas.create_window((0, 0), window=self.detail_frame, anchor=tk.NW)
        self.detail_frame.bind("<Configure>", self._sync_detail_scrollregion)
        self.detail_canvas.bind("<Configure>", self._resize_detail_window)
        self.detail_canvas.bind("<MouseWheel>", self._on_detail_wheel)
        self._build_inspector_groups()

    def _build_inspector_groups(self) -> None:
        self.content_group = CollapsibleGroup(self.detail_frame, "内容与题型", expanded=True)
        self.font_group = CollapsibleGroup(
            self.detail_frame,
            "字体",
            expanded=True,
            reset_command=lambda: self._restore_group("font"),
        )
        self.paragraph_group = CollapsibleGroup(
            self.detail_frame,
            "段落",
            expanded=True,
            reset_command=lambda: self._restore_group("paragraph"),
        )
        self.options_group = CollapsibleGroup(
            self.detail_frame,
            "选择项",
            expanded=False,
            reset_command=lambda: self._restore_group("options"),
        )
        self.pagination_group = CollapsibleGroup(
            self.detail_frame,
            "分页",
            expanded=False,
            reset_command=lambda: self._restore_group("pagination"),
        )
        self.global_group = CollapsibleGroup(
            self.detail_frame,
            "整卷设置",
            expanded=True,
            reset_command=lambda: self._restore_group("global"),
        )
        self._all_groups = (
            self.content_group,
            self.font_group,
            self.paragraph_group,
            self.options_group,
            self.pagination_group,
            self.global_group,
        )
        self._build_content_group(self.content_group.body)
        self._build_font_group(self.font_group.body)
        self._build_paragraph_group(self.paragraph_group.body)
        self._build_options_group(self.options_group.body)
        self._build_pagination_group(self.pagination_group.body)
        self._build_global_group(self.global_group.body)
        self._show_groups({"内容与题型", "整卷设置"})

    def _build_content_group(self, parent: ttk.Frame) -> None:
        self.identity_frame = ttk.Frame(parent)
        self.identity_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(self.identity_frame, text="题型").grid(row=0, column=0, sticky=tk.W)
        self.kind_combo = ttk.Combobox(
            self.identity_frame,
            textvariable=self.kind_var,
            values=("客观题", "主观题", "结构内容"),
            state="readonly",
            width=9,
        )
        self.kind_combo.grid(row=0, column=1, sticky=tk.W, padx=(5, 12))
        ttk.Label(self.identity_frame, text="分值").grid(row=0, column=2, sticky=tk.W)
        self.score_entry = ttk.Entry(self.identity_frame, textvariable=self.score_var, width=7)
        self.score_entry.grid(row=0, column=3, sticky=tk.W, padx=(5, 0))

        ttk.Label(parent, text="内容对象").pack(anchor=tk.W)
        object_frame = ttk.Frame(parent)
        object_frame.pack(fill=tk.X, pady=(4, 6))
        self.object_tree = ttk.Treeview(
            object_frame,
            columns=("summary",),
            show="tree headings",
            height=5,
            style="Object.Treeview",
        )
        self.object_tree.heading("#0", text="对象")
        self.object_tree.heading("summary", text="内容摘要")
        self.object_tree.column("#0", width=92, stretch=False)
        self.object_tree.column("summary", width=290, stretch=True)
        object_scroll = ttk.Scrollbar(object_frame, orient=tk.VERTICAL, command=self.object_tree.yview)
        self.object_tree.configure(yscrollcommand=object_scroll.set)
        self.object_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        object_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.object_tree.bind("<<TreeviewSelect>>", self._on_content_object_select)

        ttk.Label(parent, text="选中内容").pack(anchor=tk.W)
        self.stem_text = tk.Text(
            parent,
            height=7,
            wrap=tk.WORD,
            relief=tk.SOLID,
            borderwidth=1,
            padx=8,
            pady=7,
            undo=True,
            autoseparators=True,
            maxundo=-1,
            exportselection=False,
        )
        self.stem_text.pack(fill=tk.X, pady=(4, 4))
        self.selection_hint_var = tk.StringVar(value="未选择文字时，字体设置作用于当前内容对象。")
        ttk.Label(
            parent,
            textvariable=self.selection_hint_var,
            foreground="#5B6470",
            wraplength=430,
        ).pack(anchor=tk.W)

    def _build_font_group(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="字体").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(
            parent,
            textvariable=self.font_var,
            values=FONT_CHOICES,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky=tk.EW, padx=(5, 12))
        ttk.Label(parent, text="大小").grid(row=0, column=2, sticky=tk.W)
        ttk.Spinbox(
            parent,
            textvariable=self.size_var,
            values=("9", "10.5", "12", "15", "16", "18", "22"),
            width=7,
            style="Property.TSpinbox",
        ).grid(row=0, column=3, sticky=tk.W, padx=(5, 10))
        ttk.Checkbutton(parent, text="加粗", variable=self.bold_var).grid(row=0, column=4, sticky=tk.W)
        ttk.Separator(parent).grid(row=1, column=0, columnspan=5, sticky=tk.EW, pady=7)
        ttk.Label(parent, text="批量范围").grid(row=2, column=0, sticky=tk.W)
        ttk.Combobox(
            parent,
            textvariable=self.batch_scope_var,
            values=BATCH_SCOPES,
            state="readonly",
            width=10,
        ).grid(row=2, column=1, sticky=tk.W, padx=(5, 8))
        ttk.Button(parent, text="应用到同类型", command=self._apply_same_type).grid(
            row=2, column=2, columnspan=3, sticky=tk.E
        )
        parent.columnconfigure(1, weight=1)

    def _build_paragraph_group(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="缩进", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=0, columnspan=6, sticky=tk.W, pady=(0, 5)
        )
        self._spin_row(parent, 1, "文本之前", self.left_indent_var, "字符", "文本之后", self.right_indent_var, "字符")
        ttk.Label(parent, text="特殊格式").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Combobox(
            parent,
            textvariable=self.special_indent_var,
            values=SPECIAL_INDENTS,
            state="readonly",
            width=8,
        ).grid(row=2, column=1, sticky=tk.W, padx=(5, 4), pady=(5, 0))
        ttk.Label(parent, text="度量值").grid(row=2, column=3, sticky=tk.W, padx=(12, 0), pady=(5, 0))
        ttk.Spinbox(parent, textvariable=self.special_indent_amount_var, from_=0, to=20, increment=0.1, width=7).grid(
            row=2, column=4, sticky=tk.W, padx=(5, 4), pady=(5, 0)
        )
        ttk.Label(parent, text="字符").grid(row=2, column=5, sticky=tk.W, pady=(5, 0))
        ttk.Separator(parent).grid(row=3, column=0, columnspan=6, sticky=tk.EW, pady=8)
        ttk.Label(parent, text="间距", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=4, column=0, columnspan=6, sticky=tk.W, pady=(0, 5)
        )
        self._spin_row(parent, 5, "段前", self.space_before_var, "磅", "段后", self.space_after_var, "磅")
        ttk.Label(parent, text="行距").grid(row=6, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Combobox(
            parent,
            textvariable=self.line_spacing_var,
            values=("1.0", "1.05", "1.25", "1.5", "2.0"),
            width=8,
        ).grid(row=6, column=1, sticky=tk.W, padx=(5, 4), pady=(5, 0))
        ttk.Label(parent, text="倍").grid(row=6, column=2, sticky=tk.W, pady=(5, 0))
        ttk.Label(parent, text="对齐").grid(row=6, column=3, sticky=tk.W, padx=(12, 0), pady=(5, 0))
        ttk.Combobox(
            parent,
            textvariable=self.alignment_var,
            values=ALIGNMENT_CHOICES,
            state="readonly",
            width=9,
        ).grid(row=6, column=4, columnspan=2, sticky=tk.W, padx=(5, 0), pady=(5, 0))

    @staticmethod
    def _spin_row(
        parent: ttk.Frame,
        row: int,
        label_a: str,
        variable_a: tk.StringVar,
        unit_a: str,
        label_b: str,
        variable_b: tk.StringVar,
        unit_b: str,
    ) -> None:
        ttk.Label(parent, text=label_a).grid(row=row, column=0, sticky=tk.W)
        ttk.Spinbox(parent, textvariable=variable_a, from_=0, to=40, increment=0.1, width=7).grid(
            row=row, column=1, sticky=tk.W, padx=(5, 4)
        )
        ttk.Label(parent, text=unit_a).grid(row=row, column=2, sticky=tk.W)
        ttk.Label(parent, text=label_b).grid(row=row, column=3, sticky=tk.W, padx=(12, 0))
        ttk.Spinbox(parent, textvariable=variable_b, from_=0, to=40, increment=0.1, width=7).grid(
            row=row, column=4, sticky=tk.W, padx=(5, 4)
        )
        ttk.Label(parent, text=unit_b).grid(row=row, column=5, sticky=tk.W)

    def _build_options_group(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="排列").grid(row=0, column=0, sticky=tk.W)
        self.option_combo = ttk.Combobox(
            parent,
            textvariable=self.option_layout_var,
            values=("四行单列", "两行两列"),
            state="readonly",
            width=10,
        )
        self.option_combo.grid(row=0, column=1, sticky=tk.W, padx=(5, 12))
        ttk.Label(parent, text="字体").grid(row=0, column=2, sticky=tk.W)
        ttk.Combobox(
            parent,
            textvariable=self.option_font_var,
            values=FONT_CHOICES,
            state="readonly",
            width=9,
        ).grid(row=0, column=3, sticky=tk.W, padx=(5, 0))
        self._spin_row(parent, 1, "文本之前", self.option_left_var, "字符", "悬挂", self.option_hanging_var, "字符")
        ttk.Label(parent, text="字号").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Spinbox(parent, textvariable=self.option_size_var, values=("9", "10.5", "12"), width=7).grid(
            row=2, column=1, sticky=tk.W, padx=(5, 4), pady=(5, 0)
        )

    def _build_pagination_group(self, parent: ttk.Frame) -> None:
        ttk.Checkbutton(parent, text="与下一段同页", variable=self.keep_next_var).pack(anchor=tk.W)
        ttk.Checkbutton(parent, text="当前对象前分页", variable=self.page_break_var).pack(anchor=tk.W, pady=(5, 0))

    def _build_global_group(self, parent: ttk.Frame) -> None:
        self._global_entry(parent, 0, "试卷名称", self.exam_name_var)
        self._global_entry(parent, 1, "科目名称", self.subject_name_var)
        self._global_entry(parent, 2, "试卷说明", self.exam_meta_var)
        self._global_entry(parent, 3, "目标页数", self.target_pages_var, width=8, hint="默认 8 页")
        ttk.Separator(parent).grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=8)
        ttk.Label(parent, text="页边距（毫米）", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=5, column=0, columnspan=3, sticky=tk.W, pady=(0, 5)
        )
        margins = ttk.Frame(parent)
        margins.grid(row=6, column=0, columnspan=3, sticky=tk.EW)
        for column, (label, variable) in enumerate(
            (("上", self.margin_top_var), ("下", self.margin_bottom_var), ("左", self.margin_left_var), ("右", self.margin_right_var))
        ):
            ttk.Label(margins, text=label).grid(row=0, column=column * 2, sticky=tk.W)
            ttk.Spinbox(margins, textvariable=variable, from_=5, to=45, increment=1, width=6).grid(
                row=0, column=column * 2 + 1, sticky=tk.W, padx=(3, 8)
            )
        ttk.Separator(parent).grid(row=7, column=0, columnspan=3, sticky=tk.EW, pady=8)
        ttk.Label(parent, textvariable=self.template_status_var, foreground="#4B5563", wraplength=390).grid(
            row=8, column=0, columnspan=3, sticky=tk.W
        )
        buttons = ttk.Frame(parent)
        buttons.grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
        ttk.Button(buttons, text="导入 Word 母版", command=self.import_template).pack(side=tk.LEFT)
        ttk.Button(buttons, text="使用默认预设", command=self.use_default_template).pack(side=tk.LEFT, padx=(6, 0))
        parent.columnconfigure(1, weight=1)

    @staticmethod
    def _global_entry(
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        width: int | None = None,
        hint: str = "",
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=1, sticky=tk.EW, padx=(7, 6), pady=3)
        if hint:
            ttk.Label(parent, text=hint, foreground="#6B7280").grid(row=row, column=2, sticky=tk.W, pady=3)

    def _build_preview_panel(self, parent: ttk.Frame) -> None:
        self.document_editor = ReadOnlyPreviewV04(
            parent,
            on_select=lambda _key: None,
            on_change=lambda _key, _value, _old: None,
            on_inline_format=lambda _key, _start, _end, _spec: None,
            on_undo=lambda: self.undo_action(),
            on_redo=lambda: self.redo_action(),
            status_variable=self.page_status_var,
        )
        self.document_editor.pack(fill=tk.BOTH, expand=True)
        self.canvas = self.document_editor.canvas

    def _install_live_bindings(self) -> None:
        for variable in (self.kind_var, self.score_var):
            variable.trace_add("write", lambda *_args: self._identity_changed())
        for variable in (self.font_var, self.size_var, self.bold_var):
            variable.trace_add("write", lambda *_args: self._font_changed())
        for variable in (
            self.left_indent_var,
            self.right_indent_var,
            self.special_indent_var,
            self.special_indent_amount_var,
            self.alignment_var,
            self.line_spacing_var,
            self.space_before_var,
            self.space_after_var,
        ):
            variable.trace_add("write", lambda *_args: self._paragraph_changed())
        for variable in (
            self.option_layout_var,
            self.option_font_var,
            self.option_size_var,
            self.option_left_var,
            self.option_hanging_var,
        ):
            variable.trace_add("write", lambda *_args: self._options_changed())
        for variable in (self.keep_next_var, self.page_break_var):
            variable.trace_add("write", lambda *_args: self._paragraph_changed())
        for variable in (
            self.exam_name_var,
            self.subject_name_var,
            self.exam_meta_var,
            self.target_pages_var,
            self.margin_top_var,
            self.margin_bottom_var,
            self.margin_left_var,
            self.margin_right_var,
        ):
            variable.trace_add("write", lambda *_args: self._schedule_global_commit())
        self.search_var.trace_add("write", lambda *_args: self._schedule_search())
        self.stem_text.bind("<KeyRelease>", self._schedule_text_commit)
        self.stem_text.bind("<ButtonRelease-1>", lambda _event: self.after(20, self._selection_status), add="+")
        self.score_entry.bind("<Return>", self._commit_score_entry, add="+")
        self.score_entry.bind("<FocusOut>", self._commit_score_entry, add="+")

    def _sync_detail_scrollregion(self, _event: object | None = None) -> None:
        self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all"))

    def _resize_detail_window(self, event: tk.Event) -> None:
        self.detail_canvas.itemconfigure(self.detail_window, width=max(1, int(event.width)))

    def _on_detail_wheel(self, event: tk.Event) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        self.detail_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def _show_groups(self, names: set[str]) -> None:
        for group in self._all_groups:
            group.pack_forget()
        for group in self._all_groups:
            if group.title in names:
                group.pack(fill=tk.X, pady=(0, 6))
        self.after_idle(self._sync_detail_scrollregion)

    def _populate_tree(self) -> None:
        super()._populate_tree()
        roots = self.tree.get_children("") if hasattr(self, "tree") else ()
        if not roots:
            return
        root = roots[0]
        self.root_iid = root
        root_text = "答案信息" if self.raw_exam.get("document_kind") == "answer" else "整份试卷"
        self.tree.item(root, text=root_text, open=True)
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        for child in list(self.tree.get_children(root)):
            if not child.startswith("block-"):
                continue
            index = int(child.split("-", 1)[1])
            block = self.raw_exam.get("blocks", [])[index]
            if query and query not in self._block_search_text(block).lower():
                self.tree.detach(child)
        summary = calculate_score_summary(self.raw_exam)
        missing = set(summary.missing_questions)
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            iid = f"block-{index}"
            if not self.tree.exists(iid) or block.get("type") != "question":
                continue
            number = str(block.get("question", {}).get("number", "?"))
            self.tree.item(iid, tags=("warning",) if number in missing else ())
        self._refresh_score_display(summary)

    @staticmethod
    def _block_search_text(block: dict[str, Any]) -> str:
        values = [str(value) for value in block.values() if not isinstance(value, (dict, list))]
        question = block.get("question", {})
        if isinstance(question, dict):
            values.extend(str(question.get(key, "")) for key in ("number", "stem"))
            values.extend(str(value) for value in question.get("options", []))
        values.extend(str(value) for value in block.get("paragraphs", []))
        return " ".join(values)

    def _schedule_search(self) -> None:
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(180, self._run_search)

    def _run_search(self) -> None:
        self._search_after = None
        current = self.tree.selection()
        self._populate_tree()
        if current and self.tree.exists(current[0]):
            self.tree.selection_set(current[0])
            self.tree.see(current[0])

    def _set_tree_open(self, opened: bool) -> None:
        def visit(iid: str) -> None:
            self.tree.item(iid, open=opened)
            for child in self.tree.get_children(iid):
                visit(child)

        for root in self.tree.get_children(""):
            visit(root)

    def _on_tree_select(self, _event: object | None) -> None:
        if getattr(self, "loading_fields", False):
            return
        self._commit_current_text(schedule_preview=False)
        selection = self.tree.selection()
        if not selection:
            return
        iid = str(selection[0])
        self.loading_fields = True
        try:
            if not iid.startswith("block-"):
                self.selected_block_index = None
                self.selected_block_type = "metadata"
                self.selection_title_var.set("整份试卷")
                self.identity_frame.pack_forget()
                self._content_objects = metadata_content_objects(self.raw_exam)
                self._populate_object_tree()
                self._show_groups({"内容与题型", "整卷设置"})
                self._load_global_fields()
            else:
                index = int(iid.split("-", 1)[1])
                self.selected_block_index = index
                block = self.raw_exam["blocks"][index]
                self.selected_block_type = str(block.get("type", ""))
                self.selection_title_var.set(self._block_label(block))
                is_question = block.get("type") == "question"
                if is_question:
                    question = block.get("question", {})
                    self.kind_var.set("客观题" if question.get("kind") == "objective" else "主观题")
                    score = question.get("score")
                    self.score_var.set("" if score is None else str(score).rstrip("0").rstrip("."))
                    self.option_layout_var.set("两行两列" if question.get("option_layout") == "two_column" else "四行单列")
                    self.identity_frame.pack(fill=tk.X, pady=(0, 6), before=self.object_tree.master.master.winfo_children()[1] if False else None)
                    self.kind_combo.configure(state="readonly")
                    self.score_entry.configure(state=tk.NORMAL)
                else:
                    self.identity_frame.pack_forget()
                    self.kind_var.set("结构内容")
                    self.score_var.set("")
                self._content_objects = content_objects_for_block(block, index)
                self._populate_object_tree()
                groups = {"内容与题型", "字体", "段落", "分页"}
                if is_question and block.get("question", {}).get("options"):
                    groups.add("选择项")
                self._show_groups(groups)
        finally:
            self.loading_fields = False
        if self._content_objects:
            self._select_object(self._content_objects[0].key, navigate=True)
        elif self.selected_block_index is not None:
            self._jump_to_block(self.selected_block_index)

    def _populate_object_tree(self) -> None:
        self.object_tree.delete(*self.object_tree.get_children())
        for item in self._content_objects:
            self.object_tree.insert("", tk.END, iid=item.key, text=item.label, values=(summary_text(item),))

    def _on_content_object_select(self, _event: object | None) -> None:
        selection = self.object_tree.selection()
        if selection:
            self._select_object(str(selection[0]), navigate=True)

    def _select_object(self, key: str, *, navigate: bool) -> None:
        item = next((value for value in self._content_objects if value.key == key), None)
        if item is None:
            return
        if self.selected_content_object is not None and self.selected_content_object.key != item.key:
            self._commit_current_text(schedule_preview=False)
        self.selected_content_object = item
        self.object_tree.selection_set(item.key)
        self.object_tree.focus(item.key)
        self.object_tree.see(item.key)
        self._load_content_object(item)
        if navigate and hasattr(self, "document_editor"):
            if item.key in self.document_editor.locators:
                self.after_idle(lambda: self.document_editor.scroll_to(item.key, highlight=True))
            else:
                self._pending_preview_block = item.block_index

    def _load_content_object(self, item: ContentObject) -> None:
        spec = paragraph_format_for(self.raw_exam, item)
        self.loading_fields = True
        try:
            self.stem_text.delete("1.0", tk.END)
            self.stem_text.insert("1.0", item.text)
            self.current_line_map = [{"target": item.target, "target_index": item.target_index}]
            self.current_paragraph_formats = []
            owner = format_owner(self.raw_exam, item)
            if owner is not None:
                self.current_paragraph_formats = [dict(value) for value in owner.get("paragraph_formats", [])]
            self.font_var.set(str(spec.get("font", "宋体")))
            self.size_var.set(str(spec.get("size_pt", 10.5)))
            self.bold_var.set(bool(spec.get("bold", False)))
            self.left_indent_var.set(str(spec.get("left_indent_chars", 0)))
            self.right_indent_var.set(str(spec.get("right_indent_chars", 0)))
            self.special_indent_var.set(str(spec.get("special_indent", "无")))
            self.special_indent_amount_var.set(str(spec.get("special_indent_chars", 0)))
            first = float(spec.get("first_line_indent_chars", 0) or 0)
            self.indent_var.set(str(first))
            self.alignment_var.set(str(spec.get("alignment", "左对齐")))
            self.line_spacing_var.set(str(spec.get("line_spacing", 1.25)))
            self.space_before_var.set(str(spec.get("space_before_pt", 0)))
            self.space_after_var.set(str(spec.get("space_after_pt", 0)))
            self.keep_next_var.set(bool(spec.get("keep_with_next", False)))
            self.page_break_var.set(bool(spec.get("page_break_before", False)))
            self._load_option_controls()
            self._render_object_editor_style(item, spec)
        finally:
            self.loading_fields = False
        self.selection_hint_var.set(f"当前对象：{item.label}。未选择文字时，字体设置作用于整个对象。")

    def _load_option_controls(self) -> None:
        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        question = block.get("question", {})
        spec = question.get("format", {})
        self.option_font_var.set(str(spec.get("option_font", "宋体")))
        self.option_size_var.set(str(spec.get("option_size_pt", 10.5)))
        self.option_left_var.set(str(spec.get("option_left_indent_chars", 1.5)))
        self.option_hanging_var.set(str(spec.get("option_hanging_indent_chars", 1.7)))

    def _render_object_editor_style(self, item: ContentObject, spec: dict[str, Any]) -> None:
        for tag in list(self.stem_text.tag_names()):
            if tag.startswith(("object_", "inlinefmt_", "semantic_")):
                self.stem_text.tag_delete(tag)
        font = str(spec.get("font", "宋体"))
        size = max(7, int(round(float(spec.get("size_pt", 10.5)))))
        weight = "bold" if spec.get("bold") else "normal"
        self.stem_text.configure(font=(font, size, weight))
        self.inline_tag_specs = {}
        for index, entry in enumerate(inline_formats_for(self.raw_exam, item)):
            name = f"object_inline_{index}"
            entry_font = str(entry.get("font", font))
            entry_size = max(7, int(round(float(entry.get("size_pt", size)))))
            entry_weight = "bold" if entry.get("bold") else "normal"
            self.stem_text.tag_configure(name, font=(entry_font, entry_size, entry_weight))
            self.stem_text.tag_add(name, f"1.0+{int(entry.get('start', 0))}c", f"1.0+{int(entry.get('end', 0))}c")
        if item.role == "segmentation":
            text = self.stem_text.get("1.0", "end-1c")
            for index, character in enumerate(text):
                if character in "ABCDEFGH":
                    name = f"object_marker_{index}"
                    self.stem_text.tag_configure(name, font=("宋体", size), relief=tk.SOLID, borderwidth=1)
                    self.stem_text.tag_add(name, f"1.0+{index}c", f"1.0+{index + 1}c")
        self.stem_text.tag_raise(tk.SEL)

    def _schedule_text_commit(self, _event: object | None = None) -> None:
        if self.loading_fields or self.selected_content_object is None:
            return
        if self._text_commit_after:
            self.after_cancel(self._text_commit_after)
        self.status_var.set("文字已修改，正在等待同步…")
        self._text_commit_after = self.after(600, self._commit_text_after_delay)

    def _commit_text_after_delay(self) -> None:
        self._text_commit_after = None
        self._commit_current_text(schedule_preview=True)

    def _commit_current_text(self, *, schedule_preview: bool) -> bool:
        item = self.selected_content_object
        if item is None or self.loading_fields:
            return False
        value = self.stem_text.get("1.0", "end-1c")
        if value == item.text:
            return False
        self._push_direct_history()
        set_content_object_text(self.raw_exam, item, value)
        updated = ContentObject(
            item.key,
            item.block_index,
            item.target,
            item.target_index,
            item.role,
            item.label,
            value,
            item.line_index,
        )
        self.selected_content_object = updated
        self._content_objects = [updated if current.key == item.key else current for current in self._content_objects]
        if self.object_tree.exists(item.key):
            self.object_tree.item(item.key, values=(summary_text(updated),))
        self._update_tree_block_label(item.block_index)
        self.status_var.set("内容已同步，正在更新预览。")
        if schedule_preview:
            self._schedule_canvas_preview()
        return True

    def _identity_changed(self) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        score_text = self.score_var.get().strip()
        if score_text and parse_score(score_text) is None:
            self.warning_var.set("当前分值格式需要检查，分值可留空后继续编辑。")
            return
        self._push_direct_history()
        question = block["question"]
        question["kind"] = "objective" if self.kind_var.get() == "客观题" else "subjective"
        question["score"] = None if not score_text else float(parse_score(score_text) or Decimal("0"))
        self._update_tree_block_label(self.selected_block_index)
        self._refresh_score_display()
        self._schedule_canvas_preview()

    def _font_changed(self) -> None:
        if self.loading_fields or self.selected_content_object is None:
            return
        try:
            font_spec = {
                "font": self.font_var.get(),
                "size_pt": float(self.size_var.get()),
                "bold": self.bold_var.get(),
            }
        except ValueError:
            return
        self._push_direct_history()
        ranges = self.stem_text.tag_ranges(tk.SEL)
        if ranges:
            start = int(self.stem_text.count("1.0", ranges[0], "chars")[0])
            end = int(self.stem_text.count("1.0", ranges[1], "chars")[0])
            set_inline_format(self.raw_exam, self.selected_content_object, start, end, font_spec)
            self.selection_hint_var.set("字体已应用到选中文字。")
        else:
            spec = self._format_spec_from_controls()
            set_paragraph_format(self.raw_exam, self.selected_content_object, spec)
            self.selection_hint_var.set("字体已应用到当前内容对象。")
        self._render_object_editor_style(
            self.selected_content_object,
            paragraph_format_for(self.raw_exam, self.selected_content_object),
        )
        self._schedule_canvas_preview()

    def _paragraph_changed(self) -> None:
        if self.loading_fields or self.selected_content_object is None:
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError:
            return
        self._push_direct_history()
        set_paragraph_format(self.raw_exam, self.selected_content_object, spec)
        self._schedule_canvas_preview()

    def _format_spec_from_controls(self) -> dict[str, Any]:
        special = self.special_indent_var.get()
        amount = float(self.special_indent_amount_var.get())
        first = amount if special == "首行" else (-amount if special == "悬挂" else 0.0)
        return {
            "font": self.font_var.get(),
            "size_pt": float(self.size_var.get()),
            "bold": self.bold_var.get(),
            "left_indent_chars": float(self.left_indent_var.get()),
            "right_indent_chars": float(self.right_indent_var.get()),
            "special_indent": special,
            "special_indent_chars": amount,
            "first_line_indent_chars": first,
            "alignment": self.alignment_var.get(),
            "line_spacing": float(self.line_spacing_var.get()),
            "space_before_pt": float(self.space_before_var.get()),
            "space_after_pt": float(self.space_after_var.get()),
            "keep_with_next": self.keep_next_var.get(),
            "page_break_before": self.page_break_var.get(),
        }

    def _options_changed(self) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        try:
            size = float(self.option_size_var.get())
            left = float(self.option_left_var.get())
            hanging = float(self.option_hanging_var.get())
        except ValueError:
            return
        self._push_direct_history()
        question = block["question"]
        question["option_layout"] = "two_column" if self.option_layout_var.get() == "两行两列" else "vertical"
        question.setdefault("format", {}).update(
            {
                "option_font": self.option_font_var.get(),
                "option_size_pt": size,
                "option_left_indent_chars": left,
                "option_hanging_indent_chars": hanging,
            }
        )
        self._schedule_canvas_preview()

    def _apply_same_type(self) -> None:
        item = self.selected_content_object
        if item is None:
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError:
            messagebox.showerror(APP_TITLE, "格式参数需要填写数字。", parent=self)
            return
        targets = objects_in_scope(self.raw_exam, item, self.batch_scope_var.get())
        self._push_direct_history()
        for target in targets:
            set_paragraph_format(self.raw_exam, target, spec)
        self.status_var.set(f"已将格式应用到 {len(targets)} 个同类型内容对象。")
        self._schedule_canvas_preview()

    def _restore_group(self, group: str) -> None:
        if group == "global":
            layout = load_layout(self.layout_path)
            page = layout.get("page", {})
            self.loading_fields = True
            try:
                self.target_pages_var.set("8")
                self.margin_top_var.set(str(page.get("margin_top_mm", 20)))
                self.margin_bottom_var.set(str(page.get("margin_bottom_mm", 18)))
                self.margin_left_var.set(str(page.get("margin_left_mm", 22)))
                self.margin_right_var.set(str(page.get("margin_right_mm", 18)))
            finally:
                self.loading_fields = False
            self._commit_global_settings(show_error=False)
            return
        item = self.selected_content_object
        if item is None:
            return
        self._push_direct_history()
        remove_paragraph_format(self.raw_exam, item)
        owner = format_owner(self.raw_exam, item)
        if owner is not None and item.role == "question_stem":
            keys = (
                ("font", "size_pt", "bold")
                if group == "font"
                else (
                    "left_indent_chars",
                    "right_indent_chars",
                    "special_indent",
                    "special_indent_chars",
                    "first_line_indent_chars",
                    "alignment",
                    "line_spacing",
                    "space_before_pt",
                    "space_after_pt",
                    "keep_with_next",
                    "page_break_before",
                )
            )
            for key in keys:
                owner.setdefault("format", {}).pop(key, None)
        if owner is not None and group == "options":
            for key in (
                "option_font",
                "option_size_pt",
                "option_left_indent_chars",
                "option_hanging_indent_chars",
            ):
                owner.setdefault("format", {}).pop(key, None)
            owner["option_layout"] = "vertical"
        self._load_content_object(item)
        self.status_var.set("已恢复当前分组的模板值。")
        self._schedule_canvas_preview()

    def _schedule_global_commit(self) -> None:
        if self.loading_fields:
            return
        if self._global_commit_after:
            self.after_cancel(self._global_commit_after)
        self._global_commit_after = self.after(600, lambda: self._commit_global_settings(show_error=False))

    def _commit_global_settings(self, *, show_error: bool) -> bool:
        self._global_commit_after = None
        try:
            target_pages = int(self.target_pages_var.get().strip())
            margins = {
                "margin_top_mm": float(self.margin_top_var.get()),
                "margin_bottom_mm": float(self.margin_bottom_var.get()),
                "margin_left_mm": float(self.margin_left_var.get()),
                "margin_right_mm": float(self.margin_right_var.get()),
            }
            if not 1 <= target_pages <= 32:
                raise ValueError("目标页数应在 1 到 32 页之间。")
            if any(not 5 <= value <= 45 for value in margins.values()):
                raise ValueError("页边距应在 5 到 45 毫米之间。")
        except ValueError as exc:
            if show_error:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return False
        metadata = self.raw_exam.setdefault("metadata", {})
        metadata["exam_name"] = self.exam_name_var.get().strip()
        metadata["subject_name"] = self.subject_name_var.get().strip()
        metadata["meta_text"] = self.exam_meta_var.get().strip()
        metadata["total_score"] = float(TARGET_SCORE)
        metadata["target_pages"] = target_pages
        metadata["page_overrides"] = margins
        self.status_var.set("整卷设置已同步。")
        self._schedule_canvas_preview()
        return True

    def apply_global_settings(self) -> None:
        self._commit_global_settings(show_error=True)

    def _load_global_fields(self) -> None:
        super()._load_global_fields()
        metadata = self.raw_exam.get("metadata", {})
        page = load_layout(self.layout_path).get("page", {})
        overrides = metadata.get("page_overrides", {})
        self.loading_fields = True
        try:
            self.target_pages_var.set(str(metadata.get("target_pages", 8)))
            self.margin_top_var.set(str(overrides.get("margin_top_mm", page.get("margin_top_mm", 20))))
            self.margin_bottom_var.set(str(overrides.get("margin_bottom_mm", page.get("margin_bottom_mm", 18))))
            self.margin_left_var.set(str(overrides.get("margin_left_mm", page.get("margin_left_mm", 22))))
            self.margin_right_var.set(str(overrides.get("margin_right_mm", page.get("margin_right_mm", 18))))
        finally:
            self.loading_fields = False

    def apply_current_question(self, *, silent: bool = False) -> bool:
        self._commit_current_text(schedule_preview=False)
        if self.selected_block_index is None:
            return True
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return True
        score_text = self.score_var.get().strip()
        if score_text and parse_score(score_text) is None:
            if not silent:
                messagebox.showerror(APP_TITLE, "分值需要填写非负数字，也可以暂时留空。", parent=self)
            return False
        question = block["question"]
        question["kind"] = "objective" if self.kind_var.get() == "客观题" else "subjective"
        question["score"] = None if not score_text else float(parse_score(score_text) or Decimal("0"))
        question["option_layout"] = "two_column" if self.option_layout_var.get() == "两行两列" else "vertical"
        self.raw_exam.setdefault("metadata", {})["total_score"] = float(TARGET_SCORE)
        self._refresh_score_display()
        return True

    def _commit_score_entry(self, _event: object | None = None) -> str:
        if self.score_var.get().strip() and parse_score(self.score_var.get()) is None:
            self.warning_var.set("当前题分值格式需要检查，分值可留空后继续编辑。")
            return "break"
        self.apply_current_question(silent=True)
        self._populate_tree()
        self._schedule_canvas_preview()
        return "break"

    def _update_tree_block_label(self, block_index: int | None) -> None:
        if block_index is None:
            return
        iid = f"block-{block_index}"
        if not self.tree.exists(iid):
            return
        block = self.raw_exam["blocks"][block_index]
        if block.get("type") == "question":
            question = block["question"]
            stem = str(question.get("stem", ""))
            kind = "客观" if question.get("kind") == "objective" else "主观"
            score = question.get("score")
            self.tree.item(
                iid,
                text=f"{question.get('number')}．{stem[:18]}",
                values=(kind, "" if score is None else score),
            )

    def _refresh_score_display(self, summary: ScoreSummary | None = None) -> None:
        if not hasattr(self, "score_total_label"):
            return
        state = summary or calculate_score_summary(self.raw_exam)
        self.score_total_label.configure(text=f"当前 {format_score(state.total)} / 150 分")
        delta = self._score_difference_text(state)
        if state.missing_questions:
            delta += f"，{len(state.missing_questions)} 题待填"
        color = "#107C10" if state.complete else ("#C42B1C" if state.difference < 0 else "#8A5700")
        self.score_total_label.configure(foreground=color)
        self.score_delta_label.configure(text=delta, foreground=color)
        self._refresh_warning_banner(state)

    def _refresh_warning_banner(self, summary: ScoreSummary) -> None:
        if self._warning_refreshing or not hasattr(self, "warning_var"):
            return
        self._warning_refreshing = True
        try:
            warnings: list[str] = []
            if summary.missing_questions:
                warnings.append("待填写分值：" + "、".join(summary.missing_questions))
            question_count = sum(1 for block in self.raw_exam.get("blocks", []) if block.get("type") == "question")
            if self.raw_exam.get("document_kind") != "answer" and question_count != 23:
                warnings.append(f"当前识别 {question_count} 道题，仍可继续编辑和导出")
            diagnostics = self.raw_exam.get("diagnostics", [])
            if diagnostics:
                warnings.append(f"另有 {len(diagnostics)} 条识别提示")
            self.warning_var.set("；".join(warnings) if warnings else "结构与分值检查暂无提醒")
        finally:
            self._warning_refreshing = False

    def _schedule_canvas_preview(self) -> None:
        if self._canvas_preview_after:
            self.after_cancel(self._canvas_preview_after)
        self._canvas_preview_after = self.after(260, self._run_canvas_preview)

    def _finish_preview(self, result: object) -> None:
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        if result.generation != self._preview_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        object_locators = build_object_locators(self.raw_exam, result.locators)
        self._preview_block_locators = dict(result.locators)
        self.preview_pages = list(result.pages)
        self.preview_page_index = min(self.preview_page_index, max(0, len(self.preview_pages) - 1))
        selected_key: object = self.selected_block_index
        if self.selected_content_object is not None:
            selected_key = self.selected_content_object.key
        self.document_editor.set_preview_pages(
            self.preview_pages,
            locators=object_locators,
            actual_pages=result.actual_pages,
            raw_exam=self.raw_exam,
            selected_key=selected_key,
        )
        self.status_var.set(f"预览已更新：实际 {result.actual_pages} 页，目标 {result.target_pages} 页")
        if self.selected_content_object is not None:
            self.after(50, lambda: self.document_editor.scroll_to(self.selected_content_object.key, highlight=False))

    def _jump_to_block(self, block_index: int) -> None:
        key: object = block_index
        if self.selected_content_object is not None and self.selected_content_object.block_index == block_index:
            key = self.selected_content_object.key
        if hasattr(self, "document_editor"):
            self._pending_preview_block = None
            self.document_editor.scroll_to(key, highlight=True)

    def _render_editable_document(self) -> None:
        return

    def _apply_editor_visual_styles(self) -> None:
        return

    def _cursor_style_event(self, _event: tk.Event) -> None:
        self.after(20, self._selection_status)

    def _selection_status(self) -> None:
        if self.stem_text.tag_ranges(tk.SEL):
            self.selection_hint_var.set("已选择文字，字体、字号和加粗只作用于选中文字。")
        elif self.selected_content_object is not None:
            self.selection_hint_var.set(f"当前对象：{self.selected_content_object.label}。段落设置作用于整个对象。")

    def undo_action(self, _event: object | None = None) -> str:
        result = super().undo_action(_event)
        self._restore_v04_selection()
        return result

    def redo_action(self, _event: object | None = None) -> str:
        result = super().redo_action(_event)
        self._restore_v04_selection()
        return result

    def _restore_v04_selection(self) -> None:
        key = self.selected_content_object.key if self.selected_content_object else None
        if self.selected_block_index is not None and self.tree.exists(f"block-{self.selected_block_index}"):
            self.tree.selection_set(f"block-{self.selected_block_index}")
            self._on_tree_select(None)
            if key and self.object_tree.exists(key):
                self._select_object(key, navigate=False)
        self._refresh_score_display()

    def _import_exam_path(self, path: Path) -> None:
        super()._import_exam_path(path)
        self.selected_content_object = None
        self._refresh_score_display()

    def open_export_dialog(self) -> None:
        self.apply_current_question(silent=True)
        summary = calculate_score_summary(self.raw_exam)
        lines: list[str] = []
        if summary.missing_questions:
            lines.append("未填写分值：" + "、".join(summary.missing_questions))
        if summary.total != TARGET_SCORE:
            lines.append(self._score_difference_text(summary))
        question_count = sum(1 for block in self.raw_exam.get("blocks", []) if block.get("type") == "question")
        if self.raw_exam.get("document_kind") != "answer" and question_count != 23:
            lines.append(f"当前识别 {question_count} 道题，与默认模板题量不同")
        diagnostics = self.raw_exam.get("diagnostics", [])
        if diagnostics:
            lines.append(f"存在 {len(diagnostics)} 条识别提示")
        if lines:
            dialog = ExportWarningDialog(self, lines)
            self.wait_window(dialog)
            if not dialog.result:
                return
        current_v02.CurrentDesktopApp.open_export_dialog(self)

    def _restore_editor_sash(self) -> None:
        if self._ui_sash_initialized or not hasattr(self, "editor_pane"):
            return
        self._ui_sash_initialized = True
        self.update_idletasks()
        height = max(1, self.editor_pane.winfo_height())
        position = int(height * 0.35)
        settings = self._read_ui_settings()
        try:
            position = int(settings.get("left_splitter", position))
        except (TypeError, ValueError):
            pass
        try:
            self.editor_pane.sashpos(0, max(150, min(height - 220, position)))
        except tk.TclError:
            return

    def _save_editor_sash(self, _event: object | None = None) -> None:
        if not hasattr(self, "editor_pane"):
            return
        try:
            position = int(self.editor_pane.sashpos(0))
        except tk.TclError:
            return
        settings = self._read_ui_settings()
        settings["left_splitter"] = position
        path = self._ui_settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    @staticmethod
    def _ui_settings_path() -> Path:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return root / "SuoyiExamTypesetter" / "ui-v04.json"

    def _read_ui_settings(self) -> dict[str, Any]:
        path = self._ui_settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
