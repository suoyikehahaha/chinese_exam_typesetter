"""Document revisions, cancellable imports and per-window draft recovery."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from .drafts import read_draft, save_draft
from .current_importer import import_exam
from .version import APP_TITLE
from .workbench_state import draft_signature


class WorkbenchSession:
    def _invalidate_edit(self):
        self._revision += 1
        self._requested_revision = None
        self._cancel_preview()
        self.raw_exam.get("metadata", {}).pop("page_spacing_scale", None)
        self.document_editor.update_message_var.set("修改已记录，正在更新预览…")
        if self._autosave_after:
            self.after_cancel(self._autosave_after)
        self._autosave_after = self.after(1200, self._autosave)

    def _autosave(self):
        self._autosave_after = None
        if self._closing or self._saved_signature == draft_signature(self.raw_exam):
            return
        session = self._session_generation
        self._recovery.save(self.raw_exam, lambda error: self._ui_messages.put(("recovery_error", session, error)))

    def _clear_recovery(self):
        if self._autosave_after:
            self.after_cancel(self._autosave_after)
            self._autosave_after = None
        self._recovery.clear()
        if self._recovery_claim is not None:
            try:
                self._recovered_path.unlink(missing_ok=True)
            finally:
                self._recovery_claim.__exit__(None, None, None)
                self._recovery_claim = self._recovered_path = None

    def recover_draft(self, *, automatic=False):
        if self._closing or self._import_in_progress or self._export_in_progress:
            return
        for path in self._recovery.candidates():
            if path == self._recovered_path:
                continue
            claim = self._recovery.claim_abandoned(path)
            if not claim.__enter__():
                claim.__exit__(None, None, None)
                continue
            try:
                raw = read_draft(path)
                title = raw["metadata"].get("exam_name") or "未命名试卷"
                if not messagebox.askyesno("恢复自动草稿", f"发现上次未关闭的草稿《{title}》。现在继续编辑吗？", parent=self):
                    return
                if not self._confirm_replace():
                    return
                source = raw["metadata"].get("source_docx_path") or raw["metadata"].get("answer_source_docx_path")
                self._apply_imported_exam(Path(source) if source else path, raw)
                self._recovery_claim, self._recovered_path = claim, path
                self._saved_signature = "recovered"
                self._refresh_history_buttons()
                self.status_var.set("自动草稿已恢复。按 Ctrl+S 选择保存位置。")
                return
            except (OSError, KeyError, TypeError, ValueError) as exc:
                messagebox.showerror(APP_TITLE, f"无法恢复草稿。{exc}", parent=self)
            finally:
                if self._recovery_claim is not claim:
                    claim.__exit__(None, None, None)
        if not automatic:
            messagebox.showinfo(APP_TITLE, "没有可恢复的自动草稿。", parent=self)

    def _set_import_lock(self, locked):
        self._import_in_progress = locked
        if locked:
            self._locked_controls = []
            widgets = [*self._walk_widgets(self.editor_panel), self.export_button]
            for widget in widgets:
                if isinstance(widget, tk.Text) and widget.cget("state") == "normal":
                    widget.configure(state="disabled")
                    self._locked_controls.append(widget)
                elif isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Button, ttk.Checkbutton,
                                         ttk.Radiobutton, ttk.Treeview, ttk.Spinbox)) and not widget.instate(["disabled"]):
                    widget.state(["disabled"])
                    self._locked_controls.append(widget)
            self.cancel_import_button.pack(side=tk.LEFT, before=self.file_label, padx=6)
            self.busy_bar.start(12)
        else:
            for widget in self._locked_controls:
                if isinstance(widget, tk.Text):
                    widget.configure(state="normal")
                else:
                    widget.state(["!disabled"])
            self._locked_controls.clear()
            self.cancel_import_button.pack_forget()
            self.busy_bar.stop()
            self._import_future = None
            self._refresh_history_buttons()

    def cancel_import(self):
        if not self._import_in_progress:
            return
        self._import_generation += 1
        if self._import_future is not None:
            self._import_future.cancel()
        self._set_import_lock(False)
        self.status_var.set("已取消导入，可以继续编辑当前文档。")
        self._schedule_canvas_preview()

    def save_project(self, *, save_as: bool = False) -> bool:
        """Save the editable structured draft for later continuation."""

        if self._import_in_progress or not self._flush_edits(show_error=True):
            return False
        path = None if save_as else self._project_path
        path = path or filedialog.asksaveasfilename(
            title="保存可继续编辑的草稿",
            defaultextension=".json",
            filetypes=[("结构化试题", "*.json")],
            initialfile=f"{self.default_export_basename()}.json",
            parent=self,
        )
        if path:
            try:
                save_draft(self.raw_exam, path, keep_resources=True)
            except (OSError, ValueError) as exc:
                messagebox.showerror(APP_TITLE, f"草稿保存失败：{exc}", parent=self)
                return False
            self._clear_recovery()
            self._project_path = Path(path)
            self._saved_signature = draft_signature(self.raw_exam)
            self.status_var.set(f"项目已保存：{path}")
            self._refresh_history_buttons()
            return True
        return False

    def import_new_exam(self) -> None:
        """Open the Word-only import dialog."""

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
        """Accept the first dropped DOCX or DOC document."""

        supported = [path for path in paths if path.suffix.lower() in {".docx", ".doc"}]
        if not supported:
            messagebox.showwarning(APP_TITLE, "请拖入 .docx 或 .doc 文件。", parent=self)
            return
        self._import_exam_path(supported[0])

    def _import_exam_path(self, path: Path) -> None:
        if getattr(self, "_export_in_progress", False):
            messagebox.showinfo(APP_TITLE, "请等待当前导出完成后再导入试题。", parent=self)
            return
        if self._import_in_progress:
            self.cancel_import()
        if not self._confirm_replace():
            return
        # Keep Tk responsive; only apply the latest import on its UI thread.
        self._import_generation += 1
        generation = self._import_generation
        self._cancel_editor_timers()
        self._cancel_preview()
        self._set_import_lock(True)
        self.status_var.set(f"正在读取：{path.name}")
        future = self._import_future = self._export_executor.submit(import_exam, path)

        def poll() -> None:
            if self._closing:
                return
            if generation != self._import_generation:
                future.cancel()
                return
            if not future.done():
                self.after(40, poll)
                return
            try:
                result = future.result()
            except Exception as exc:
                self._set_import_lock(False)
                self._schedule_canvas_preview()
                self.status_var.set(f"导入失败：{path.name}")
                messagebox.showerror(APP_TITLE, str(exc), parent=self)
                return
            self._set_import_lock(False)
            self._apply_imported_exam(path, result)

        self.after(40, poll)

    def _apply_imported_exam(self, path: Path, result: dict) -> None:
        self._cancel_editor_timers()
        self._cancel_preview()
        self._session_generation += 1
        self._revision = 0
        self._requested_revision = self._displayed_revision = None
        self._last_export_path = None
        self._preview_warnings = ()
        self._clear_recovery()
        self.selected_content_object = None
        self._content_objects = []
        self.selected_block_index = None
        self.raw_exam = result
        self.current_exam_path = path
        self._project_path = None
        self.file_label_var.set(path.name)
        self.result_bar.pack_forget()
        self.loading_fields = True
        self.search_var.set("")
        self.loading_fields = False
        self.selected_block_index = None
        if hasattr(self, "undo_stack"):
            self.undo_stack.clear()
            self.redo_stack.clear()
        self._populate_tree()
        self._load_global_fields()
        self._saved_signature = draft_signature(self.raw_exam)
        self._refresh_history_buttons()
        diagnostics = self.raw_exam.get("diagnostics", [])
        suffix = f"，识别提示 {len(diagnostics)} 条" if diagnostics else ""
        self.status_var.set(f"已导入：{path.name}{suffix}")
        self._select_first_question()
        self.request_preview()
        self._refresh_score_display()

    def open_project(self) -> None:
        if getattr(self, "_export_in_progress", False) or getattr(self, "_import_in_progress", False):
            return
        if not self._confirm_replace():
            return
        path = filedialog.askopenfilename(parent=self, title="继续编辑草稿", filetypes=[("排版草稿", "*.json")])
        if not path:
            return
        try:
            raw = read_draft(path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            messagebox.showerror(APP_TITLE, f"无法读取草稿：{exc}", parent=self)
            return
        self._cancel_preview()
        source = raw["metadata"].get("source_docx_path") or raw["metadata"].get("answer_source_docx_path")
        self._apply_imported_exam(Path(source) if source else Path(path), raw)
        self._project_path = Path(path)
        self._refresh_history_buttons()
        self.status_var.set(f"已恢复草稿：{Path(path).name}")
