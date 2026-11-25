"""主工作台的异步预览、打印校对、导入导出、草稿管理与 GitHub 更新逻辑。"""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
import os
from pathlib import Path
import threading
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..current_importer import import_exam
from ..current_pipeline import ExportDestinationError, build_documents
from ..drafts import read_draft, save_draft
from ..export_naming import default_export_basename
from ..github_updater import check_latest_release, download_release_asset
from ..inspector_model import build_object_locators
from ..score_summary import calculate_score_summary, format_score
from ..version import APP_TITLE, APP_VERSION
from ..workbench_state import draft_signature, suggested_export_path
from .dialogs import AboutDialog, ExportWarningDialog, GitHubUpdateDialog


class SessionActionsMixin:
    """封装预览调度、导入锁定、DOCX 导出、草稿恢复与在线更新检查。"""

    # ==================== 预览调度与打印校对 ====================

    def _release_startup_preview(self) -> None:
        self._startup_preview_deferred = False
        if self._startup_preview_pending:
            self._startup_preview_pending = False
            self.request_preview()

    def _schedule_canvas_preview(self) -> None:
        self._preview_debounce_timer.start()

    def toggle_print_proof(self) -> None:
        if self._proof_requested:
            self.request_preview(print_proof=False)
        else:
            self.request_preview(print_proof=True)

    def _cancel_preview(self) -> None:
        self._preview_ticket += 1
        self._preview_service.cancel()
        if not self._export_in_progress:
            self._set_busy(False)

    def request_preview(self, *, print_proof: bool = False) -> None:
        if self._closing or self._export_in_progress or self._import_in_progress:
            return
        if self._startup_preview_deferred:
            self._startup_preview_pending = True
            return
        if self._busy:
            self._preview_service.cancel()
            self._set_busy(False)
        if not self._flush_edits(show_error=print_proof):
            return

        self._preview_debounce_timer.stop()
        self._proof_requested = print_proof
        self.preview_panel.set_proof_mode(print_proof)
        self._preview_ticket += 1
        ticket = self._preview_ticket
        self._requested_revision = (self._session_generation, self._revision)
        self._set_busy(True)
        self._set_status("正在用本机办公软件校对打印分页…" if print_proof else "正在生成预览，请稍候…")

        self._preview_settings_feedback = deepcopy(self._settings_feedback)
        if self._preview_settings_feedback is not None:
            self._set_settings_message("设置已应用，正在更新预览…", tone="info")

        try:
            self._preview_generation = self._preview_service.submit(
                self.raw_exam,
                self.layout_path,
                self.template_path,
                lambda result, tk=ticket: self._signals.previewReady.emit(tk, result),
                print_proof=print_proof,
            )
        except Exception as exc:  # noqa: BLE001
            self._finish_task_error(exc)

    def _on_preview_ready(self, ticket: int, result: Any) -> None:
        if self._closing or ticket != self._preview_ticket:
            return
        self._finish_preview(result)

    def _finish_preview(self, result: Any) -> None:
        revision = (self._session_generation, self._revision)
        if self._requested_revision != revision:
            return
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        if result.generation != self._preview_generation:
            return

        self._set_busy(False)
        proof = result.engine in {"Microsoft Word", "LibreOffice"}
        object_locators = {} if proof else build_object_locators(self.raw_exam, result.locators)
        selected_key: object = self.selected_block_index
        if self.selected_content_object is not None:
            selected_key = self.selected_content_object.key

        loaded = self.preview_panel.set_preview_pages(
            list(result.pages),
            locators=object_locators,
            actual_pages=result.actual_pages,
            raw_exam=self.raw_exam,
            selected_key=selected_key,
            page_info=getattr(result, "page_info", ()),
        )
        if not loaded:
            self._finish_task_error(OSError(self.preview_panel.update_message()))
            return

        self._displayed_revision = revision
        self._preview_service.acknowledge(result.generation)
        self.preview_pages = list(result.pages)

        feedback = self._preview_settings_feedback
        if feedback is not None:
            page_index = max(0, min(feedback["page"], max(0, len(result.pages) - 1)))
            changed = page_index in self.preview_panel.changed_pages
            visible = "画面已更新" if changed else "画面无变化"
            message = f"整卷设置已应用 · 第 {page_index + 1} 页{visible}"
            if feedback.get("header_changed"):
                message += " · 已定位卷首"
            elif not changed:
                message += "，其他页面可能受影响"
            self.preview_panel.show_page_update(page_index, message)
            self._set_settings_message(message, tone="success" if changed else "info")
            self._settings_feedback = None
            self._preview_settings_feedback = None
        else:
            self.preview_panel.set_update_message("已按当前设置生成预览", tone="info")

        document_kind = str(self.raw_exam.get("document_kind", "exam"))
        if proof:
            message = f"{result.engine} 打印校对 · 共 {result.actual_pages} 页"
            self._set_status(message)
            self.preview_panel.set_update_message(message + " · 修改后自动返回快速预览", tone="success")
        elif document_kind == "exam_with_answer":
            self._set_status(f"预览估算 {result.actual_pages} 页（含答案）；打印分页以 Word 文档为准")
        elif document_kind == "answer":
            self._set_status(f"答案预览估算 {result.actual_pages} 页；打印分页以 Word 文档为准")
        else:
            self._set_status(f"快速预览 {result.actual_pages} 页 · 按内容自然分页")

        self._preview_warnings = tuple(getattr(result, "warnings", ()))
        self._refresh_score_display()

    def _finish_task_error(self, error: Exception) -> None:
        self._set_busy(False)
        if self._proof_requested:
            self._proof_requested = False
            self.preview_panel.set_proof_mode(False)
        self._set_status("任务失败，请查看提示。")
        if self._settings_feedback is not None:
            self._set_settings_message("预览更新失败；设置已保留，点击“应用并查看”重试。", tone="error")
        self._show_error(str(error))

    # ==================== 导入、导出与草稿管理 ====================

    def import_new_exam(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Word 试题",
            str(self.current_exam_path.parent) if self.current_exam_path else "",
            "Word 文档 (*.docx *.doc);;DOCX 文档 (*.docx);;旧版 DOC 文档 (*.doc)",
        )
        if path:
            self._import_exam_path(Path(path))

    def _handle_dropped_files(self, paths: tuple[Path, ...]) -> None:
        supported = [p for p in paths if p.suffix.lower() in {".docx", ".doc"}]
        if not supported:
            QMessageBox.warning(self, APP_TITLE, "请拖入 .docx 或 .doc 格式的 Word 文件。")
            return
        self._import_exam_path(supported[0])

    def _set_import_lock(self, locked: bool) -> None:
        self._import_in_progress = locked
        self.outline_panel.setEnabled(not locked)
        self.inspector_panel.set_locked(locked)
        self.preview_panel.rich_editor.set_locked(locked)
        self.export_button.setEnabled(not locked)
        self.save_button.setEnabled(not locked)
        self.undo_button.setEnabled(not locked and bool(self.undo_stack))
        self.redo_button.setEnabled(not locked and bool(self.redo_stack))
        self.cancel_import_button.setVisible(locked)
        self._set_busy(self._busy)
        if not locked:
            self._import_future = None
            self._refresh_history_buttons()

    def cancel_import(self) -> None:
        if not self._import_in_progress:
            return
        self._import_generation += 1
        if self._import_future is not None:
            self._import_future.cancel()
        self._set_import_lock(False)
        self._set_status("已取消导入，可以继续编辑当前文档。")
        self._schedule_canvas_preview()

    def _import_exam_path(self, path: Path) -> None:
        if self._export_in_progress:
            QMessageBox.information(self, APP_TITLE, "请等待当前导出完成后再导入试题。")
            return
        if self._import_in_progress:
            self.cancel_import()
        if not self._confirm_replace():
            return
        self._import_generation += 1
        generation = self._import_generation
        self._cancel_editor_timers()
        self._cancel_preview()
        self._set_import_lock(True)
        self._set_status(f"正在读取并识别：{path.name}…")

        future = self._import_future = self._export_executor.submit(import_exam, path)

        def done(completed: Future[Any]) -> None:
            if completed.cancelled():
                return
            try:
                result = completed.result()
            except Exception as exc:  # noqa: BLE001
                self._signals.importFailed.emit(generation, path, str(exc))
                return
            self._signals.importReady.emit(generation, path, result)

        future.add_done_callback(done)

    def _on_import_ready(self, generation: int, path: Path, result: dict[str, Any]) -> None:
        if self._closing or generation != self._import_generation:
            return
        self._set_import_lock(False)
        self._apply_imported_exam(path, result)

    def _on_import_failed(self, generation: int, path: Path, error: str) -> None:
        if self._closing or generation != self._import_generation:
            return
        self._set_import_lock(False)
        self._schedule_canvas_preview()
        self._set_status(f"导入失败：{path.name}")
        self._show_error(error)

    def _apply_imported_exam(self, path: Path, result: dict[str, Any]) -> None:
        self._cancel_editor_timers()
        self._cancel_preview()
        self._session_generation += 1
        self._revision = 0
        self._requested_revision = None
        self._displayed_revision = None
        self._last_export_path = None
        self._preview_warnings = ()
        self._clear_recovery()
        self.selected_content_object = None
        self._content_objects = []
        self.selected_block_index = None
        self.raw_exam = result
        self.current_exam_path = path
        self._project_path = None
        self.result_banner.hide()
        self.outline_panel.clear_search()
        self.undo_stack.clear()
        self.redo_stack.clear()

        self._load_global_fields()
        self.outline_panel.populate(self.raw_exam, keep_selection=False)
        self.preview_panel.rich_editor.load_document(self.raw_exam, self.layout_path, preserve_scroll=False)
        self.preview_panel.set_view_mode("rich")
        self._saved_signature = draft_signature(self.raw_exam)
        self._refresh_history_buttons()

        diagnostics = self.raw_exam.get("diagnostics", [])
        suffix = f"，识别提示 {len(diagnostics)} 条" if diagnostics else ""
        self._set_status(f"已导入：{path.name}{suffix}")
        self.outline_panel.select_first_question()
        self.request_preview()
        self._refresh_score_display()

    def default_export_basename(self) -> str:
        metadata = self.raw_exam.get("metadata", {})
        fallback = str(metadata.get("exam_name", "语文试卷"))
        return default_export_basename(self.current_exam_path, fallback)

    def open_export_dialog(self) -> None:
        if self._export_in_progress or self._import_in_progress:
            QMessageBox.information(self, APP_TITLE, "请等待当前导入或导出完成。")
            return
        if not self._flush_edits(show_error=True):
            return
        self._cancel_preview()

        summary = calculate_score_summary(self.raw_exam)
        lines: list[str] = []
        if summary.missing_questions:
            lines.append("未填写分值：" + "、".join(summary.missing_questions))
        if summary.total != summary.target:
            if summary.difference > 0:
                lines.append(f"当前总分比目标还差 {format_score(summary.difference)} 分")
            elif summary.difference < 0:
                lines.append(f"当前总分超出目标 {format_score(-summary.difference)} 分")
        question_count = sum(1 for block in self.raw_exam.get("blocks", []) if block.get("type") == "question")
        if self.raw_exam.get("document_kind") != "answer" and question_count == 0:
            lines.append("当前识别 0 道题，请确认文档结构是否正确")
        diagnostics = self.raw_exam.get("diagnostics", [])
        if diagnostics:
            lines.append(f"存在 {len(diagnostics)} 条识别提示")
        if lines:
            dialog = ExportWarningDialog(self, lines)
            dialog.exec()
            if not dialog.result_confirmed:
                self._schedule_canvas_preview()
                return

        directory = self.current_exam_path.parent if self.current_exam_path else Path.home() / "Documents"
        if self._last_export_path:
            directory = self._last_export_path.parent
        initial = suggested_export_path(self.current_exam_path, directory, self.default_export_basename())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Word 文档",
            str(initial),
            "Word 文档 (*.docx)",
        )
        if not path:
            self._schedule_canvas_preview()
            return
        target = Path(path)
        self.start_export(target.parent, target.stem, True, False)

    def start_export(
        self,
        output_dir: Path,
        basename: str,
        export_docx: bool = True,
        export_pdf: bool = False,
    ) -> None:
        if self._export_in_progress or self._import_in_progress:
            QMessageBox.information(self, APP_TITLE, "请等待当前任务完成。")
            return
        if not self._flush_edits(show_error=True):
            return
        self._cancel_preview()
        safe_name = basename.strip() or "语文试卷"
        destination = output_dir / f"{safe_name}.docx"
        if self.current_exam_path and destination.resolve() == self.current_exam_path.resolve():
            QMessageBox.warning(self, APP_TITLE, "请选择新的文件名，以保留导入的 Word 原件。")
            return

        raw = deepcopy(self.raw_exam)
        self._export_signature = draft_signature(raw)
        template = self.template_path
        self._export_generation += 1
        generation = self._export_generation
        self._export_in_progress = True
        self._set_busy(True)
        self._set_status("正在导出 Word 文件，请稍候…")

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

        def done(completed: Future[Any]) -> None:
            try:
                result = completed.result()
            except Exception as exc:  # noqa: BLE001
                result = exc
            self._signals.exportReady.emit(generation, result)

        future.add_done_callback(done)

    def _on_export_ready(self, generation: int, result: Any) -> None:
        if self._closing or generation != self._export_generation:
            return
        self._finish_export(result)

    def _finish_export(self, result: Any) -> None:
        self._export_in_progress = False
        if isinstance(result, ExportDestinationError):
            self._set_busy(False)
            self._set_status("目标 Word 文件正被占用或不可写。")
            if self._ask_retry_cancel(
                "无法保存 Word 文件",
                f"{result}\n\n关闭目标文件后点击“重试”；也可以点击“取消”，重新导出并选择其他文件名。",
            ):
                target = result.target
                QTimer.singleShot(100, lambda: self.start_export(target.parent, target.stem, True, False))
            return
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        generation, docx, _pdf, _engine = result
        if generation != self._export_generation:
            return
        self._set_busy(False)
        self._set_status("正式 Word 文件已导出。")
        if docx:
            self._last_export_path = Path(docx)
            self.result_banner.show_message(f"已成功导出：{Path(docx).name}")
            self._refresh_history_buttons()
        if self._export_signature != draft_signature(self.raw_exam):
            self._set_status("文件已导出，后续修改可再次导出。")
        self._schedule_canvas_preview()

    def open_export_result(self, folder: bool) -> None:
        if self._last_export_path is None:
            return
        try:
            os.startfile(self._last_export_path.parent if folder else self._last_export_path)
        except OSError as exc:
            self._show_error(f"无法打开导出结果：{exc}")

    def save_project(self, *, save_as: bool = False) -> bool:
        if self._import_in_progress or not self._flush_edits(show_error=True):
            return False
        path = None if save_as else self._project_path
        if path is None:
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "保存可继续编辑的草稿",
                f"{self.default_export_basename()}.json",
                "结构化试题草稿 (*.json)",
            )
            if not selected:
                return False
            path = Path(selected)
        try:
            save_draft(self.raw_exam, path, keep_resources=True)
        except (OSError, ValueError) as exc:
            self._show_error(f"草稿保存失败：{exc}")
            return False
        self._clear_recovery()
        self._project_path = Path(path)
        self._saved_signature = draft_signature(self.raw_exam)
        self._set_status(f"草稿已保存：{path}")
        self._refresh_history_buttons()
        return True

    def open_project(self) -> None:
        if self._export_in_progress or self._import_in_progress:
            return
        if not self._confirm_replace():
            return
        selected, _ = QFileDialog.getOpenFileName(
            self, "继续编辑草稿", "", "排版草稿 (*.json)"
        )
        if not selected:
            return
        try:
            raw = read_draft(selected)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._show_error(f"无法读取草稿：{exc}")
            return
        self._cancel_preview()
        source = raw["metadata"].get("source_docx_path") or raw["metadata"].get("answer_source_docx_path")
        self._apply_imported_exam(Path(source) if source else Path(selected), raw)
        self._project_path = Path(selected)
        self._refresh_history_buttons()
        self._set_status(f"已恢复草稿：{Path(selected).name}")

    def _autosave(self) -> None:
        if self._closing or self._saved_signature == draft_signature(self.raw_exam):
            return
        session = self._session_generation
        self._recovery.save(
            self.raw_exam,
            lambda error, s=session: self._signals.recoveryError.emit(s, str(error)),
        )

    def _on_recovery_error(self, session: int, error: str) -> None:
        if not self._closing and session == self._session_generation:
            self._set_status(f"自动草稿保存失败，请使用 Ctrl+S 另存。{error}")

    def _clear_recovery(self) -> None:
        self._autosave_timer.stop()
        self._recovery.clear()
        if self._recovery_claim is not None:
            try:
                if self._recovered_path is not None:
                    self._recovered_path.unlink(missing_ok=True)
            finally:
                self._recovery_claim.__exit__(None, None, None)
                self._recovery_claim = None
                self._recovered_path = None

    def recover_draft(self, *, automatic: bool = False) -> None:
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
                if not self._ask_yes_no("恢复自动草稿", f"发现上次未关闭的草稿《{title}》，现在继续编辑吗？"):
                    return
                if not self._confirm_replace():
                    return
                source = raw["metadata"].get("source_docx_path") or raw["metadata"].get("answer_source_docx_path")
                self._apply_imported_exam(Path(source) if source else path, raw)
                self._recovery_claim, self._recovered_path = claim, path
                self._saved_signature = "recovered"
                self._refresh_history_buttons()
                self._set_status("自动草稿已恢复，按 Ctrl+S 可选择保存位置。")
                return
            except (OSError, KeyError, TypeError, ValueError) as exc:
                self._show_error(f"无法恢复草稿：{exc}")
            finally:
                if self._recovery_claim is not claim:
                    claim.__exit__(None, None, None)
        if not automatic:
            QMessageBox.information(self, APP_TITLE, "没有可恢复的自动草稿。")

    # ==================== GitHub 更新检查与关于 ====================

    def show_about(self) -> None:
        AboutDialog(self, self._resource_path("assets/app-icon.png")).exec()

    def open_github_update_page(self) -> None:
        GitHubUpdateDialog(self, APP_VERSION).exec()

    def check_for_updates(self, *, automatic: bool = False) -> None:
        if self._pending_update is not None:
            if not automatic:
                self._offer_install()
            return
        if self._update_in_progress:
            return
        self._update_in_progress = True
        self._update_automatic = automatic
        if not automatic:
            self._set_status("正在连接 GitHub 检查更新…")
            self._set_busy(self._busy)

        def worker() -> None:
            try:
                info = check_latest_release(APP_VERSION)
                self._signals.updateChecked.emit(info)
            except Exception as exc:  # noqa: BLE001
                self._signals.updateError.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_error(self, message: str) -> None:
        self._update_in_progress = False
        if self._update_automatic:
            self._last_update_error = message
            self._check_update_action.setText("重新检查更新（上次连接失败）")
            return
        self._set_busy(self._busy)
        self._set_status("检查更新失败。")
        self._show_error(message)

    def _on_update_checked(self, info: Any) -> None:
        self._update_in_progress = False
        if not self._update_automatic:
            self._set_busy(self._busy)
        if not info.newer:
            if self._update_automatic:
                return
            self._set_status("当前已是最新版。")
            QMessageBox.information(self, APP_TITLE, f"当前版本 v{APP_VERSION} 已是最新版。")
            return
        if self._ask_yes_no(
            "发现新版本",
            f"发现新版本 v{info.version}\n\n{info.name}\n\n"
            f"更新包：{info.asset_name}\n大小：{info.asset_size / 1024 / 1024:.1f} MB\n\n"
            "是否立即下载更新包？",
        ):
            self._download_update(info)

    def _download_update(self, info: Any) -> None:
        self._update_automatic = False
        self._update_in_progress = True
        self._set_status("正在下载 GitHub 更新包…")
        self._set_busy(self._busy)

        def worker() -> None:
            try:
                path = download_release_asset(info)
                self._signals.updateDownloaded.emit(path)
            except Exception as exc:  # noqa: BLE001
                self._signals.updateError.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_downloaded(self, path: Any) -> None:
        self._update_in_progress = False
        self._set_busy(self._busy)
        self._pending_update = Path(path)
        self._offer_install()

    def _offer_install(self) -> None:
        if self._import_in_progress or self._export_in_progress:
            self._set_status("更新包已就绪。当前任务完成后可从“帮助 → 检查更新”安装。")
            return
        if self._ask_yes_no("更新包已校验", "现在安装并重新打开工作台吗？稍后也可在帮助菜单安装。"):
            self._close_with_update(self._pending_update)


__all__ = ["SessionActionsMixin"]
