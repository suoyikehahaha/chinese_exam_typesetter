"""主工作台的富文本直编联动、格式校验、撤销重做与整卷设置逻辑。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QTimer

from ..config import load_layout
from ..inspector_model import (
    ContentObject,
    clear_inline_formats,
    content_objects_for_block,
    default_format_for_exam,
    inline_formats_for,
    merge_inline_format,
    metadata_content_objects,
    objects_in_scope,
    paragraph_format_for,
    set_content_object_text,
    set_paragraph_format,
)
from ..layout_presets import DEFAULT_PRESET, PRESET_LABELS, preset_page
from ..page_target import use_natural_pagination
from ..review_items import review_items
from ..score_summary import ScoreSummary, format_score, parse_score
from ..workbench_state import validate_format
from .outline_panel import block_display_label


class EditingActionsMixin:
    """封装富文本纸面直编、大纲联动、精细排版校验、批量套用、撤销重做与整卷设置。"""

    # ==================== 富文本画布双向实时联动 ====================

    def _rich_editor(self):
        panel = getattr(self, "preview_panel", None)
        return getattr(panel, "rich_editor", None)

    def _sync_rich_document(self, *, preserve_scroll: bool = True, restore_key: str | None = None) -> None:
        rich = self._rich_editor()
        if rich is not None and hasattr(self, "layout_path"):
            rich.load_document(
                self.raw_exam,
                self.layout_path,
                preserve_scroll=preserve_scroll,
                restore_key=restore_key,
            )

    def _refresh_rich_objects(self, keys: list[str]) -> None:
        rich = self._rich_editor()
        if rich is not None and keys:
            rich.refresh_objects(self.raw_exam, keys)

    def _on_rich_cursor_object_changed(self, item: ContentObject) -> None:
        """在仿真纸面点击或拖选文本时，静默同步左栏大纲和右栏精细面板（不跳转、不抢焦点）。"""

        if self._import_in_progress:
            return
        if self.selected_content_object is not None and self.selected_content_object.key == item.key:
            return

        block_index = item.block_index
        ins = self.inspector_panel
        if block_index != self.selected_block_index:
            self.selected_block_index = block_index
            self.outline_panel.select_block(block_index, emit=False)
            block = (
                self.raw_exam["blocks"][block_index]
                if block_index is not None and 0 <= block_index < len(self.raw_exam.get("blocks", []))
                else None
            )
            ins.set_loading(True)
            try:
                if block is not None and block.get("type") == "question":
                    question = block["question"]
                    kind = "客观题" if question.get("kind") == "objective" else "主观题"
                    score = parse_score(question.get("score"))
                    ins.set_question_mode(True, kind=kind, score="" if score is None else format_score(score))
                    ins.set_selection_title(f"第 {question.get('number', '?')} 题")
                else:
                    ins.set_question_mode(False)
                    ins.set_selection_title(block_display_label(block)[:38] if block else "卷首信息")
                self._content_objects = (
                    content_objects_for_block(block, block_index)
                    if block is not None
                    else metadata_content_objects(self.raw_exam)
                )
            finally:
                ins.set_loading(False)

        matched = next((obj for obj in self._content_objects if obj.key == item.key), item)
        self.selected_content_object = matched
        self._load_content_object(matched)

    def _on_rich_object_text_edited(self, old_item: ContentObject, new_text: str) -> None:
        """在仿真纸面直接改字后，实时写回唯一数据源并同步大纲、整卷字段与后台预览。"""

        if self._import_in_progress:
            return
        self._push_direct_history()
        set_content_object_text(self.raw_exam, old_item, new_text)
        updated = ContentObject(
            old_item.key,
            old_item.block_index,
            old_item.target,
            old_item.target_index,
            old_item.role,
            old_item.label,
            new_text,
            old_item.line_index,
        )
        self._content_objects = [
            updated if current.key == old_item.key else current for current in self._content_objects
        ]
        if self.selected_content_object is not None and self.selected_content_object.key == old_item.key:
            self.selected_content_object = updated
        self.outline_panel.update_block_label(self.raw_exam, old_item.block_index)
        if old_item.block_index is None:
            self._load_global_fields()
        self._set_status("纸面文字已同步，正在更新分页预览。")
        self._schedule_canvas_preview()

    def _on_rich_inline_style(self, patch: dict[str, Any]) -> None:
        """响应顶部 Word 快捷条或选区浮动迷你栏的字体/字号/加粗/下划线/着重号设定。"""

        rich = self._rich_editor()
        if self._import_in_progress or rich is None:
            return
        rich.flush_pending_text()
        segments = rich.selected_segments()
        if not segments:
            return
        editable = [seg for seg in segments if seg.item.block_index is not None]
        if not editable:
            self._set_status("卷首版式由国标模板统一控制，可在纸面直接修改文字。")
            rich._sync_toolbars_from_cursor()
            return

        clean_patch: dict[str, Any] = {}
        if "font" in patch:
            clean_patch["font"] = str(patch["font"])
        if "size_pt" in patch:
            val = float(patch["size_pt"])
            if not (5 <= val <= 72):
                return
            clean_patch["size_pt"] = val
        if "bold" in patch:
            clean_patch["bold"] = bool(patch["bold"])
        if "underline" in patch:
            clean_patch["underline"] = bool(patch["underline"])
        if "emphasis" in patch:
            clean_patch["emphasis"] = str(patch["emphasis"] or "")
        if not clean_patch:
            return

        self._push_direct_history()
        has_selection = rich.has_text_selection()
        para_patch = {k: v for k, v in clean_patch.items() if k in {"font", "size_pt", "bold"}}
        dec_patch = {k: v for k, v in clean_patch.items() if k in {"underline", "emphasis"}}

        touched_keys: list[str] = []
        for seg in editable:
            touched_keys.append(seg.item.key)
            if has_selection and not seg.whole_object:
                merge_inline_format(self.raw_exam, seg.item, seg.start, seg.end, clean_patch)
            else:
                if para_patch:
                    spec = paragraph_format_for(self.raw_exam, seg.item)
                    spec.update(para_patch)
                    set_paragraph_format(self.raw_exam, seg.item, spec)
                    if seg.item.text:
                        merge_inline_format(
                            self.raw_exam,
                            seg.item,
                            0,
                            len(seg.item.text),
                            {k: None for k in para_patch},
                        )
                if dec_patch and seg.end > seg.start:
                    merge_inline_format(self.raw_exam, seg.item, seg.start, seg.end, dec_patch)

        rich.refresh_objects(self.raw_exam, touched_keys)
        if self.selected_content_object is not None and self.selected_content_object.key in touched_keys:
            self._load_content_object(self.selected_content_object)
        if has_selection and len(editable) == 1 and not editable[0].whole_object:
            self._set_status("已将格式应用到选中文字。")
        else:
            self._set_status(f"已更新 {len(editable)} 个段落的格式。")
        self._schedule_canvas_preview()

    def _on_rich_clear_inline(self) -> None:
        """清除富文本画布当前选区或当前段落的局部字符格式。"""

        rich = self._rich_editor()
        if self._import_in_progress or rich is None:
            return
        rich.flush_pending_text()
        segments = [seg for seg in rich.selected_segments() if seg.item.block_index is not None]
        if not segments:
            return
        self._push_direct_history()
        has_selection = rich.has_text_selection()
        keys: list[str] = []
        for seg in segments:
            keys.append(seg.item.key)
            if has_selection and not seg.whole_object:
                clear_inline_formats(self.raw_exam, seg.item, seg.start, seg.end)
            else:
                clear_inline_formats(self.raw_exam, seg.item)
        rich.refresh_objects(self.raw_exam, keys)
        if self.selected_content_object is not None and self.selected_content_object.key in keys:
            self._load_content_object(self.selected_content_object)
        self._set_status("已清除局部字符格式。")
        self._schedule_canvas_preview()

    def _on_rich_paragraph_style(self, patch: dict[str, Any]) -> None:
        """在富文本画布中直接调整单段或跨多段的对齐方式与行距。"""

        rich = self._rich_editor()
        if self._import_in_progress or rich is None:
            return
        rich.flush_pending_text()
        segments = [seg for seg in rich.selected_segments() if seg.item.block_index is not None]
        if not segments:
            self._set_status("卷首段落版式由国标模板统一控制。")
            rich._sync_toolbars_from_cursor()
            return
        if "line_spacing" in patch:
            val = float(patch["line_spacing"])
            if not (0.5 <= val <= 4.0):
                return
        self._push_direct_history()
        keys: list[str] = []
        seen: set[str] = set()
        for seg in segments:
            if seg.item.key in seen:
                continue
            seen.add(seg.item.key)
            keys.append(seg.item.key)
            spec = paragraph_format_for(self.raw_exam, seg.item)
            spec.update(patch)
            set_paragraph_format(self.raw_exam, seg.item, spec)
        rich.refresh_objects(self.raw_exam, keys)
        if self.selected_content_object is not None and self.selected_content_object.key in seen:
            self._load_content_object(self.selected_content_object)
        self._set_status(f"已更新 {len(keys)} 个段落的排版属性。")
        self._schedule_canvas_preview()

    # ==================== 结构大纲与对象选择 ====================

    def _on_outline_select(self, block_index: int | None) -> None:
        if self._import_in_progress:
            return
        if block_index == self.selected_block_index and self.selected_content_object is not None:
            return
        if not self.apply_current_question(silent=False):
            self.outline_panel.select_block(self.selected_block_index, emit=False)
            return

        self.selected_content_object = None
        self.selected_block_index = block_index
        block = (
            self.raw_exam["blocks"][block_index]
            if block_index is not None and 0 <= block_index < len(self.raw_exam.get("blocks", []))
            else None
        )

        ins = self.inspector_panel
        ins.set_loading(True)
        try:
            if block is not None and block.get("type") == "question":
                question = block["question"]
                kind = "客观题" if question.get("kind") == "objective" else "主观题"
                score = parse_score(question.get("score"))
                ins.set_question_mode(True, kind=kind, score="" if score is None else format_score(score))
                ins.set_selection_title(f"第 {question.get('number', '?')} 题")
            else:
                ins.set_question_mode(False)
                ins.set_selection_title(block_display_label(block)[:38] if block else "卷首信息")

            self._content_objects = (
                content_objects_for_block(block, block_index)
                if block is not None
                else metadata_content_objects(self.raw_exam)
            )
            if self._content_objects:
                self._select_object(self._content_objects[0].key, navigate=False)
            else:
                ins.set_native_readonly_placeholder()
        finally:
            ins.set_loading(False)

        self._on_inspector_tab_changed(ins.inspector_tabs.currentIndex())
        if block_index is not None:
            self._jump_to_block(block_index)

    def _select_object(self, key: str, *, navigate: bool) -> None:
        if self._import_in_progress:
            return
        if self.selected_content_object is not None and self.selected_content_object.key == key:
            return
        item = next((obj for obj in self._content_objects if obj.key == key), None)
        if item is None:
            return
        rich = self._rich_editor()
        if rich is not None:
            rich.flush_pending_text()

        self.selected_content_object = item
        self._load_content_object(item)

        if navigate:
            if item.key in self.preview_panel.locators:
                QTimer.singleShot(0, lambda k=item.key: self.preview_panel.scroll_to(k, highlight=True))
            elif item.block_index is not None:
                self._jump_to_block(item.block_index)

    def _load_content_object(self, item: ContentObject) -> None:
        self._invalid_option = False
        spec = paragraph_format_for(self.raw_exam, item)
        inlines = inline_formats_for(self.raw_exam, item)
        option_spec: dict[str, Any] | None = None
        option_layout = "四行单列"
        if self.selected_block_index is not None:
            block = self.raw_exam["blocks"][self.selected_block_index]
            if block.get("type") == "question":
                q = block.get("question", {})
                option_spec = q.get("format", {})
                option_layout = "两行两列" if q.get("option_layout") == "two_column" else "四行单列"
        self.inspector_panel.load_content_object(
            item, spec, inlines, option_spec=option_spec, option_layout=option_layout
        )

    def _on_inspector_tab_changed(self, selected: int) -> None:
        if selected == 1:
            self.inspector_panel.set_selection_title("整卷设置 · 影响所有页面")
        elif self.selected_block_index is not None:
            block = self.raw_exam["blocks"][self.selected_block_index]
            self.inspector_panel.set_selection_title(block_display_label(block)[:38])
        else:
            self.inspector_panel.set_selection_title("卷首信息")

    def _jump_to_block(self, block_index: int) -> None:
        key: object = block_index
        if self.selected_content_object is not None and self.selected_content_object.block_index == block_index:
            key = self.selected_content_object.key
        self.preview_panel.scroll_to(key, highlight=True)

    # ==================== 编辑提交、精细排版校验与撤销重做 ====================

    def _invalidate_edit(self) -> None:
        self._revision += 1
        self._requested_revision = None
        self._cancel_preview()
        self.raw_exam.get("metadata", {}).pop("page_spacing_scale", None)
        self.preview_panel.set_update_message("修改已记录，正在更新预览…", tone="info", show_banner=False)
        self._autosave_timer.start()

    def _push_direct_history(self) -> None:
        self._invalidate_edit()
        self.undo_stack.append(deepcopy(self.raw_exam))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        QTimer.singleShot(0, self._refresh_history_buttons)

    def apply_current_question(self, *, silent: bool = False) -> bool:
        rich = self._rich_editor()
        if rich is not None:
            rich.flush_pending_text()
        if self.selected_block_index is None:
            return True
        blocks = self.raw_exam.get("blocks", [])
        if not (0 <= self.selected_block_index < len(blocks)):
            return True
        block = blocks[self.selected_block_index]
        if block.get("type") != "question":
            return True
        score_text = self.inspector_panel.score_entry.text().strip()
        if score_text and parse_score(score_text) is None:
            self.inspector_panel.set_score_invalid(True)
            if not silent:
                self._show_error("分值需要填写非负数字，也可以暂时留空。")
            return False
        self.inspector_panel.set_score_invalid(False)
        question = block["question"]
        question["kind"] = "objective" if self.inspector_panel.kind_combo.currentText() == "客观题" else "subjective"
        question["score"] = None if not score_text else float(parse_score(score_text) or Decimal("0"))
        question["option_layout"] = (
            "two_column" if self.inspector_panel.option_layout_combo.currentText() == "两行两列" else "vertical"
        )
        self._refresh_score_display()
        return True

    def _on_identity_changed(self) -> None:
        if self._import_in_progress or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        score_text = self.inspector_panel.score_entry.text().strip()
        if score_text and parse_score(score_text) is None:
            self.inspector_panel.set_score_invalid(True)
            self._set_status("当前分值格式需要检查，可留空后继续编辑。")
            return
        self.inspector_panel.set_score_invalid(False)
        question = block["question"]
        kind = "objective" if self.inspector_panel.kind_combo.currentText() == "客观题" else "subjective"
        score = None if not score_text else float(parse_score(score_text) or Decimal("0"))
        if question.get("kind") == kind and question.get("score") == score:
            return
        self._push_direct_history()
        question["kind"] = kind
        question["score"] = score
        self.outline_panel.update_block_label(self.raw_exam, self.selected_block_index)
        self._refresh_score_display()
        self._sync_rich_document(preserve_scroll=True)
        self._schedule_canvas_preview()

    def _commit_score_entry(self) -> None:
        if self._import_in_progress:
            return
        score_text = self.inspector_panel.score_entry.text().strip()
        if score_text and parse_score(score_text) is None:
            self.inspector_panel.set_score_invalid(True)
            self._set_status("当前题分值格式需要检查，可留空后继续编辑。")
            return
        self.apply_current_question(silent=True)
        self.outline_panel.update_block_label(self.raw_exam, self.selected_block_index)
        self._sync_rich_document(preserve_scroll=True)
        self._schedule_canvas_preview()

    def _format_spec_from_controls(self) -> dict[str, Any]:
        """基于当前对象的真实字形/对齐/行距，叠加右栏精细缩进、段距与分页参数并校验。"""

        ins = self.inspector_panel
        base = (
            paragraph_format_for(self.raw_exam, self.selected_content_object)
            if self.selected_content_object is not None
            else {}
        )
        special = ins.special_indent_combo.currentText()
        amount = float(ins.special_amount_edit.text().strip())
        first = amount if special == "首行" else (-amount if special == "悬挂" else 0.0)
        return validate_format(
            {
                "font": str(base.get("font", "宋体")),
                "size_pt": float(base.get("size_pt", 10.5)),
                "bold": bool(base.get("bold", False)),
                "alignment": str(base.get("alignment", "左对齐")),
                "line_spacing": float(base.get("line_spacing", 1.25)),
                "left_indent_chars": float(ins.left_indent_edit.text().strip()),
                "right_indent_chars": float(ins.right_indent_edit.text().strip()),
                "special_indent": special,
                "special_indent_chars": amount,
                "first_line_indent_chars": first,
                "space_before_pt": float(ins.space_before_edit.text().strip()),
                "space_after_pt": float(ins.space_after_edit.text().strip()),
                "keep_with_next": ins.keep_next_check.isChecked(),
                "page_break_before": ins.page_break_check.isChecked(),
                "indent_origin": (
                    "paragraph"
                    if self.raw_exam.get("metadata", {}).get("layout_preset") == "national_ii_2026"
                    else "first_line"
                ),
            }
        )

    @staticmethod
    def _friendly_format_error(exc: Exception) -> str:
        text = str(exc)
        return "请输入有效数字：缩进 0～20 字符，段前/段后 0～72 磅。" if "float" in text else text

    def _on_paragraph_changed(self) -> None:
        if self._import_in_progress or self.selected_content_object is None:
            return
        if self.selected_content_object.block_index is None:
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError as exc:
            self.inspector_panel.set_format_error(self._friendly_format_error(exc))
            return
        self.inspector_panel.set_format_error("")
        self._push_direct_history()
        set_paragraph_format(self.raw_exam, self.selected_content_object, spec)
        self._refresh_rich_objects([self.selected_content_object.key])
        self._schedule_canvas_preview()

    def _on_options_changed(self) -> None:
        if self._import_in_progress or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        ins = self.inspector_panel
        try:
            size = float(ins.option_size_combo.currentText().strip())
            left = float(ins.option_left_edit.text().strip())
            hanging = float(ins.option_hanging_edit.text().strip())
            if not (5 <= size <= 72 and 0 <= left <= 20 and 0 <= hanging <= 20):
                raise ValueError("选择项参数超出范围")
        except ValueError:
            self._invalid_option = True
            ins.set_format_error("选择项字号需在 5～72 磅，缩进需在 0～20 字符之间。")
            return
        self._invalid_option = False
        ins.set_format_error("")
        self._push_direct_history()
        question = block["question"]
        question["option_layout"] = (
            "two_column" if ins.option_layout_combo.currentText() == "两行两列" else "vertical"
        )
        question.setdefault("format", {}).update(
            {
                "option_font": ins.option_font_combo.currentText(),
                "option_size_pt": size,
                "option_left_indent_chars": left,
                "option_hanging_indent_chars": hanging,
            }
        )
        option_keys = [obj.key for obj in self._content_objects if obj.target == "option"]
        self._refresh_rich_objects(option_keys)
        self._schedule_canvas_preview()

    def _apply_same_type(self) -> None:
        if self._import_in_progress or self.selected_content_object is None:
            return
        if self.selected_content_object.block_index is None:
            self._set_status("卷首版式由国标模板统一控制，不支持批量套用。")
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError:
            self._show_error("当前格式参数需要填写有效数字。")
            return
        scope = self.inspector_panel.batch_scope_combo.currentText()
        targets = objects_in_scope(self.raw_exam, self.selected_content_object, scope)
        if len(targets) > 1:
            if not self._ask_yes_no(
                "批量应用格式",
                f"将当前格式应用到{scope}内的 {len(targets)} 个同类对象吗？\n应用后仍可通过“撤销”恢复。",
            ):
                return
        self._push_direct_history()
        for target in targets:
            set_paragraph_format(self.raw_exam, target, spec)
        self._refresh_rich_objects([target.key for target in targets])
        self._set_status(f"已将格式应用到 {len(targets)} 个同类型内容对象。")
        self._schedule_canvas_preview()

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
        if item is None or item.block_index is None:
            return
        self._push_direct_history()
        current = paragraph_format_for(self.raw_exam, item)
        template = default_format_for_exam(self.raw_exam, item)
        if group == "pagination":
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
        set_paragraph_format(self.raw_exam, item, current)
        self._load_content_object(item)
        self._refresh_rich_objects([item.key])
        self._set_status("已恢复当前分组的模板默认值。")
        self._schedule_canvas_preview()

    def _restore_global_group(self) -> None:
        self._push_direct_history()
        self._global_commit_timer.stop()
        margins = preset_page(self.raw_exam, DEFAULT_PRESET)
        metadata = self.raw_exam.setdefault("metadata", {})
        use_natural_pagination(metadata)
        metadata["layout_preset"] = DEFAULT_PRESET
        metadata["page_overrides"] = margins
        self._global_invalid = False
        self._load_global_fields()
        if hasattr(self, "_sync_rich_document"):
            self._sync_rich_document(preserve_scroll=True)
        self._set_status("已恢复模板页边距。")
        self._queue_settings_feedback()
        self._set_settings_message("已恢复模板值，正在更新预览…", tone="info")
        self._schedule_canvas_preview()

    def _restore_options_group(self) -> None:
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
        if self.selected_content_object is not None:
            self._load_content_object(self.selected_content_object)
        option_keys = [obj.key for obj in getattr(self, "_content_objects", []) if obj.target == "option"]
        if hasattr(self, "_refresh_rich_objects"):
            self._refresh_rich_objects(option_keys)
        self._set_status("已恢复选择项模板默认值。")
        self._schedule_canvas_preview()

    def undo_action(self) -> None:
        if self._import_in_progress:
            return
        self._flush_edits(show_error=False)
        if not self.undo_stack:
            return
        self.redo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.undo_stack.pop()
        self._invalidate_edit()
        self._restore_selection()
        self._refresh_history_buttons()
        self.request_preview()

    def redo_action(self) -> None:
        if self._import_in_progress:
            return
        self._flush_edits(show_error=False)
        if not self.redo_stack:
            return
        self.undo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.redo_stack.pop()
        self._invalidate_edit()
        self._restore_selection()
        self._refresh_history_buttons()
        self.request_preview()

    def _restore_selection(self) -> None:
        key = self.selected_content_object.key if self.selected_content_object else None
        block_index = self.selected_block_index
        self.selected_content_object = None
        self.selected_block_index = None
        self._load_global_fields()
        self._sync_rich_document(preserve_scroll=True, restore_key=key)
        self.outline_panel.populate(self.raw_exam, keep_selection=False)
        if not self.outline_panel.select_block(block_index, emit=True):
            self.outline_panel.select_block(None, emit=True)
        if key:
            self._select_object(key, navigate=False)
        self._refresh_score_display()

    # ==================== 整卷设置与反馈 ====================

    def _load_global_fields(self) -> None:
        self._global_invalid = False
        metadata = self.raw_exam.setdefault("metadata", {})
        metadata["layout_preset"] = DEFAULT_PRESET
        use_natural_pagination(metadata)
        page = load_layout(self.layout_path).get("page", {})
        overrides = metadata.get("page_overrides", {})
        defaults = preset_page(self.raw_exam, DEFAULT_PRESET)

        ins = self.inspector_panel
        ins.set_loading(True)
        try:
            ins.template_label.setText(PRESET_LABELS[DEFAULT_PRESET])
            ins.exam_name_edit.setText(str(metadata.get("exam_name", "")))
            ins.exam_name_edit.setCursorPosition(0)
            ins.subject_name_edit.setText(str(metadata.get("subject_name", "语　文")))
            ins.subject_name_edit.setCursorPosition(0)
            ins.exam_meta_edit.setText(str(metadata.get("meta_text", "")))
            ins.exam_meta_edit.setCursorPosition(0)
            ins.document_scope_combo.setCurrentText(
                "局部训练" if metadata.get("document_scope") == "practice" else "全卷"
            )
            ins.margin_top_edit.setText(
                ins._clean_num(overrides.get("margin_top_mm", defaults.get("margin_top_mm", page.get("margin_top_mm", 25.4))))
            )
            ins.margin_bottom_edit.setText(
                ins._clean_num(overrides.get("margin_bottom_mm", defaults.get("margin_bottom_mm", page.get("margin_bottom_mm", 25.4))))
            )
            ins.margin_left_edit.setText(
                ins._clean_num(overrides.get("margin_left_mm", defaults.get("margin_left_mm", page.get("margin_left_mm", 31.75))))
            )
            ins.margin_right_edit.setText(
                ins._clean_num(overrides.get("margin_right_mm", defaults.get("margin_right_mm", page.get("margin_right_mm", 31.75))))
            )
            ins.set_margin_invalid(False)
        finally:
            ins.set_loading(False)

    def _schedule_global_commit(self) -> None:
        if self._import_in_progress:
            return
        self._set_settings_message("设置已修改，正在等待更新…", tone="info")
        self._invalidate_edit()
        self._global_commit_timer.start()

    def _on_scope_changed(self) -> None:
        self._commit_global_settings(show_error=True)
        self._refresh_score_display()

    def _commit_global_settings(self, *, show_error: bool) -> bool:
        self._global_commit_timer.stop()
        ins = self.inspector_panel
        try:
            margins = {
                "margin_top_mm": float(ins.margin_top_edit.text().strip()),
                "margin_bottom_mm": float(ins.margin_bottom_edit.text().strip()),
                "margin_left_mm": float(ins.margin_left_edit.text().strip()),
                "margin_right_mm": float(ins.margin_right_edit.text().strip()),
            }
            if any(not (5 <= value <= 45) for value in margins.values()):
                raise ValueError("页边距应在 5 到 45 毫米之间。")
        except ValueError as exc:
            self._global_invalid = True
            ins.set_margin_invalid(True)
            msg = str(exc) if "毫米" in str(exc) else "页边距请填写 5 到 45 之间的数字（单位：毫米）。"
            self._set_settings_message(msg, tone="error")
            if show_error:
                self._show_error(msg)
            return False

        self._global_invalid = False
        ins.set_margin_invalid(False)
        metadata = self.raw_exam.setdefault("metadata", {})
        updates = {
            "exam_name": ins.exam_name_edit.text().strip(),
            "subject_name": ins.subject_name_edit.text().strip(),
            "meta_text": ins.exam_meta_edit.text().strip(),
            "layout_preset": DEFAULT_PRESET,
            "document_scope": "practice" if ins.document_scope_combo.currentText() == "局部训练" else "full_exam",
            "page_overrides": margins,
        }
        if all(metadata.get(key) == value for key, value in updates.items()):
            return True

        self._push_direct_history()
        header_changed = any(
            metadata.get(key, "") != updates[key] for key in ("exam_name", "subject_name", "meta_text")
        )
        self._queue_settings_feedback(header_changed=header_changed)
        metadata.update(updates)
        metadata.pop("page_spacing_scale", None)

        if self.selected_content_object is not None and self.selected_content_object.block_index is None:
            key = self.selected_content_object.key
            self._content_objects = metadata_content_objects(self.raw_exam)
            self.selected_content_object = next((obj for obj in self._content_objects if obj.key == key), None)
            if self.selected_content_object is not None:
                self._load_content_object(self.selected_content_object)

        self._sync_rich_document(preserve_scroll=True)
        self._set_settings_message("设置已应用，正在更新预览…", tone="info")
        self._schedule_canvas_preview()
        return True

    def apply_global_settings(self) -> None:
        if self._import_in_progress:
            return
        if not self._commit_global_settings(show_error=True):
            return
        if self._settings_feedback is None:
            self._queue_settings_feedback()
        self._preview_debounce_timer.stop()
        self.request_preview()

    def _queue_settings_feedback(self, *, header_changed: bool = False) -> None:
        previous = self._settings_feedback or {}
        header_changed = header_changed or bool(previous.get("header_changed"))
        self._settings_feedback = {
            "page": 0 if header_changed else self.preview_panel.current_page(),
            "header_changed": header_changed,
        }

    # ==================== 分值与待核对提醒 ====================

    def _refresh_score_display(self, summary: ScoreSummary | None = None) -> None:
        self.outline_panel.refresh_score(self.raw_exam, summary)
        self.outline_panel.set_review_items(review_items(self.raw_exam, self._preview_warnings))

    def _go_to_issue(self, index: int | None) -> None:
        if self._import_in_progress:
            return
        self.outline_panel.clear_search()
        self.outline_panel.populate(self.raw_exam, keep_selection=False)
        if self.outline_panel.select_block(index, emit=True):
            if not self._inspector_visible:
                self.toggle_inspector()
            self.inspector_panel.inspector_tabs.setCurrentIndex(0)
            if index is not None:
                self._jump_to_block(index)


__all__ = ["EditingActionsMixin"]
