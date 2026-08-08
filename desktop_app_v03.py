"""Version 0.3 workbench with one editable A4 surface and score accounting."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

import desktop_app as legacy_base
import desktop_app_current as current_runtime
import desktop_app_current_v01 as current_v02
from app.editable_a4_canvas_v03 import (
    EditableA4Canvas,
    block_editor_text,
    question_editor_lines,
)
from app.score_summary_v03 import (
    TARGET_SCORE,
    ScoreSummary,
    calculate_score_summary,
    format_score,
    parse_score,
)


APP_VERSION = "0.3.0"
APP_TITLE = current_v02.APP_TITLE
legacy_base.VERSION = APP_VERSION
current_runtime.APP_VERSION = APP_VERSION
current_v02.APP_VERSION = APP_VERSION


class CurrentDesktopApp(current_v02.CurrentDesktopApp):
    """First production pass of the Word-style v0.3 editor."""

    def __init__(self) -> None:
        self._canvas_committing = False
        self._canvas_preview_after: str | None = None
        super().__init__()
        self.score_var.trace_add("write", self._score_entry_changed)
        if hasattr(self, "score_entry"):
            self.score_entry.bind("<Return>", self._commit_score_entry, add="+")
            self.score_entry.bind("<FocusOut>", self._commit_score_entry, add="+")
        self._refresh_score_display()
        self.after_idle(self._render_editable_document)

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        score_card = ttk.LabelFrame(parent, text="分值测算", padding=(10, 7))
        score_card.pack(fill=tk.X, pady=(0, 9))
        self.score_total_label = ttk.Label(
            score_card,
            text="当前 0 / 150 分",
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self.score_total_label.pack(side=tk.LEFT)
        self.score_delta_label = ttk.Label(score_card, text="待录入分值")
        self.score_delta_label.pack(side=tk.RIGHT)
        super()._build_editor_panel(parent)

    def _build_preview_panel(self, parent: ttk.Frame) -> None:
        self.document_editor = EditableA4Canvas(
            parent,
            on_select=self._select_from_document,
            on_change=self._change_from_document,
            on_inline_format=self._format_from_document,
            on_undo=lambda: self.undo_action(),
            on_redo=lambda: self.redo_action(),
            status_variable=self.page_status_var,
        )
        self.document_editor.pack(fill=tk.BOTH, expand=True)
        self.canvas = self.document_editor.canvas

    def _load_global_fields(self) -> None:
        super()._load_global_fields()
        metadata = self.raw_exam.setdefault("metadata", {})
        metadata["total_score"] = float(TARGET_SCORE)
        self.total_score_var.set(format_score(TARGET_SCORE))

    def apply_global_settings(self) -> None:
        self.total_score_var.set(format_score(TARGET_SCORE))
        super().apply_global_settings()
        self.raw_exam.setdefault("metadata", {})["total_score"] = float(TARGET_SCORE)
        self._render_editable_document()

    def apply_current_question(self, *, silent: bool = False) -> bool:
        index = self.selected_block_index
        previous = None
        if index is not None and 0 <= index < len(self.raw_exam.get("blocks", [])):
            previous = block_editor_text(self.raw_exam["blocks"][index])
        result = super().apply_current_question(silent=silent)
        self.raw_exam.setdefault("metadata", {})["total_score"] = float(TARGET_SCORE)
        if result and index is not None and hasattr(self, "document_editor"):
            block = self.raw_exam["blocks"][index]
            current = block_editor_text(block)
            if previous != current or not self._canvas_committing:
                self.document_editor.update_block(index, current, block)
        self._refresh_score_display()
        return result

    def apply_format_and_preview(self) -> None:
        super().apply_format_and_preview()
        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        self.document_editor.update_block(
            self.selected_block_index,
            block_editor_text(block),
            block,
        )

    def _import_exam_path(self, path: Any) -> None:
        super()._import_exam_path(path)
        self.raw_exam.setdefault("metadata", {})["total_score"] = float(TARGET_SCORE)
        self._refresh_score_display()
        self._render_editable_document()

    def open_export_dialog(self) -> None:
        self.apply_current_question(silent=True)
        summary = calculate_score_summary(self.raw_exam)
        if not summary.complete:
            detail = self._score_difference_text(summary)
            if summary.missing_questions:
                detail += "\n未填写分值：" + "、".join(summary.missing_questions)
            messagebox.showwarning(
                APP_TITLE,
                "满分校验未通过，暂不能正式导出。\n" + detail,
                parent=self,
            )
            return
        super().open_export_dialog()

    def _finish_preview(self, result: object) -> None:
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
        self.document_editor.set_preview_pages(
            self.preview_pages,
            locators=result.locators,
            actual_pages=result.actual_pages,
            raw_exam=self.raw_exam,
            selected_key=self.selected_block_index,
        )
        self.status_var.set(
            f"A4 编辑画布已校准：实际 {result.actual_pages} 页，目标 {result.target_pages} 页"
        )
        if self._pending_preview_block is not None:
            block_index = self._pending_preview_block
            self.after(20, lambda: self._jump_to_block(block_index))

    def _show_current_page(self) -> None:
        if hasattr(self, "document_editor"):
            self.document_editor._update_status(self.document_editor.current_page())

    def _jump_to_block(self, block_index: int) -> None:
        if hasattr(self, "document_editor"):
            self._pending_preview_block = None
            self.document_editor.scroll_to(block_index)
            self.status_var.set("已定位到 A4 编辑画布中的对应内容。")

    def previous_page(self) -> None:
        if hasattr(self, "document_editor"):
            self.document_editor.previous_page()

    def next_page(self) -> None:
        if hasattr(self, "document_editor"):
            self.document_editor.next_page()

    def change_zoom(self, delta: float) -> None:
        if hasattr(self, "document_editor"):
            self.document_editor.change_zoom(delta)

    def _populate_tree(self) -> None:
        super()._populate_tree()
        if not hasattr(self, "tree"):
            return
        summary = calculate_score_summary(self.raw_exam)
        roots = self.tree.get_children("")
        if roots:
            self._set_tree_score(roots[0], summary.total)
        for section in summary.sections:
            if section.block_index is not None:
                self._set_tree_score(f"block-{section.block_index}", section.total)
        self._refresh_score_display(summary)

    def _set_tree_score(self, iid: str, score: Decimal) -> None:
        if not self.tree.exists(iid):
            return
        values = list(self.tree.item(iid, "values"))
        while len(values) < 2:
            values.append("")
        values[1] = format_score(score)
        self.tree.item(iid, values=values)

    def _refresh_score_display(self, summary: ScoreSummary | None = None) -> None:
        if not hasattr(self, "score_total_label"):
            return
        state = summary or calculate_score_summary(self.raw_exam)
        self.score_total_label.configure(
            text=f"当前 {format_score(state.total)} / 150 分"
        )
        text = self._score_difference_text(state)
        if state.missing_questions:
            text += f"，{len(state.missing_questions)} 题待填"
        if state.complete:
            color = "#107C10"
        elif state.difference < 0:
            color = "#C42B1C"
        else:
            color = "#9A6700"
        self.score_total_label.configure(foreground=color)
        self.score_delta_label.configure(text=text, foreground=color)

    @staticmethod
    def _score_difference_text(summary: ScoreSummary) -> str:
        if summary.difference > 0:
            return f"还差 {format_score(summary.difference)} 分"
        if summary.difference < 0:
            return f"超出 {format_score(-summary.difference)} 分"
        return "总分已达到 150 分"

    def _score_entry_changed(self, *_args: object) -> None:
        if getattr(self, "loading_fields", False) or self.selected_block_index is None:
            return
        if self.score_var.get().strip() and parse_score(self.score_var.get()) is None:
            self.score_delta_label.configure(text="分值格式有误", foreground="#C42B1C")

    def _commit_score_entry(self, _event: object | None = None) -> str:
        if self.selected_block_index is None:
            return "break"
        if self.score_var.get().strip() and parse_score(self.score_var.get()) is None:
            messagebox.showerror(APP_TITLE, "分值需要填写非负数字。", parent=self)
            return "break"
        self.apply_current_question(silent=True)
        self._populate_tree()
        self._schedule_canvas_preview()
        return "break"

    def _render_editable_document(self) -> None:
        """Keep the right side tied to the latest rendered page images."""

        return

    def _on_tree_select(self, event: object) -> None:
        """Load the left editor and move the preview to the same block."""

        super()._on_tree_select(event)
        selection = self.tree.selection() if hasattr(self, "tree") else ()
        if not selection or not str(selection[0]).startswith("block-"):
            return
        try:
            block_index = int(str(selection[0]).split("-", 1)[1])
        except ValueError:
            return
        if self.document_editor.block_pages:
            self.after_idle(lambda: self._jump_to_block(block_index))
        else:
            self._pending_preview_block = block_index
            if not self.busy:
                self.request_preview()

    def _select_from_document(self, key: object) -> None:
        if isinstance(key, str) and key.startswith("meta:"):
            roots = self.tree.get_children("")
            if roots:
                self.tree.selection_set(roots[0])
                self.tree.focus(roots[0])
            self.selected_block_index = None
            return
        if not isinstance(key, int):
            return
        iid = f"block-{key}"
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            self._on_tree_select(None)

    def _change_from_document(self, key: object, value: str, old_value: str) -> None:
        if value == old_value:
            return
        self._push_direct_history()
        self._canvas_committing = True
        try:
            if isinstance(key, str) and key.startswith("meta:"):
                self._commit_metadata_text(key, value)
            elif isinstance(key, int) and 0 <= key < len(self.raw_exam.get("blocks", [])):
                self._commit_block_text(self.raw_exam["blocks"][key], value)
                self.selected_block_index = key
                self._sync_left_editor(key)
        finally:
            self._canvas_committing = False
        self.raw_exam.setdefault("metadata", {})["total_score"] = float(TARGET_SCORE)
        self._populate_tree()
        self.status_var.set("A4 画布内容已同步，正在校准分页……")
        self._schedule_canvas_preview()

    def _push_direct_history(self) -> None:
        if not hasattr(self, "undo_stack"):
            return
        self.undo_stack.append(deepcopy(self.raw_exam))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.history_transaction_open = False

    def _commit_metadata_text(self, key: str, value: str) -> None:
        metadata = self.raw_exam.setdefault("metadata", {})
        field = key.split(":", 1)[1]
        if field == "notices":
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            if lines and lines[0].rstrip("：:") in {"注意事项", "考生须知"}:
                lines.pop(0)
            metadata["notices"] = [
                re.sub(r"^\s*\d+[．.]\s*", "", line) for line in lines
            ]
        else:
            metadata[field] = value.strip()

    def _commit_block_text(self, block: dict[str, Any], value: str) -> None:
        if block.get("type") != "question":
            self._commit_nonquestion_content(block, value)
            return
        question = block.get("question", {})
        if not isinstance(question, dict):
            return
        lines = value.splitlines()
        _old_lines, mapping = question_editor_lines(question)
        for line_index, item in enumerate(mapping):
            if line_index >= len(lines):
                break
            text = lines[line_index]
            target = str(item["target"])
            target_index = int(item["target_index"])
            if target == "stem":
                question["stem"] = text
            elif target == "option" and target_index < len(question.get("options", [])):
                question["options"][target_index] = text
            elif target == "embedded" and target_index < len(question.get("embedded_segments", [])):
                self._replace_embedded_line(question, target_index, text)
            elif target == "segmentation":
                question["segmentation_text"] = text
            elif target in question and target_index < len(question[target]):
                question[target][target_index] = text

    @staticmethod
    def _replace_embedded_line(
        question: dict[str, Any],
        target_index: int,
        text: str,
    ) -> None:
        old_segments = question["embedded_segments"][target_index]
        label = "".join(
            str(item.get("text", ""))
            for item in old_segments
            if item.get("role") == "label"
        )
        if label and text.startswith(label):
            question["embedded_segments"][target_index] = [
                {"text": label, "role": "label"},
                {"text": text[len(label):], "role": "body"},
            ]
        else:
            question["embedded_segments"][target_index] = [
                {"text": text, "role": "body"}
            ]

    def _sync_left_editor(self, index: int) -> None:
        block = self.raw_exam["blocks"][index]
        self.loading_fields = True
        try:
            if block.get("type") == "question":
                self._load_question_fields(block["question"])
            else:
                self._load_nonquestion_fields(block)
        finally:
            self.loading_fields = False

    def _format_from_document(
        self,
        key: object,
        start: str,
        end: str,
        spec: dict[str, Any],
    ) -> None:
        if not isinstance(key, int) or not 0 <= key < len(self.raw_exam.get("blocks", [])):
            return
        self._push_direct_history()
        block = self.raw_exam["blocks"][key]
        start_line, start_col = (int(value) for value in start.split("."))
        end_line, end_col = (int(value) for value in end.split("."))
        if block.get("type") == "question":
            self._append_question_inline_formats(
                key,
                block["question"],
                start_line,
                start_col,
                end_line,
                end_col,
                spec,
            )
            block["question"].setdefault("format", {})["alignment"] = spec["alignment"]
        else:
            block.setdefault("inline_formats", []).append(
                {
                    "target": "block",
                    "target_index": start_line - 1,
                    "line": start_line - 1,
                    "start": start_col,
                    "end": end_col,
                    "font": spec["font"],
                    "size_pt": spec["size_pt"],
                    "bold": spec["bold"],
                }
            )
            block.setdefault("format", {})["alignment"] = spec["alignment"]
        self.status_var.set("选中文字格式已同步。")
        self._schedule_canvas_preview()

    def _append_question_inline_formats(
        self,
        key: int,
        question: dict[str, Any],
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        spec: dict[str, Any],
    ) -> None:
        _lines, mapping = question_editor_lines(question)
        entries = question.setdefault("inline_formats", [])
        widget = self.document_editor.text_widgets[key]
        for line in range(start_line, end_line + 1):
            if line - 1 >= len(mapping):
                continue
            text = widget.get(f"{line}.0", f"{line}.end")
            left = start_col if line == start_line else 0
            right = end_col if line == end_line else len(text)
            if left >= right:
                continue
            entry = dict(mapping[line - 1])
            entry.update(
                {
                    "line": line - 1,
                    "start": left,
                    "end": right,
                    "font": spec["font"],
                    "size_pt": spec["size_pt"],
                    "bold": spec["bold"],
                }
            )
            entries.append(entry)

    def _schedule_canvas_preview(self) -> None:
        if self._canvas_preview_after:
            self.after_cancel(self._canvas_preview_after)
        self._canvas_preview_after = self.after(650, self._run_canvas_preview)

    def _run_canvas_preview(self) -> None:
        self._canvas_preview_after = None
        self.request_preview()

    def undo_action(self, _event: object | None = None) -> str:
        result = super().undo_action(_event)
        self._render_editable_document()
        self._refresh_score_display()
        return result

    def redo_action(self, _event: object | None = None) -> str:
        result = super().redo_action(_event)
        self._render_editable_document()
        self._refresh_score_display()
        return result


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "main"]
