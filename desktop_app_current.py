"""Current desktop entry point for runtime version 0.1.

The visual workbench is reused from the mature editor implementation, while
document building, importing, preview scheduling and version identity are
owned here. This gives Windows packaging one stable module to launch.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any

from PIL import Image, ImageTk

import desktop_app as base
from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.preview_service import PreviewResult, PreviewService
from app.version import APP_TITLE, APP_VERSION
from desktop_app_v087_release import ProductionDesktopApp as LegacyWorkbench


base.APP_TITLE = APP_TITLE
base.VERSION = APP_VERSION
base.build_documents = build_documents


class CurrentDesktopApp(LegacyWorkbench):
    """Windows workbench using the explicit 0.1 runtime services."""

    def __init__(self) -> None:
        self._preview_service = PreviewService(build_documents)
        self._export_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="exam-export",
        )
        self._preview_generation = 0
        self._export_generation = 0
        super().__init__()

    def request_preview(self) -> None:
        """Schedule a debounced-style single-flight preview task."""

        if getattr(self, "_startup_preview_deferred", False):
            self._startup_preview_pending = True
            return
        if self.busy:
            self._preview_service.cancel()
            self.busy = False
            self.busy_bar.stop()
        self.apply_current_question(silent=True)
        selection = self.tree.selection()
        if selection and selection[0].startswith("block-"):
            self._pending_preview_block = int(selection[0].split("-", 1)[1])
        raw = deepcopy(self.raw_exam)
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在生成预览，请稍候…")
        try:
            self._preview_generation = self._preview_service.submit(
                raw,
                self.layout_path,
                self.template_path,
                self._receive_preview_result,
            )
        except Exception as exc:
            self._finish_task_error(exc)

    def _receive_preview_result(self, result: PreviewResult | Exception) -> None:
        """Marshal worker output back to Tk's main thread."""

        try:
            self.after(0, lambda: self._finish_preview(result))
        except tk.TclError:
            return

    def _finish_preview(self, result: PreviewResult | Exception) -> None:
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        if result.generation != self._preview_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        self._preview_block_locators = result.locators
        self.preview_pages = list(result.pages)
        self.preview_page_index = min(
            self.preview_page_index,
            max(0, len(self.preview_pages) - 1),
        )
        self.status_var.set(f"预览已更新，PDF 引擎：{result.engine}")
        self._show_current_page()
        if self._pending_preview_block is not None:
            block_index = self._pending_preview_block
            self.after(20, lambda: self._jump_to_block(block_index))

    def _finish_task_error(self, error: Exception) -> None:
        self.busy = False
        self.busy_bar.stop()
        self.status_var.set("任务失败，请查看提示")
        try:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
        except tk.TclError:
            return

    def import_new_exam(self) -> None:
        """Import DOCX, TXT, Markdown or JSON through the current facade."""

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
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        self.current_exam_path = Path(path)
        self.selected_block_index = None
        if hasattr(self, "undo_stack"):
            self.undo_stack.clear()
            self.redo_stack.clear()
        self._populate_tree()
        self._load_global_fields()
        diagnostics = self.raw_exam.get("diagnostics", [])
        suffix = f"，识别提示 {len(diagnostics)} 条" if diagnostics else ""
        self.status_var.set(f"已导入：{Path(path).name}{suffix}")
        self.after(100, self._select_first_question)
        self.request_preview()

    def start_export(
        self,
        output_dir: Path,
        basename: str,
        export_docx: bool,
        export_pdf: bool,
    ) -> None:
        """Export DOCX through the current pipeline with a safe display name."""

        if self.busy:
            messagebox.showinfo(APP_TITLE, "请等待当前任务完成。", parent=self)
            return
        self.apply_current_question(silent=True)
        safe_name = basename.strip() or "语文试卷"
        if not safe_name.endswith("（排版）"):
            safe_name += "（排版）"
        raw = deepcopy(self.raw_exam)
        template = self.template_path
        self._export_generation += 1
        generation = self._export_generation
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在导出 Word 文件，请稍候…")

        def worker() -> tuple[int, Path | None, Path | None, str] | Exception:
            try:
                docx, pdf, engine = build_documents(
                    raw,
                    self.layout_path,
                    output_dir,
                    safe_name,
                    template_path=template,
                    export_docx=export_docx,
                    export_pdf=export_pdf,
                    temporary_dir=self.temp_dir,
                )
                return generation, docx, pdf, engine
            except Exception as exc:  # export boundary sends actionable error to UI
                return exc

        future = self._export_executor.submit(worker)

        def done(completed: Any) -> None:
            result = completed.result()
            try:
                self.after(0, lambda: self._finish_export(result))
            except tk.TclError:
                return

        future.add_done_callback(done)

    def _finish_export(self, result: Any) -> None:
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        generation, docx, pdf, engine = result
        if generation != self._export_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        lines = ["导出完成。"]
        if docx:
            lines.append(f"Word：{docx}")
        if pdf:
            lines.append(f"PDF：{pdf}")
            lines.append(f"转换引擎：{engine}")
        self.status_var.set("正式文件已导出")
        messagebox.showinfo(APP_TITLE, "\n".join(lines), parent=self)

    def _show_current_page(self) -> None:
        """Display one page while closing the source image handle promptly."""

        if not self.preview_pages:
            return
        path = self.preview_pages[self.preview_page_index]
        with Image.open(path) as image:
            target_width = max(400, int(image.width * self.zoom))
            target_height = int(image.height * target_width / image.width)
            resized = image.resize(
                (target_width, target_height),
                Image.Resampling.LANCZOS,
            )
        self.preview_photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        canvas_width = max(self.canvas.winfo_width(), target_width + 80)
        x = canvas_width // 2
        self.canvas.create_rectangle(
            x - target_width // 2 + 6,
            30,
            x + target_width // 2 + 6,
            30 + target_height + 6,
            fill="#AEB4BC",
            outline="",
        )
        self.canvas.create_image(x, 24, image=self.preview_photo, anchor=tk.N)
        self.canvas.configure(scrollregion=(0, 0, canvas_width, target_height + 60))
        self.page_status_var.set(
            f"第 {self.preview_page_index + 1} / {len(self.preview_pages)} 页"
            f"  {int(self.zoom * 100)}%"
        )

    def _on_close(self) -> None:
        """Stop workers before destroying Tk widgets."""

        self._preview_service.close()
        self._export_executor.shutdown(wait=True, cancel_futures=True)
        super()._on_close()


def main() -> int:
    """Launch the current Windows workbench."""

    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["CurrentDesktopApp", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
