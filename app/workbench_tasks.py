"""Schedule, retire and display document-revision-specific previews and Word exports."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import queue
import tkinter as tk
from tkinter import messagebox
from typing import Any

from .current_pipeline import build_documents, ExportDestinationError
from .inspector_model import build_object_locators
from .version import APP_TITLE
from .workbench_state import draft_signature


class WorkbenchTasks:
    def _release_startup_preview(self) -> None:
        """Release the delayed first preview after the main window is ready."""

        self._startup_preview_deferred = False
        if self._startup_preview_pending:
            self._startup_preview_pending = False
            self.request_preview()

    def _run_canvas_preview(self) -> None:
        """Run the debounced preview request scheduled by inspector edits."""

        self._canvas_preview_after = None
        self.request_preview()

    def toggle_print_proof(self):
        if self._proof_requested:
            self.request_preview()
        else:
            self.request_preview(print_proof=True)

    def request_preview(self, *, print_proof=False) -> None:
        """Preview is cancellable background work, not a lock on exporting."""
        if self._closing:
            return
        if getattr(self, "_export_in_progress", False) or getattr(self, "_import_in_progress", False):
            return

        if getattr(self, "_startup_preview_deferred", False):
            self._startup_preview_pending = True
            return
        if self.busy:
            self._preview_service.cancel()
            self.busy = False
            self.busy_bar.stop()
        if not self._flush_edits(show_error=print_proof):
            return
        if self._canvas_preview_after:
            self.after_cancel(self._canvas_preview_after)
            self._canvas_preview_after = None
        self._proof_requested = print_proof
        self.document_editor.proof_button.configure(text="返回快速预览" if print_proof else "打印校对")
        selection = self.tree.selection()
        if selection and selection[0].startswith("block-"):
            self._pending_preview_block = int(selection[0].split("-", 1)[1])
        raw = self.raw_exam
        self._preview_ticket += 1
        ticket = self._preview_ticket
        self._requested_revision = (self._session_generation, self._revision)
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在用本机办公软件校对打印分页…" if print_proof else "正在生成预览，请稍候…")
        self._preview_settings_feedback = deepcopy(self._settings_feedback)
        if self._preview_settings_feedback is not None:
            self._set_settings_message("设置已应用，正在更新预览…")
        try:
            self._preview_generation = self._preview_service.submit(
                raw,
                self.layout_path,
                self.template_path,
                lambda result: self._ui_messages.put(("preview", ticket, result)),
                print_proof=print_proof,
            )
        except Exception as exc:  # noqa: BLE001
            self._finish_task_error(exc)

    def _poll_ui_messages(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, ticket, result = self._ui_messages.get_nowait()
                if kind == "preview" and ticket == self._preview_ticket:
                    self._finish_preview(result)
                elif kind == "recovery_error" and ticket == self._session_generation:
                    self.status_var.set(f"自动草稿保存失败，请用 Ctrl+S 另存。{result}")
                elif kind == "export" and ticket == self._export_generation:
                    self._finish_export(result)
        except queue.Empty:
            pass
        self.after(60, self._poll_ui_messages)

    def _cancel_preview(self) -> None:
        self._preview_ticket += 1
        self._preview_service.cancel()
        if not self._export_in_progress:
            self.busy = False
            self.busy_bar.stop()

    def _finish_task_error(self, error: Exception) -> None:
        """Stop the busy indicators and show an actionable preview error."""

        self.busy = False
        self.busy_bar.stop()
        if self._proof_requested:
            self._proof_requested = False
            self.document_editor.proof_button.configure(text="打印校对")
        self.status_var.set("任务失败，请查看提示")
        if self._settings_feedback is not None:
            self._set_settings_message("预览更新失败；设置已保留，点击“应用并查看”重试。")
        try:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
        except tk.TclError:
            return

    def start_export(
        self,
        output_dir: Path,
        basename: str,
        export_docx: bool,
        export_pdf: bool,
    ) -> None:
        """Export the current draft through the unified DOCX pipeline."""

        if getattr(self, "_export_in_progress", False) or getattr(self, "_import_in_progress", False):
            messagebox.showinfo(APP_TITLE, "请等待当前任务完成。", parent=self)
            return
        if not self._flush_edits(show_error=True):
            return
        self._cancel_preview()
        safe_name = basename.strip() or "语文试卷"
        # The save dialog has already confirmed this exact path.
        destination = output_dir / f"{safe_name}.docx"
        if self.current_exam_path and destination.resolve() == self.current_exam_path.resolve():
            messagebox.showwarning(APP_TITLE, "请选择新的文件名，以保留导入的 Word 原件。", parent=self)
            return
        raw = deepcopy(self.raw_exam)
        self._export_signature = draft_signature(raw)
        template = self.template_path
        self._export_generation += 1
        self._export_in_progress = True
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
            except Exception as exc:  # noqa: BLE001
                return exc

        future = self._export_executor.submit(worker)

        def done(completed: Any) -> None:
            try:
                result = completed.result()
            except Exception as exc:
                result = exc
            self._ui_messages.put(("export", generation, result))

        future.add_done_callback(done)

    def _finish_export(self, result: Any) -> None:
        """Publish export completion on the Tk thread."""
        self._export_in_progress = False
        if isinstance(result, ExportDestinationError):
            self.busy = False
            self.busy_bar.stop()
            self.status_var.set("目标 Word 文件正被占用或不可写")
            retry = messagebox.askretrycancel(
                "无法保存 Word 文件",
                f"{result}\n\n关闭目标文件后点击“重试”；也可以点击“取消”，"
                "重新导出并选择其他文件名。",
                parent=self,
            )
            if retry:
                target = result.target
                self.after(100, lambda: self.start_export(target.parent, target.stem, True, False))
            return
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        generation, docx, pdf, engine = result
        if generation != self._export_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        self.status_var.set("正式文件已导出")
        if docx:
            self._last_export_path = Path(docx)
            self.result_var.set(f"已导出  ·  {Path(docx).name}")
            self.result_bar.pack(fill=tk.X, side=tk.BOTTOM)
            self._refresh_history_buttons()
        if self._export_signature != draft_signature(self.raw_exam):
            self.status_var.set("文件已导出，后续修改可再次导出。")
        self._schedule_canvas_preview()

    def _schedule_canvas_preview(self) -> None:
        if self._canvas_preview_after:
            self.after_cancel(self._canvas_preview_after)
        self._canvas_preview_after = self.after(95, self._run_canvas_preview)

    def _finish_preview(self, result: object) -> None:
        revision = (self._session_generation, self._revision)
        if self._requested_revision != revision:
            return
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        if result.generation != self._preview_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        proof = result.engine in {"Microsoft Word", "LibreOffice"}
        object_locators = {} if proof else build_object_locators(self.raw_exam, result.locators)
        selected_key: object = self.selected_block_index
        if self.selected_content_object is not None:
            selected_key = self.selected_content_object.key
        loaded = self.document_editor.set_preview_pages(
            list(result.pages),
            locators=object_locators,
            actual_pages=result.actual_pages,
            raw_exam=self.raw_exam,
            selected_key=selected_key,
            page_info=getattr(result, "page_info", ()),
        )
        if not loaded:
            self._finish_task_error(OSError(self.document_editor.update_message_var.get()))
            return
        self._displayed_revision = revision
        self._preview_service.acknowledge(result.generation)
        self._preview_block_locators = dict(result.locators)
        self.preview_pages = list(result.pages)
        self.preview_page_index = min(self.preview_page_index, max(0, len(self.preview_pages) - 1))
        feedback = self._preview_settings_feedback
        if feedback is not None:
            page_index = max(0, min(feedback["page"], len(result.pages) - 1))
            changed = page_index in self.document_editor.changed_pages
            visible = "画面已更新" if changed else "画面无变化"
            message = f"整卷设置已应用 · 第 {page_index + 1} 页{visible}"
            if feedback.get("header_changed"):
                message += " · 已定位卷首"
            elif not changed:
                message += "，其他页面可能受影响"
            self.document_editor.show_page_update(page_index, message)
            self._set_settings_message(message)
            self._settings_feedback = None
            self._preview_settings_feedback = None
        if feedback is None:
            page = self.document_editor.current_page() + 1
            self.document_editor.update_message_var.set(f"第 {page} 页 · 已显示当前修改")
        document_kind = str(self.raw_exam.get("document_kind", "exam"))
        if document_kind == "exam_with_answer":
            self.status_var.set(
                f"预览估算 {result.actual_pages} 页（含答案）；打印分页以 Word 文档为准"
            )
        elif document_kind == "answer":
            self.status_var.set(f"答案预览估算 {result.actual_pages} 页；打印分页以 Word 文档为准")
        else:
            self.status_var.set(f"快速预览 {result.actual_pages} 页 · 按内容自然分页")
        if proof:
            message = f"{result.engine} 打印校对 · 共 {result.actual_pages} 页"
            self.status_var.set(message)
            self.document_editor.update_message_var.set(message + " · 修改后自动返回快速预览")
        self._preview_warnings = tuple(getattr(result, "warnings", ()))
        self._refresh_score_display()
        # Preserve the reader's viewport; explicit structure selection navigates.
