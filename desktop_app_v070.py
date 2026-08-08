"""工作台 v0.7.0，统一格式面板并落实最新卷面规则。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

import desktop_app as base
from app.block_overrides import apply_block_overrides
from app.chinese_typography_v2 import enable_chinese_typography_v2
from app.config import load_layout
from app.exam_format_rules_v2 import apply_exam_format_rules_v2
from app.exporters import PdfExporter
from app.flexible_importers_v4 import import_exam, save_exam
from app.inline_formatting_v2 import apply_inline_formats_v2
from app.models import ExamDocument
from app.native_docx_objects import restore_native_objects
from app.pagination import apply_pagination_guards
from app.question_overrides import apply_question_overrides
from app.renderers import DocxRenderer
from app.semantic_formatting_v3 import apply_semantic_formatting_v3
from app.validators import check_required_fonts, validate_exam
from desktop_app_v060_final import FinalRichDesktopApp


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.7.0"


def build_documents_v7(
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
    """生成符合最新题干、中文禁则和题内结构规则的文件。"""

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
    apply_semantic_formatting_v3(docx_work)
    apply_exam_format_rules_v2(docx_work)
    apply_inline_formats_v2(docx_work, raw_exam)
    enable_chinese_typography_v2(docx_work)

    pdf_path: Path | None = None
    engine = "docx-only"
    if export_pdf:
        pdf_path, engine = PdfExporter().export(
            docx_work,
            output_dir / f"{basename}.pdf",
        )
    return (docx_work if export_docx else None), pdf_path, engine


base.build_documents = build_documents_v7


class WordOnlyExportDialog(tk.Toplevel):
    """只导出可编辑 Word 的设置窗口。"""

    def __init__(self, parent: "FinalDesktopAppV7") -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("导出 Word 试卷")
        self.geometry("560x250")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.output_var = tk.StringVar(
            value=str(Path.home() / "Documents" / "语文试卷输出")
        )
        default_name = str(
            parent.raw_exam.get("metadata", {}).get("exam_name", "高中语文试卷")
        )
        self.name_var = tk.StringVar(value=base._safe_filename(default_name))
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=22)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="导出可编辑 Word", style="DialogTitle.TLabel").grid(
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
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=3, sticky=tk.E, pady=(20, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="导出 DOCX",
            style="Primary.TButton",
            command=self._submit,
        ).pack(side=tk.LEFT, padx=(10, 0))
        frame.columnconfigure(1, weight=1)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(parent=self)
        if path:
            self.output_var.set(path)

    def _submit(self) -> None:
        name = base._safe_filename(self.name_var.get().strip())
        if not name:
            messagebox.showerror(base.APP_TITLE, "请输入文件名称。", parent=self)
            return
        self.destroy()
        self.parent.start_export(
            Path(self.output_var.get()),
            name,
            True,
            False,
        )


class FinalDesktopAppV7(FinalRichDesktopApp):
    """合并题干和选项格式，并让历史按钮始终可见。"""

    def __init__(self) -> None:
        super().__init__()
        for widget in self.detail_frame.grid_slaves():
            if int(widget.grid_info().get("row", -1)) == 23:
                widget.grid_configure(row=21)

    def _build_combined_editor(self, tab: ttk.Frame) -> None:
        super()._build_combined_editor(tab)
        for widget in list(tab.grid_slaves()):
            info = widget.grid_info()
            row = int(info.get("row", -1))
            if row in {5, 14} and isinstance(widget, ttk.Separator):
                widget.grid_remove()
            elif row == 6 and isinstance(widget, ttk.Label):
                widget.configure(text="题目与选择项格式")
            elif row == 15 and isinstance(widget, ttk.Label):
                widget.grid_remove()
            elif 16 <= row <= 19:
                widget.grid_configure(row=row - 2)
            elif row == 20:
                widget.grid_configure(row=18)
            elif row == 21:
                widget.grid_configure(row=19)
            elif row == 22:
                widget.grid_configure(row=20)
            elif row == 0 and isinstance(widget, ttk.Label):
                widget.grid_configure(columnspan=2)
        ttk.Button(tab, text="撤回", command=self.undo_action).grid(
            row=0, column=2, sticky=tk.E, padx=(6, 3)
        )
        ttk.Button(tab, text="前进", command=self.redo_action).grid(
            row=0, column=3, sticky=tk.W, padx=(3, 0)
        )

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

    def open_export_dialog(self) -> None:
        if self.busy:
            messagebox.showinfo(base.APP_TITLE, "请等待当前预览任务完成。")
            return
        self.apply_current_question(silent=True)
        WordOnlyExportDialog(self)

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
    FinalDesktopAppV7().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
