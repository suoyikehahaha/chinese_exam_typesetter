"""Editor commands, undo history and validation of local formatting changes."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any
import tkinter as tk
from tkinter import messagebox

from . import inspector_model
from .inspector_model import (
    ContentObject, objects_in_scope, paragraph_format_for, set_content_object_text,
    set_inline_format, set_paragraph_format, summary_text,
)
from .page_target import use_natural_pagination
from .layout_presets import DEFAULT_PRESET, PRESET_LABELS, preset_page
from .score_summary import parse_score
from .version import APP_TITLE
from .workbench_state import validate_format


class WorkbenchEditing:
    def _push_direct_history(self) -> None:
        """Record one complete-document snapshot before inspector changes."""

        if not hasattr(self, "undo_stack"):
            return
        self._invalidate_edit()
        self.undo_stack.append(deepcopy(self.raw_exam))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.history_transaction_open = False
        self.after_idle(self._refresh_history_buttons)

    def _editor_text_modified(self, _event=None):
        if self.stem_text.edit_modified():
            self.stem_text.edit_modified(False)
            self._schedule_text_commit()

    def _schedule_text_commit(self, _event: object | None = None) -> None:
        if self._import_in_progress or self.loading_fields or self.selected_content_object is None:
            return
        if self.stem_text.get("1.0", "end-1c") == self.selected_content_object.text:
            return
        self._invalidate_edit()
        if self._text_commit_after:
            self.after_cancel(self._text_commit_after)
        self.status_var.set("文字已修改，正在等待同步…")
        self._text_commit_after = self.after(220, self._commit_text_after_delay)

    def _commit_text_after_delay(self) -> None:
        self._text_commit_after = None
        self._commit_current_text(schedule_preview=True)

    def _commit_current_text(self, *, schedule_preview: bool) -> bool:
        item = self.selected_content_object
        if item is None or self.loading_fields or self._import_in_progress:
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
        if item.block_index is None:
            self._load_global_fields()
        self.status_var.set("内容已同步，正在更新预览。")
        if schedule_preview:
            self._schedule_canvas_preview()
        return True

    def _identity_changed(self) -> None:
        if self._import_in_progress:
            return
        if self.loading_fields or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        score_text = self.score_var.get().strip()
        if score_text and parse_score(score_text) is None:
            self.warning_var.set("当前分值格式需要检查，分值可留空后继续编辑。")
            return
        question = block["question"]
        kind = "objective" if self.kind_var.get() == "客观题" else "subjective"
        score = None if not score_text else float(parse_score(score_text) or Decimal("0"))
        if question.get("kind") == kind and question.get("score") == score:
            return
        self._push_direct_history()
        question["kind"], question["score"] = kind, score
        self._update_tree_block_label(self.selected_block_index)
        self._refresh_score_display()
        self._schedule_canvas_preview()

    def _font_changed(self) -> None:
        if self._import_in_progress:
            return
        if self.loading_fields or self.selected_content_object is None:
            return
        try:
            self._format_spec_from_controls()
            font_spec = {
                "font": self.font_var.get(),
                "size_pt": float(self.size_var.get()),
                "bold": self.bold_var.get(),
            }
        except ValueError as exc:
            self.format_error_var.set(self._format_error(exc))
            return
        self.format_error_var.set("")
        self._commit_current_text(schedule_preview=False)
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
        if self._import_in_progress:
            return
        if self.loading_fields or self.selected_content_object is None:
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError as exc:
            self.format_error_var.set(self._format_error(exc))
            return
        self.format_error_var.set("")
        self._push_direct_history()
        set_paragraph_format(self.raw_exam, self.selected_content_object, spec)
        self._schedule_canvas_preview()

    @staticmethod
    def _format_error(error) -> str:
        text = str(error)
        return "请输入数字，字号为 5 至 72 磅，行距为 0.5 至 4 倍。" if "float" in text else text

    def _format_spec_from_controls(self) -> dict[str, Any]:
        special = self.special_indent_var.get()
        amount = float(self.special_indent_amount_var.get())
        first = amount if special == "首行" else (-amount if special == "悬挂" else 0.0)
        return validate_format({
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
            "indent_origin": "paragraph" if self.raw_exam.get("metadata", {}).get("layout_preset") == "national_ii_2026" else "first_line",
        })

    def _options_changed(self) -> None:
        if self._import_in_progress:
            return
        if self.loading_fields or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        try:
            size = float(self.option_size_var.get())
            left = float(self.option_left_var.get())
            hanging = float(self.option_hanging_var.get())
            if not (5 <= size <= 72 and 0 <= left <= 20 and 0 <= hanging <= 20):
                raise ValueError("选择项参数超出范围")
        except ValueError:
            self._invalid_option = True
            self.format_error_var.set("选择项字号为 5 至 72 磅，缩进为 0 至 20 字符。")
            return
        self._invalid_option = False
        self.format_error_var.set("")
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
        if self._import_in_progress:
            return
        item = self.selected_content_object
        if item is None:
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError:
            messagebox.showerror(APP_TITLE, "格式参数需要填写数字。", parent=self)
            return
        scope = self.batch_scope_var.get()
        targets = objects_in_scope(self.raw_exam, item, scope)
        if len(targets) > 1 and not messagebox.askyesno("批量应用格式", f"将格式应用到{scope}内的 {len(targets)} 个同类对象吗？可用撤销恢复。", parent=self):
            return
        self._push_direct_history()
        for target in targets:
            set_paragraph_format(self.raw_exam, target, spec)
        self.status_var.set(f"已将格式应用到 {len(targets)} 个同类型内容对象。")
        self._schedule_canvas_preview()

    def undo_action(self, _event: object | None = None) -> str:
        if self._import_in_progress:
            return "break"
        self._flush_edits(show_error=False)
        if not self.undo_stack:
            return "break"
        if self.history_transaction_open:
            self.apply_current_question(silent=True)
        self.redo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.undo_stack.pop()
        self._invalidate_edit()
        self.history_transaction_open = False
        self.selection_dirty = False
        self._restore_selection()
        self._refresh_history_buttons()
        self.request_preview()
        return "break"

    def redo_action(self, _event: object | None = None) -> str:
        if self._import_in_progress:
            return "break"
        self._flush_edits(show_error=False)
        if not self.redo_stack:
            return "break"
        self.undo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.redo_stack.pop()
        self._invalidate_edit()
        self.history_transaction_open = False
        self.selection_dirty = False
        self._restore_selection()
        self._refresh_history_buttons()
        self.request_preview()
        return "break"

    def _restore_group(self, group: str) -> None:
        if self._import_in_progress:
            return
        if group == "global":
            self._restore_global_group()
            return
        if group == "options":
            self._restore_options_group()
            return
        item = self.selected_content_object
        if item is None:
            return
        self._push_direct_history()
        current = inspector_model.paragraph_format_for(self.raw_exam, item)
        template = inspector_model.default_format_for_exam(self.raw_exam, item)
        if group == "font":
            keys = ("font", "size_pt", "bold")
        elif group == "pagination":
            keys = ("keep_with_next", "page_break_before")
        else:
            keys = (
                "left_indent_chars",
                "right_indent_chars",
                "special_indent",
                "special_indent_chars",
                "first_line_indent_chars",
                "alignment",
                "line_spacing",
                "space_before_pt",
                "space_after_pt",
                "indent_origin",
            )
        for key in keys:
            current[key] = template[key]
        inspector_model.set_paragraph_format(self.raw_exam, item, current)
        self._load_content_object(item)
        self.status_var.set("已恢复当前分组的模板值。")
        self._schedule_canvas_preview()

    def _restore_global_group(self) -> None:
        """Restore pagination defaults without replacing imported exam text."""

        self._push_direct_history()
        if self._global_commit_after:
            self.after_cancel(self._global_commit_after)
            self._global_commit_after = None
        margins = preset_page(self.raw_exam, DEFAULT_PRESET)
        metadata = self.raw_exam.setdefault("metadata", {})
        use_natural_pagination(metadata)
        metadata["layout_preset"] = DEFAULT_PRESET
        metadata["page_overrides"] = margins
        self.loading_fields = True
        try:
            if "layout_preset_var" in self.__dict__:
                self.layout_preset_var.set(PRESET_LABELS[DEFAULT_PRESET])
            self.margin_top_var.set(str(margins["margin_top_mm"]).rstrip("0").rstrip("."))
            self.margin_bottom_var.set(str(margins["margin_bottom_mm"]).rstrip("0").rstrip("."))
            self.margin_left_var.set(str(margins["margin_left_mm"]).rstrip("0").rstrip("."))
            self.margin_right_var.set(str(margins["margin_right_mm"]).rstrip("0").rstrip("."))
        finally:
            self.loading_fields = False
        self.status_var.set("已恢复模板页边距。")
        self._queue_settings_feedback()
        self._set_settings_message("已恢复模板值，正在更新预览…")
        self._schedule_canvas_preview()

    def _restore_options_group(self) -> None:
        """Restore the confirmed choice-option defaults for the selected question."""

        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        self._push_direct_history()
        question = block["question"]
        question["option_layout"] = "vertical"
        question.setdefault("format", {}).update(
            {
                "option_font": "宋体",
                "option_size_pt": 10.5,
                "option_left_indent_chars": 1.5,
                "option_hanging_indent_chars": 1.7,
                "option_indent_origin": "paragraph",
            }
        )
        self.loading_fields = True
        try:
            self.option_layout_var.set("四行单列")
            self.option_font_var.set("宋体")
            self.option_size_var.set("10.5")
            self.option_left_var.set("1.5")
            self.option_hanging_var.set("1.7")
        finally:
            self.loading_fields = False
        self.status_var.set("已恢复选择项模板值。")
        self._schedule_canvas_preview()
