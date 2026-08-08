"""高中语文试卷智能排版系统桌面工作台。"""

from __future__ import annotations

from copy import deepcopy
import argparse
import json
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk
import pypdfium2 as pdfium

from app.config import load_layout
from app.editor_importers import import_exam, save_exam
from app.exporters import PdfExporter
from app.models import ExamDocument
from app.pagination import apply_pagination_guards
from app.question_overrides import apply_question_overrides
from app.renderers import DocxRenderer
from app.validators import check_required_fonts, validate_exam


APP_TITLE = "高中语文试卷智能排版工作台"
VERSION = "0.3.0"
FONT_CHOICES = ("宋体", "黑体", "楷体", "仿宋")
ALIGNMENT_CHOICES = ("左对齐", "居中", "右对齐", "两端对齐")


def resource_path(relative: str) -> Path:
    """定位开发目录或 PyInstaller 目录中的资源。"""

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def build_documents(
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
    """从编辑器数据生成 DOCX 和 PDF。"""

    layout = load_layout(layout_path)
    exam = ExamDocument.from_dict(raw_exam)
    issues = validate_exam(exam)
    errors = [issue.message for issue in issues if issue.severity == "error"]
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
    apply_question_overrides(docx_work, raw_exam)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path = output_dir / f"{basename}.pdf"
        pdf_path, engine = PdfExporter().export(docx_work, pdf_path)
    return (docx_work if export_docx else None), pdf_path, engine


def rasterize_pdf(pdf_path: Path, output_dir: Path) -> list[Path]:
    """把 PDF 转成预览页面。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(pdf_path)
    pages: list[Path] = []
    for index in range(len(document)):
        page = document[index]
        bitmap = page.render(scale=1.65)
        target = output_dir / f"page-{index + 1}.png"
        bitmap.to_pil().save(target)
        pages.append(target)
    return pages


class ExportDialog(tk.Toplevel):
    """导出设置窗口。"""

    def __init__(self, parent: "DesktopApp") -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("导出试卷")
        self.geometry("560x310")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.output_var = tk.StringVar(
            value=str(Path.home() / "Documents" / "语文试卷输出")
        )
        default_name = str(
            parent.raw_exam.get("metadata", {}).get("exam_name", "高中语文试卷")
        )
        self.name_var = tk.StringVar(value=_safe_filename(default_name))
        self.docx_var = tk.BooleanVar(value=True)
        self.pdf_var = tk.BooleanVar(value=True)
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=22)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="导出设置", style="DialogTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 18)
        )
        ttk.Label(frame, text="文件名称").grid(row=1, column=0, sticky=tk.W, pady=7)
        ttk.Entry(frame, textvariable=self.name_var).grid(
            row=1, column=1, columnspan=2, sticky=tk.EW, pady=7
        )
        ttk.Label(frame, text="输出目录").grid(row=2, column=0, sticky=tk.W, pady=7)
        ttk.Entry(frame, textvariable=self.output_var).grid(
            row=2, column=1, sticky=tk.EW, pady=7
        )
        ttk.Button(frame, text="选择", command=self._choose_output).grid(
            row=2, column=2, padx=(8, 0), pady=7
        )
        options = ttk.Frame(frame)
        options.grid(row=3, column=1, columnspan=2, sticky=tk.W, pady=10)
        ttk.Checkbutton(options, text="Word DOCX", variable=self.docx_var).pack(
            side=tk.LEFT
        )
        ttk.Checkbutton(options, text="PDF", variable=self.pdf_var).pack(
            side=tk.LEFT, padx=(24, 0)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky=tk.E, pady=(22, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.LEFT)
        ttk.Button(
            buttons, text="开始导出", style="Primary.TButton", command=self._submit
        ).pack(side=tk.LEFT, padx=(10, 0))
        frame.columnconfigure(1, weight=1)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(parent=self)
        if path:
            self.output_var.set(path)

    def _submit(self) -> None:
        if not self.docx_var.get() and not self.pdf_var.get():
            messagebox.showerror(APP_TITLE, "请至少选择一种导出格式。", parent=self)
            return
        name = _safe_filename(self.name_var.get().strip())
        if not name:
            messagebox.showerror(APP_TITLE, "请输入文件名称。", parent=self)
            return
        self.destroy()
        self.parent.start_export(
            Path(self.output_var.get()),
            name,
            self.docx_var.get(),
            self.pdf_var.get(),
        )


class DesktopApp(tk.Tk):
    """试题结构编辑、格式设置、预览与导出工作台。"""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} v{VERSION}")
        self.geometry("1380x840")
        self.minsize(1120, 700)
        self._setup_styles()

        self.temp_context = tempfile.TemporaryDirectory(prefix="exam_typesetter_")
        self.temp_dir = Path(self.temp_context.name)
        self.layout_path = resource_path("templates/layout.yaml")
        self.template_path: Path | None = None
        self.current_exam_path: Path | None = None
        self.raw_exam = json.loads(
            resource_path("samples/exam.json").read_text(encoding="utf-8")
        )
        self.selected_block_index: int | None = None
        self.preview_pages: list[Path] = []
        self.preview_page_index = 0
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.zoom = 0.82
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False

        self.status_var = tk.StringVar(value="已载入全国卷默认预设。")
        self.template_status_var = tk.StringVar(value="当前版式：全国卷默认预设")
        self.page_status_var = tk.StringVar(value="尚未生成预览")

        self._create_variables()
        self._build_menu()
        self._build_ui()
        self._populate_tree()
        self._load_global_fields()
        self.after(200, self._poll_messages)
        self.after(700, self.request_preview)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Microsoft YaHei UI", 9))
        style.configure("Toolbar.TFrame", background="#F4F5F7")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("DialogTitle.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Status.TLabel", background="#F4F5F7", foreground="#444444")
        style.configure("Treeview", rowheight=27)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _create_variables(self) -> None:
        self.kind_var = tk.StringVar()
        self.score_var = tk.StringVar()
        self.option_layout_var = tk.StringVar()
        self.font_var = tk.StringVar()
        self.size_var = tk.StringVar()
        self.indent_var = tk.StringVar()
        self.alignment_var = tk.StringVar()
        self.line_spacing_var = tk.StringVar()
        self.space_before_var = tk.StringVar()
        self.space_after_var = tk.StringVar()
        self.keep_next_var = tk.BooleanVar()
        self.page_break_var = tk.BooleanVar()
        self.option_font_var = tk.StringVar()
        self.option_size_var = tk.StringVar()
        self.option_left_var = tk.StringVar()
        self.option_hanging_var = tk.StringVar()

        self.exam_name_var = tk.StringVar()
        self.subject_name_var = tk.StringVar()
        self.exam_meta_var = tk.StringVar()
        self.total_score_var = tk.StringVar()

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="导入新试题", command=self.import_new_exam)
        file_menu.add_command(label="保存结构化项目", command=self.save_project)
        file_menu.add_separator()
        file_menu.add_command(label="导入 Word 母版", command=self.import_template)
        file_menu.add_command(label="使用默认预设", command=self.use_default_template)
        file_menu.add_separator()
        file_menu.add_command(label="导出", command=self.open_export_dialog)
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)
        preview_menu = tk.Menu(menu, tearoff=False)
        preview_menu.add_command(label="刷新预览", command=self.request_preview)
        preview_menu.add_command(label="上一页", command=self.previous_page)
        preview_menu.add_command(label="下一页", command=self.next_page)
        menu.add_cascade(label="预览", menu=preview_menu)
        self.config(menu=menu)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(12, 9))
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text=APP_TITLE, style="Title.TLabel").pack(
            side=tk.LEFT, padx=(0, 20)
        )
        self._toolbar_button(toolbar, "导入试题", self.import_new_exam)
        self._toolbar_button(toolbar, "保存项目", self.save_project)
        self._toolbar_button(toolbar, "导入母版", self.import_template)
        self._toolbar_button(toolbar, "默认预设", self.use_default_template)
        self._toolbar_button(toolbar, "刷新预览", self.request_preview, primary=True)
        self._toolbar_button(toolbar, "导出", self.open_export_dialog, primary=True)
        ttk.Label(toolbar, textvariable=self.template_status_var).pack(
            side=tk.RIGHT, padx=(12, 4)
        )

        main = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main, padding=(12, 12, 8, 8))
        right = ttk.Frame(main, padding=(8, 12, 12, 8))
        main.add(left, weight=4)
        main.add(right, weight=7)

        self._build_editor_panel(left)
        self._build_preview_panel(right)

        status = ttk.Frame(self, style="Toolbar.TFrame", padding=(12, 7))
        status.pack(fill=tk.X)
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(
            side=tk.LEFT
        )
        self.busy_bar = ttk.Progressbar(status, mode="indeterminate", length=150)
        self.busy_bar.pack(side=tk.RIGHT)

    def _toolbar_button(
        self, parent: ttk.Frame, text: str, command: object, *, primary: bool = False
    ) -> None:
        ttk.Button(
            parent,
            text=text,
            command=command,
            style="Primary.TButton" if primary else "TButton",
        ).pack(side=tk.LEFT, padx=4)

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="试卷结构").pack(anchor=tk.W)
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=False, pady=(6, 10))
        self.tree = ttk.Treeview(
            tree_frame, columns=("kind", "score"), show="tree headings", height=12
        )
        self.tree.heading("#0", text="内容")
        self.tree.heading("kind", text="题型")
        self.tree.heading("score", text="分值")
        self.tree.column("#0", width=255, stretch=True)
        self.tree.column("kind", width=62, anchor=tk.CENTER)
        self.tree.column("score", width=48, anchor=tk.CENTER)
        tree_scroll = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self.editor_tabs = ttk.Notebook(parent)
        self.editor_tabs.pack(fill=tk.BOTH, expand=True)
        self.content_tab = ttk.Frame(self.editor_tabs, padding=12)
        self.format_tab = ttk.Frame(self.editor_tabs, padding=12)
        self.global_tab = ttk.Frame(self.editor_tabs, padding=12)
        self.editor_tabs.add(self.content_tab, text="题目内容")
        self.editor_tabs.add(self.format_tab, text="逐题格式")
        self.editor_tabs.add(self.global_tab, text="卷面设置")
        self._build_content_tab()
        self._build_format_tab()
        self._build_global_tab()

    def _build_content_tab(self) -> None:
        tab = self.content_tab
        ttk.Label(tab, text="题型").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(
            tab,
            textvariable=self.kind_var,
            values=("客观题", "主观题"),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(tab, text="分值").grid(row=0, column=2, sticky=tk.W, padx=(18, 0))
        ttk.Entry(tab, textvariable=self.score_var, width=9).grid(
            row=0, column=3, sticky=tk.W
        )
        ttk.Label(tab, text="选项排列").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(
            tab,
            textvariable=self.option_layout_var,
            values=("四行单列", "两行两列"),
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(tab, text="题干").grid(
            row=2, column=0, columnspan=4, sticky=tk.W, pady=(10, 5)
        )
        self.stem_text = tk.Text(
            tab,
            height=7,
            wrap=tk.WORD,
            font=("SimSun", 10),
            relief=tk.SOLID,
            borderwidth=1,
            padx=8,
            pady=8,
        )
        self.stem_text.grid(row=3, column=0, columnspan=4, sticky=tk.NSEW)
        ttk.Button(
            tab,
            text="应用内容修改",
            style="Primary.TButton",
            command=self.apply_current_question,
        ).grid(row=4, column=0, columnspan=4, sticky=tk.E, pady=(10, 0))
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(3, weight=1)
        tab.rowconfigure(3, weight=1)

    def _build_format_tab(self) -> None:
        tab = self.format_tab
        self._combo_row(tab, 0, "题干字体", self.font_var, FONT_CHOICES)
        self._entry_row(tab, 1, "题干字号", self.size_var, "五号为 10.5")
        self._entry_row(tab, 2, "首行缩进", self.indent_var, "单位：字符")
        self._combo_row(tab, 3, "对齐方式", self.alignment_var, ALIGNMENT_CHOICES)
        self._entry_row(tab, 4, "行距倍数", self.line_spacing_var, "例如 1.25")
        self._entry_row(tab, 5, "段前间距", self.space_before_var, "单位：磅")
        self._entry_row(tab, 6, "段后间距", self.space_after_var, "单位：磅")

        ttk.Separator(tab).grid(row=7, column=0, columnspan=3, sticky=tk.EW, pady=10)
        self._combo_row(tab, 8, "选项字体", self.option_font_var, FONT_CHOICES)
        self._entry_row(tab, 9, "选项字号", self.option_size_var, "五号为 10.5")
        self._entry_row(tab, 10, "选项左缩进", self.option_left_var, "单位：字符")
        self._entry_row(tab, 11, "选项悬挂", self.option_hanging_var, "单位：字符")

        flags = ttk.Frame(tab)
        flags.grid(row=12, column=0, columnspan=3, sticky=tk.W, pady=(10, 4))
        ttk.Checkbutton(
            flags, text="与下一段同页", variable=self.keep_next_var
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            flags, text="题目前强制分页", variable=self.page_break_var
        ).pack(side=tk.LEFT, padx=(20, 0))
        ttk.Button(
            tab,
            text="应用格式并刷新预览",
            style="Primary.TButton",
            command=self.apply_format_and_preview,
        ).grid(row=13, column=0, columnspan=3, sticky=tk.E, pady=(10, 0))
        tab.columnconfigure(1, weight=1)

    def _build_global_tab(self) -> None:
        tab = self.global_tab
        self._entry_row(tab, 0, "试卷名称", self.exam_name_var, "")
        self._entry_row(tab, 1, "科目名称", self.subject_name_var, "")
        self._entry_row(tab, 2, "试卷说明", self.exam_meta_var, "")
        self._entry_row(tab, 3, "卷首总分", self.total_score_var, "")
        ttk.Separator(tab).grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=12)
        ttk.Label(
            tab,
            text="未选择母版时，系统使用全国卷默认预设。导入母版后，页边距、页眉页脚和已有版式结构从母版继承。",
            wraplength=420,
        ).grid(row=5, column=0, columnspan=3, sticky=tk.W)
        buttons = ttk.Frame(tab)
        buttons.grid(row=6, column=0, columnspan=3, sticky=tk.E, pady=(16, 0))
        ttk.Button(buttons, text="导入母版", command=self.import_template).pack(
            side=tk.LEFT
        )
        ttk.Button(buttons, text="恢复默认预设", command=self.use_default_template).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(
            buttons,
            text="应用卷面设置",
            style="Primary.TButton",
            command=self.apply_global_settings,
        ).pack(side=tk.LEFT, padx=(8, 0))
        tab.columnconfigure(1, weight=1)

    def _combo_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(
            parent, textvariable=variable, values=values, state="readonly"
        ).grid(row=row, column=1, sticky=tk.EW, pady=4)

    def _entry_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        hint: str,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=4)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky=tk.EW, pady=4
        )
        if hint:
            ttk.Label(parent, text=hint, foreground="#777777").grid(
                row=row, column=2, sticky=tk.W, padx=(8, 0), pady=4
            )

    def _build_preview_panel(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(bar, text="页面预览", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(bar, text="上一页", command=self.previous_page).pack(
            side=tk.LEFT, padx=(20, 4)
        )
        ttk.Button(bar, text="下一页", command=self.next_page).pack(side=tk.LEFT)
        ttk.Button(bar, text="缩小", command=lambda: self.change_zoom(-0.1)).pack(
            side=tk.LEFT, padx=(18, 4)
        )
        ttk.Button(bar, text="放大", command=lambda: self.change_zoom(0.1)).pack(
            side=tk.LEFT
        )
        ttk.Button(
            bar, text="刷新", style="Primary.TButton", command=self.request_preview
        ).pack(side=tk.LEFT, padx=(18, 0))
        ttk.Label(bar, textvariable=self.page_status_var).pack(side=tk.RIGHT)

        preview_frame = ttk.Frame(parent, relief=tk.SUNKEN, borderwidth=1)
        preview_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(
            preview_frame,
            background="#D9DDE3",
            highlightthickness=0,
        )
        x_scroll = ttk.Scrollbar(
            preview_frame, orient=tk.HORIZONTAL, command=self.canvas.xview
        )
        y_scroll = ttk.Scrollbar(
            preview_frame, orient=tk.VERTICAL, command=self.canvas.yview
        )
        self.canvas.configure(
            xscrollcommand=x_scroll.set,
            yscrollcommand=y_scroll.set,
        )
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)
        y_scroll.grid(row=0, column=1, sticky=tk.NS)
        x_scroll.grid(row=1, column=0, sticky=tk.EW)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.canvas.bind("<Configure>", lambda _event: self._show_current_page())
        self.canvas.create_text(
            380,
            260,
            text="正在准备默认预览……",
            fill="#555555",
            font=("Microsoft YaHei UI", 12),
        )

    def _populate_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        root = self.tree.insert("", tk.END, text="试卷信息", open=True, values=("", ""))
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            block_type = block.get("type")
            if block_type == "section_title":
                self.tree.insert(
                    root,
                    tk.END,
                    iid=f"block-{index}",
                    text=block.get("text", ""),
                    values=("章节", ""),
                )
            elif block_type == "subsection":
                self.tree.insert(
                    root,
                    tk.END,
                    iid=f"block-{index}",
                    text=block.get("name", ""),
                    values=("模块", ""),
                )
            elif block_type == "material":
                title = block.get("title") or "阅读材料"
                self.tree.insert(
                    root,
                    tk.END,
                    iid=f"block-{index}",
                    text=title,
                    values=("材料", ""),
                )
            elif block_type == "poetry":
                self.tree.insert(
                    root,
                    tk.END,
                    iid=f"block-{index}",
                    text=block.get("title", "诗歌"),
                    values=("诗歌", ""),
                )
            elif block_type == "question":
                question = block.get("question", {})
                kind = "客观" if question.get("kind") == "objective" else "主观"
                score = question.get("score")
                stem = str(question.get("stem", ""))
                label = f"{question.get('number')}．{stem[:18]}"
                self.tree.insert(
                    root,
                    tk.END,
                    iid=f"block-{index}",
                    text=label,
                    values=(kind, "" if score is None else score),
                )
        self.tree.item(root, open=True)

    def _on_tree_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        if not iid.startswith("block-"):
            self.selected_block_index = None
            self.editor_tabs.select(self.global_tab)
            return
        index = int(iid.split("-", 1)[1])
        block = self.raw_exam["blocks"][index]
        if block.get("type") != "question":
            self.selected_block_index = None
            return
        self.selected_block_index = index
        self._load_question_fields(block["question"])
        self.editor_tabs.select(self.content_tab)

    def _load_question_fields(self, question: dict[str, Any]) -> None:
        self.kind_var.set("客观题" if question.get("kind") == "objective" else "主观题")
        score = question.get("score")
        self.score_var.set("" if score is None else str(score).rstrip("0").rstrip("."))
        self.option_layout_var.set(
            "两行两列" if question.get("option_layout") == "two_column" else "四行单列"
        )
        self.stem_text.delete("1.0", tk.END)
        self.stem_text.insert("1.0", question.get("stem", ""))
        default_indent = 0 if question.get("kind") == "objective" else 1.5
        spec = question.get("format", {})
        self.font_var.set(str(spec.get("font", "宋体")))
        self.size_var.set(str(spec.get("size_pt", 10.5)))
        self.indent_var.set(str(spec.get("first_line_indent_chars", default_indent)))
        self.alignment_var.set(str(spec.get("alignment", "左对齐")))
        self.line_spacing_var.set(str(spec.get("line_spacing", 1.25)))
        self.space_before_var.set(str(spec.get("space_before_pt", 0)))
        self.space_after_var.set(str(spec.get("space_after_pt", 0)))
        self.keep_next_var.set(bool(spec.get("keep_with_next", False)))
        self.page_break_var.set(bool(spec.get("page_break_before", False)))
        self.option_font_var.set(str(spec.get("option_font", "宋体")))
        self.option_size_var.set(str(spec.get("option_size_pt", 10.5)))
        self.option_left_var.set(str(spec.get("option_left_indent_chars", 1.5)))
        self.option_hanging_var.set(str(spec.get("option_hanging_indent_chars", 1.7)))

    def _load_global_fields(self) -> None:
        metadata = self.raw_exam.get("metadata", {})
        self.exam_name_var.set(str(metadata.get("exam_name", "")))
        self.subject_name_var.set(str(metadata.get("subject_name", "语　文")))
        self.exam_meta_var.set(str(metadata.get("meta_text", "")))
        self.total_score_var.set(str(metadata.get("total_score", 150)))

    def apply_current_question(self, *, silent: bool = False) -> bool:
        if self.selected_block_index is None:
            if not silent:
                messagebox.showinfo(APP_TITLE, "请先在结构树中选择一道题。")
            return False
        question = self.raw_exam["blocks"][self.selected_block_index]["question"]
        question["stem"] = self.stem_text.get("1.0", tk.END).strip()
        question["kind"] = "objective" if self.kind_var.get() == "客观题" else "subjective"
        question["score"] = _optional_float(self.score_var.get())
        question["option_layout"] = (
            "two_column" if self.option_layout_var.get() == "两行两列" else "vertical"
        )
        self._populate_tree()
        if not silent:
            self.status_var.set(f"第 {question['number']} 题内容已更新。")
        return True

    def apply_format_and_preview(self) -> None:
        if not self.apply_current_question(silent=True):
            return
        question = self.raw_exam["blocks"][self.selected_block_index]["question"]
        question["format"] = {
            "font": self.font_var.get(),
            "size_pt": _required_float(self.size_var.get(), "题干字号"),
            "first_line_indent_chars": _required_float(self.indent_var.get(), "首行缩进"),
            "alignment": self.alignment_var.get(),
            "line_spacing": _required_float(self.line_spacing_var.get(), "行距"),
            "space_before_pt": _required_float(self.space_before_var.get(), "段前间距"),
            "space_after_pt": _required_float(self.space_after_var.get(), "段后间距"),
            "keep_with_next": self.keep_next_var.get(),
            "page_break_before": self.page_break_var.get(),
            "option_font": self.option_font_var.get(),
            "option_size_pt": _required_float(self.option_size_var.get(), "选项字号"),
            "option_left_indent_chars": _required_float(
                self.option_left_var.get(), "选项左缩进"
            ),
            "option_hanging_indent_chars": _required_float(
                self.option_hanging_var.get(), "选项悬挂"
            ),
        }
        self.request_preview()

    def apply_global_settings(self) -> None:
        metadata = self.raw_exam.setdefault("metadata", {})
        metadata["exam_name"] = self.exam_name_var.get().strip()
        metadata["subject_name"] = self.subject_name_var.get().strip()
        metadata["meta_text"] = self.exam_meta_var.get().strip()
        metadata["total_score"] = _required_float(self.total_score_var.get(), "卷首总分")
        self.status_var.set("卷面设置已更新。")
        self.request_preview()

    def import_new_exam(self) -> None:
        path = filedialog.askopenfilename(
            title="导入新试题",
            filetypes=[
                ("支持的试题", "*.json *.docx *.txt *.md"),
                ("结构化 JSON", "*.json"),
                ("Word 文档", "*.docx"),
                ("文本或 Markdown", "*.txt *.md"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.raw_exam = import_exam(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.current_exam_path = Path(path)
        self.selected_block_index = None
        self._populate_tree()
        self._load_global_fields()
        self.status_var.set(f"已导入：{Path(path).name}")
        self.request_preview()

    def save_project(self) -> None:
        self.apply_current_question(silent=True)
        path = filedialog.asksaveasfilename(
            title="保存结构化项目",
            defaultextension=".json",
            filetypes=[("结构化试题", "*.json")],
            initialfile="exam.json",
        )
        if not path:
            return
        save_exam(self.raw_exam, path)
        self.current_exam_path = Path(path)
        self.status_var.set(f"项目已保存：{path}")

    def import_template(self) -> None:
        path = filedialog.askopenfilename(
            title="导入 Word 母版",
            filetypes=[("Word 母版", "*.docx")],
        )
        if not path:
            return
        self.template_path = Path(path)
        self.template_status_var.set(f"当前母版：{self.template_path.name}")
        self.status_var.set("母版已载入，正在刷新预览。")
        self.request_preview()

    def use_default_template(self) -> None:
        self.template_path = None
        self.template_status_var.set("当前版式：全国卷默认预设")
        self.status_var.set("已恢复全国卷默认预设。")
        self.request_preview()

    def request_preview(self) -> None:
        if self.busy:
            self.status_var.set("当前任务完成后可再次刷新预览。")
            return
        self.apply_current_question(silent=True)
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在生成 Word 和 PDF 预览……")
        raw = deepcopy(self.raw_exam)
        template = self.template_path
        iteration_dir = Path(
            tempfile.mkdtemp(prefix="preview_", dir=self.temp_dir)
        )

        def worker() -> None:
            try:
                _, pdf_path, engine = build_documents(
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
                    raise RuntimeError("预览 PDF 未生成。")
                pages = rasterize_pdf(pdf_path, iteration_dir / "pages")
                self.messages.put(("preview", (pages, engine)))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def open_export_dialog(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "请等待当前预览任务完成。")
            return
        self.apply_current_question(silent=True)
        ExportDialog(self)

    def start_export(
        self,
        output_dir: Path,
        basename: str,
        export_docx: bool,
        export_pdf: bool,
    ) -> None:
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在导出正式文件……")
        raw = deepcopy(self.raw_exam)
        template = self.template_path
        temporary = Path(tempfile.mkdtemp(prefix="export_", dir=self.temp_dir))

        def worker() -> None:
            try:
                docx, pdf, engine = build_documents(
                    raw,
                    self.layout_path,
                    output_dir,
                    basename,
                    template_path=template,
                    export_docx=export_docx,
                    export_pdf=export_pdf,
                    temporary_dir=temporary,
                )
                self.messages.put(("export", (docx, pdf, engine)))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_messages(self) -> None:
        try:
            kind, payload = self.messages.get_nowait()
        except queue.Empty:
            self.after(180, self._poll_messages)
            return
        self.busy = False
        self.busy_bar.stop()
        if kind == "preview":
            pages, engine = payload
            self.preview_pages = pages
            self.preview_page_index = min(
                self.preview_page_index, max(0, len(pages) - 1)
            )
            self.status_var.set(f"预览已更新，PDF 引擎：{engine}")
            self._show_current_page()
        elif kind == "export":
            docx, pdf, engine = payload
            lines = ["导出完成。"]
            if docx:
                lines.append(f"Word：{docx}")
            if pdf:
                lines.append(f"PDF：{pdf}")
                lines.append(f"转换引擎：{engine}")
            self.status_var.set("正式文件已导出。")
            messagebox.showinfo(APP_TITLE, "\n".join(lines))
        else:
            self.status_var.set("任务失败，请查看提示。")
            messagebox.showerror(APP_TITLE, str(payload))
        self.after(180, self._poll_messages)

    def _show_current_page(self) -> None:
        if not self.preview_pages:
            return
        path = self.preview_pages[self.preview_page_index]
        image = Image.open(path)
        target_width = max(400, int(image.width * self.zoom))
        target_height = int(image.height * target_width / image.width)
        resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        canvas_width = max(self.canvas.winfo_width(), target_width + 80)
        x = canvas_width // 2
        self.canvas.create_rectangle(
            x - target_width // 2 + 6,
            24 + 6,
            x + target_width // 2 + 6,
            24 + target_height + 6,
            fill="#AEB4BC",
            outline="",
        )
        self.canvas.create_image(x, 24, image=self.preview_photo, anchor=tk.N)
        self.canvas.configure(
            scrollregion=(0, 0, canvas_width, target_height + 60)
        )
        self.page_status_var.set(
            f"第 {self.preview_page_index + 1} / {len(self.preview_pages)} 页　"
            f"{int(self.zoom * 100)}%"
        )

    def previous_page(self) -> None:
        if self.preview_pages and self.preview_page_index > 0:
            self.preview_page_index -= 1
            self._show_current_page()

    def next_page(self) -> None:
        if (
            self.preview_pages
            and self.preview_page_index < len(self.preview_pages) - 1
        ):
            self.preview_page_index += 1
            self._show_current_page()

    def change_zoom(self, delta: float) -> None:
        self.zoom = min(1.5, max(0.35, self.zoom + delta))
        self._show_current_page()

    def _on_close(self) -> None:
        try:
            self.temp_context.cleanup()
        except Exception:
            pass
        self.destroy()


def _safe_filename(value: str) -> str:
    forbidden = '<>:"/\\|?*'
    return "".join("_" if char in forbidden else char for char in value).strip(" .")


def _optional_float(value: str) -> float | None:
    text = value.strip()
    return None if not text else float(text)


def _required_float(value: str, label: str) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label}必须填写数字。") from exc


def self_test(output: Path) -> int:
    """便携版自检，覆盖逐题格式和 DOCX 生成。"""

    raw = json.loads(resource_path("samples/exam.json").read_text(encoding="utf-8"))
    first_question = next(
        block["question"]
        for block in raw["blocks"]
        if block.get("type") == "question"
    )
    first_question["format"] = {
        "font": "宋体",
        "size_pt": 10.5,
        "first_line_indent_chars": 0,
        "alignment": "左对齐",
        "line_spacing": 1.25,
        "option_font": "宋体",
        "option_size_pt": 10.5,
        "option_left_indent_chars": 1.5,
        "option_hanging_indent_chars": 1.7,
    }
    docx, _, _ = build_documents(
        raw,
        resource_path("templates/layout.yaml"),
        output,
        "工作台自检",
        export_docx=True,
        export_pdf=False,
        temporary_dir=output,
    )
    print(docx)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", metavar="OUTPUT")
    args, _ = parser.parse_known_args()
    if args.self_test:
        return self_test(Path(args.self_test))
    DesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
