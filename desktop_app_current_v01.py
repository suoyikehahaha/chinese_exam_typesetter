"""Canonical current desktop module for runtime version 0.1.

The public runtime is now Word-only at the file boundary, DOCX-only at the
export boundary, and independent of Office/WPS for startup and live preview.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import desktop_app_current as runtime
from app.current_importer import import_exam
from app.current_pipeline_v02 import build_documents
from app.export_naming_v01 import default_export_basename, with_export_suffix
from app.office_bridge_v01 import engine_summary
from app.preview_service_v02 import PreviewService
from app.windows_drop_v01 import install_file_drop, uninstall_file_drop


runtime.build_documents = build_documents
runtime.base.build_documents = build_documents
runtime.import_exam = import_exam
runtime.PreviewService = PreviewService
runtime.base.APP_TITLE = runtime.APP_TITLE


class CurrentDesktopApp(runtime.CurrentDesktopApp):
    """Current workbench with the narrowed Word workflow."""

    def __init__(self) -> None:
        self.target_pages_var = tk.StringVar(value="8")
        super().__init__()

    def _build_ui(self) -> None:
        super()._build_ui()
        # The inherited global panel already has rows 0 through 6.  Keep the
        # page target close to the document-wide settings without enlarging
        # the main editor layout.
        if hasattr(self, "global_tab"):
            ttk.Label(self.global_tab, text="目标页数").grid(
                row=7, column=0, sticky=tk.W, pady=4
            )
            ttk.Entry(self.global_tab, textvariable=self.target_pages_var).grid(
                row=7, column=1, sticky=tk.EW, pady=4
            )
            ttk.Label(self.global_tab, text="默认 8 页，自动微调段落节奏").grid(
                row=7, column=2, sticky=tk.W, padx=(8, 0), pady=4
            )
            ttk.Label(
                self.global_tab,
                text=engine_summary(),
                foreground="#5C6770",
                wraplength=420,
            ).grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))
        install_file_drop(self, self._handle_dropped_files)

    def _load_global_fields(self) -> None:
        super()._load_global_fields()
        value = self.raw_exam.get("metadata", {}).get("target_pages", 8)
        self.target_pages_var.set(str(value))

    def apply_global_settings(self) -> None:
        try:
            value = int(self.target_pages_var.get().strip())
        except ValueError as exc:
            messagebox.showerror(runtime.APP_TITLE, "目标页数必须填写整数。", parent=self)
            raise ValueError("目标页数必须填写整数。") from exc
        if not 1 <= value <= 32:
            messagebox.showerror(runtime.APP_TITLE, "目标页数应在 1 到 32 页之间。", parent=self)
            raise ValueError("目标页数应在 1 到 32 页之间。")
        self.raw_exam.setdefault("metadata", {})["target_pages"] = value
        super().apply_global_settings()

    def default_export_basename(self) -> str:
        """Return the imported document stem for the DOCX export dialog."""

        metadata = self.raw_exam.get("metadata", {})
        fallback = str(metadata.get("exam_name", "语文试卷"))
        return default_export_basename(self.current_exam_path, fallback)

    def open_export_dialog(self) -> None:
        """Open a DOCX-only export dialog."""

        if self.busy:
            messagebox.showinfo(runtime.APP_TITLE, "请等待当前预览任务完成。", parent=self)
            return
        self.apply_current_question(silent=True)
        initial = with_export_suffix(self.default_export_basename())
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 Word 文档",
            defaultextension=".docx",
            filetypes=[("Word 文档（DOCX）", "*.docx")],
            initialfile=f"{initial}.docx",
        )
        if not path:
            return
        target = Path(path)
        basename = target.stem
        if basename.endswith("（排版）"):
            basename = basename[:-4]
        self.start_export(target.parent, basename, True, False)

    def import_new_exam(self) -> None:
        """Import only DOCX or legacy DOC files."""

        path = filedialog.askopenfilename(
            title="导入 Word 试题",
            filetypes=[
                ("Word 文档", "*.docx *.doc"),
                ("DOCX 文档", "*.docx"),
                ("旧版 DOC 文档", "*.doc"),
            ],
        )
        if path:
            self._import_exam_path(Path(path))

    def _handle_dropped_files(self, paths: tuple[Path, ...]) -> None:
        """Accept the first dropped Word document and ignore other files."""

        supported = [path for path in paths if path.suffix.lower() in {".docx", ".doc"}]
        if not supported:
            messagebox.showwarning(runtime.APP_TITLE, "请拖入 .docx 或 .doc 文件。", parent=self)
            return
        self._import_exam_path(supported[0])

    def _import_exam_path(self, path: Path) -> None:
        try:
            self.raw_exam = import_exam(path)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(runtime.APP_TITLE, str(exc), parent=self)
            return
        self.current_exam_path = path
        self.selected_block_index = None
        if hasattr(self, "undo_stack"):
            self.undo_stack.clear()
            self.redo_stack.clear()
        self._populate_tree()
        self._load_global_fields()
        diagnostics = self.raw_exam.get("diagnostics", [])
        suffix = f"，识别提示 {len(diagnostics)} 条" if diagnostics else ""
        self.status_var.set(f"已导入：{path.name}{suffix}")
        self.after(100, self._select_first_question)
        self.request_preview()

    def _finish_preview(self, result: object) -> None:
        """Use internal-preview wording and report target versus actual pages."""

        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        if result.generation != self._preview_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        self._preview_block_locators = result.locators
        self.preview_pages = list(result.pages)
        self.preview_page_index = min(self.preview_page_index, max(0, len(self.preview_pages) - 1))
        self.status_var.set(
            f"预览已更新：实际 {result.actual_pages} 页，目标 {result.target_pages} 页（内部预览）"
        )
        self._show_current_page()
        if self._pending_preview_block is not None:
            block_index = self._pending_preview_block
            self.after(20, lambda: self._jump_to_block(block_index))

    def _on_close(self) -> None:
        uninstall_file_drop(self)
        super()._on_close()


runtime.CurrentDesktopApp = CurrentDesktopApp

APP_TITLE = runtime.APP_TITLE
APP_VERSION = runtime.APP_VERSION


def main() -> int:
    """Launch the current workbench."""

    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "import_exam", "main"]
