"""Exercise real Tk widgets and model routes with external dialogs isolated."""

import json
from pathlib import Path
import tkinter as tk
import sys
from unittest.mock import patch

import pytest

from desktop_app import CurrentDesktopApp
from app.workbench_state import draft_signature, suggested_export_path
from app.inspector_model import content_objects_for_block, paragraph_format_for, set_paragraph_format

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def workbench():
    with patch.object(CurrentDesktopApp, "request_preview"), \
         patch.object(CurrentDesktopApp, "_schedule_canvas_preview"), \
         patch.object(CurrentDesktopApp, "_activate_file_drop"):
        try:
            window = CurrentDesktopApp(check_updates=False)
        except tk.TclError as exc:
            if sys.platform != "win32" and "display" in str(exc).lower():
                pytest.skip(f"Tk display unavailable: {exc}")
            raise
        window.withdraw()
        window.update()
        try:
            yield window
        finally:
            window._closing = True
            window._cancel_editor_timers()
            def widgets(widget):
                yield widget
                for child in widget.winfo_children():
                    yield from widgets(child)
            owners = list(widgets(window))
            for timer in window.tk.call("after", "info"):
                command = window.tk.call("after", "info", timer)[0]
                owner = next((w for w in owners if command in (w._tclCommands or [])), window)
                owner.after_cancel(timer)
            window._preview_service.close()
            window._export_executor.shutdown(wait=True, cancel_futures=True)
            window.temp_context.cleanup()
            window.destroy()


@pytest.fixture
def app(workbench):
    raw = json.loads((ROOT / "samples/exam.json").read_text(encoding="utf-8"))
    workbench._apply_imported_exam(ROOT / "samples/exam.json", raw)
    workbench.current_exam_path = None
    workbench.update()
    return workbench


def select_question(app, position=0):
    indices = [i for i, block in enumerate(app.raw_exam["blocks"]) if block["type"] == "question"]
    index = indices[position]
    app.tree.selection_set(f"block-{index}")
    app._on_tree_select(None)
    app.update()
    return app.raw_exam["blocks"][index]["question"]


def test_reference_template_is_automatic_and_reset_uses_it(app):
    from app.layout_presets import DEFAULT_PRESET, PRESET_LABELS
    assert app.raw_exam["metadata"]["layout_preset"] == DEFAULT_PRESET
    assert app.template_label.cget("textvariable") == str(app.layout_preset_var)
    assert app.layout_preset_var.get() == PRESET_LABELS[DEFAULT_PRESET]
    app.raw_exam["metadata"]["layout_preset"] = "guangzhou"
    app._load_global_fields()
    assert app.raw_exam["metadata"]["layout_preset"] == DEFAULT_PRESET
    select_question(app)
    app._restore_group("paragraph")
    assert app.raw_exam["blocks"][app.selected_block_index]["question"]["format"]["special_indent"] == "无"


def test_text_switch_and_undo_roundtrip(app):
    question = select_question(app)
    original = question["stem"]
    app.stem_text.delete("1.0", tk.END)
    app.stem_text.insert("1.0", "修改后的题干")
    option = next(item for item in app._content_objects if item.target == "option")
    app._select_object(option.key, navigate=False)
    assert question["stem"] == "修改后的题干"
    assert app.stem_text.get("1.0", "end-1c") == option.text
    app.undo_action()
    assert app.raw_exam["blocks"][app.selected_block_index]["question"]["stem"] == original
    app.redo_action()
    assert app.raw_exam["blocks"][app.selected_block_index]["question"]["stem"] == "修改后的题干"


def test_score_sixty_and_switching_questions(app):
    question = select_question(app)
    app.score_var.set("60")
    select_question(app, 1)
    assert question["score"] == 60
    select_question(app)
    assert app.score_var.get() == "60"


def test_header_fields_do_not_restore_stale_text(app):
    app.tree.selection_set(app.root_iid)
    app._on_tree_select(None)
    app.inspector_tabs.select(2)
    app.update()
    app.exam_name_var.set("页面设置修改的卷名")
    assert app._flush_edits(show_error=False)
    app.inspector_tabs.select(0)
    app.update()
    assert app.stem_text.get("1.0", "end-1c") == "页面设置修改的卷名"
    assert app._flush_edits(show_error=False)
    assert app.raw_exam["metadata"]["exam_name"] == "页面设置修改的卷名"


def test_invalid_page_setting_remains_blocked_after_debounce(app):
    app.margin_top_var.set("nan")
    assert not app._commit_global_settings(show_error=False)
    assert not app._flush_edits(show_error=False)
    app.margin_top_var.set("25.4")
    assert app._flush_edits(show_error=False)


def test_invalid_format_cannot_export_previous_value(app):
    select_question(app)
    app.size_var.set("nan")
    assert not app._flush_edits(show_error=False)
    app.size_var.set("10.5")
    assert app._flush_edits(show_error=False)


def test_import_cancels_old_editor_buffer_and_save_keeps_source_path(app, tmp_path):
    select_question(app)
    app.stem_text.delete("1.0", tk.END)
    app.stem_text.insert("1.0", "上一份文件的文字")
    raw = json.loads((ROOT / "samples/exam.json").read_text(encoding="utf-8"))
    source = tmp_path / "新试卷.docx"
    app._apply_imported_exam(source, raw)
    app.update()
    assert "上一份文件的文字" not in json.dumps(app.raw_exam, ensure_ascii=False)
    draft = tmp_path / "继续编辑.json"
    with patch("desktop_app.filedialog.asksaveasfilename", return_value=str(draft)):
        assert app.save_project()
    assert app.current_exam_path == source
    assert draft.exists()


def test_one_option_format_does_not_change_siblings():
    raw = json.loads((ROOT / "samples/exam.json").read_text(encoding="utf-8"))
    index = next(i for i, b in enumerate(raw["blocks"]) if b["type"] == "question")
    options = [i for i in content_objects_for_block(raw["blocks"][index], index) if i.target == "option"]
    before = paragraph_format_for(raw, options[1])
    set_paragraph_format(raw, options[0], {"font": "黑体", "size_pt": 18})
    assert paragraph_format_for(raw, options[1]) == before


def test_export_name_and_transient_pagination(tmp_path):
    source = tmp_path / "题目.docx"
    source.touch()
    first = suggested_export_path(source, tmp_path, source.stem)
    first.touch()
    assert suggested_export_path(source, tmp_path, source.stem) != first
    raw = {"metadata": {}, "blocks": []}
    before = draft_signature(raw)
    raw["metadata"]["page_spacing_scale"] = 1.06
    assert draft_signature(raw) == before


def test_preview_only_allocates_visible_page_images(app, tmp_path):
    from PIL import Image
    page = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), "white").save(page)
    preview = app.document_editor
    preview.set_preview_pages([page] * 12)
    app.update_idletasks()
    preview._load_visible_pages()
    assert len(preview.page_frames) == 12
    assert 1 <= len(preview.preview_image_refs) <= 4


def preview_pages(tmp_path, name, changed=False):
    from PIL import Image, ImageDraw
    folder = tmp_path / name
    folder.mkdir()
    pages = []
    for index in range(3):
        path = folder / f'page-{index}.png'
        image = Image.new('RGB', (794, 1123), 'white')
        if changed and index == 2:
            ImageDraw.Draw(image).rectangle((100, 100, 600, 130), fill='black')
        image.save(path)
        pages.append(path)
    return pages


def finish_settings_preview(app, pages):
    from copy import deepcopy
    from app.preview_service import PreviewResult
    app._preview_generation += 1
    app._preview_settings_feedback = deepcopy(app._settings_feedback)
    app._finish_preview(PreviewResult(app._preview_generation, tuple(pages), 'internal', {}, 8, len(pages), 1))


def test_global_panel_is_compact_and_scope_is_clear(app):
    select_question(app)
    app.inspector_tabs.select(2)
    app.update()
    assert app.inspector_tabs.tab(2, 'text') == '整卷'
    assert app.selection_title_var.get() == '整卷设置 · 影响所有页面'
    app.header_settings_group.set_expanded(False)
    assert not app.header_settings_group.body.winfo_manager()
    assert app.global_group.winfo_reqheight() < 360
    assert app.apply_page_settings_button.cget('text') == '应用并查看'
    app.inspector_tabs.select(1)
    app.update()
    assert '影响所有页面' not in app.selection_title_var.get()


def test_natural_pagination_hides_page_count_and_retains_model(app):
    app.pagination_mode_var.set('自然分页')
    app._pagination_mode_changed()
    assert app.target_pages_var.get() == '0'
    assert not app.target_pages_entry.winfo_manager()
    assert app._commit_global_settings(show_error=False)
    assert app.raw_exam['metadata']['target_pages'] == 0
    app.pagination_mode_var.set('目标页数')
    app._pagination_mode_changed()
    assert app.target_pages_entry.winfo_manager() == 'pack'
    assert app.target_pages_var.get() == '8'


def test_applying_margins_returns_to_viewed_page_and_reports_visible_change(app, tmp_path):
    preview = app.document_editor
    preview.set_preview_pages(preview_pages(tmp_path, 'before'))
    with patch.object(preview, 'current_page', return_value=2):
        app.margin_left_var.set('28')
        assert app._commit_global_settings(show_error=False)
    assert app._settings_feedback['page'] == 2
    with patch.object(preview, '_scroll_page') as scroll:
        finish_settings_preview(app, preview_pages(tmp_path, 'after', changed=True))
        scroll.assert_called_with(2)
    assert '第 3 页画面已更新' in app.page_settings_status_var.get()
    assert preview.update_message_var.get() == app.page_settings_status_var.get()


def test_unchanged_preview_is_reported_honestly(app, tmp_path):
    preview = app.document_editor
    pages = preview_pages(tmp_path, 'same')
    preview.set_preview_pages(pages)
    with patch.object(preview, 'current_page', return_value=1):
        app._queue_settings_feedback()
    with patch.object(preview, '_scroll_page'):
        finish_settings_preview(app, pages)
    assert '第 2 页画面无变化' in app.page_settings_status_var.get()


def test_missing_preview_keeps_previous_pages_and_settings_for_retry(app, tmp_path):
    preview = app.document_editor
    pages = preview_pages(tmp_path, 'retained')
    finish_settings_preview(app, pages)
    fingerprints = list(preview._page_fingerprints)
    app._queue_settings_feedback()
    with patch('desktop_app.messagebox.showerror') as error:
        finish_settings_preview(app, [tmp_path / 'expired.png'])
    assert preview.preview_pages == pages
    assert app.preview_pages == pages
    assert preview._page_fingerprints == fingerprints
    assert app._settings_feedback is not None
    assert '更新失败' in app.page_settings_status_var.get()
    assert '重试' in app.page_settings_status_var.get()
    assert 'expired.png' in error.call_args.args[1]
    assert '重新生成' in error.call_args.args[1]


def test_header_edit_goes_to_first_page(app, tmp_path):
    preview = app.document_editor
    pages = preview_pages(tmp_path, 'header')
    preview.set_preview_pages(pages)
    with patch.object(preview, 'current_page', return_value=2):
        app.exam_name_var.set('修改后卷名')
        assert app._commit_global_settings(show_error=False)
    with patch.object(preview, '_scroll_page') as scroll:
        finish_settings_preview(app, pages)
        scroll.assert_called_with(0)
    assert '已定位卷首' in app.page_settings_status_var.get()


def test_page_navigation_uses_entire_canvas_extent(app):
    preview = app.document_editor
    with patch.object(preview, 'update_idletasks'), patch.object(preview, '_update_scrollregion'), \
         patch.object(preview.canvas, 'bbox', return_value=(0, 0, 800, 4000)), \
         patch.object(preview.canvas, 'winfo_height', return_value=800), \
         patch.object(preview.canvas, 'yview_moveto') as move:
        preview._scroll_to_y(2000)
        move.assert_called_with(0.5)


def test_page_anchor_survives_different_page_count(app, tmp_path):
    preview = app.document_editor
    preview.set_preview_pages(preview_pages(tmp_path, 'anchor'))
    holder = preview.page_frames[1].master
    with patch.object(preview, 'current_page', return_value=1), \
         patch.object(holder, 'winfo_y', return_value=1100), \
         patch.object(holder, 'winfo_height', return_value=1100), \
         patch.object(preview.canvas, 'canvasy', return_value=1375):
        anchor = preview._capture_page_position()
    assert anchor == (1, 0.25)
    with patch.object(preview, 'update_idletasks'), patch.object(holder, 'winfo_y', return_value=1500), \
         patch.object(holder, 'winfo_height', return_value=1500), patch.object(preview, '_scroll_to_y') as scroll:
        preview._apply_page_position(anchor)
        scroll.assert_called_with(1875)


def test_new_global_edit_discards_queued_old_preview(app):
    from types import SimpleNamespace
    ticket = app._preview_ticket
    app.busy = True
    app._ui_messages.put(('preview', ticket, SimpleNamespace(generation=app._preview_generation)))
    app.margin_top_var.set('27')
    with patch.object(app, '_finish_preview') as finish:
        app._poll_ui_messages()
        finish.assert_not_called()
    assert '等待更新' in app.page_settings_status_var.get()


def test_page_counter_updates_while_editing_global_fields(app):
    preview = app.document_editor
    with patch.object(preview, 'focus_get', return_value=app.target_pages_entry):
        preview._update_status(2)
    assert preview.page_number_var.get() == '3'
    preview.page_number_var.set('7')
    with patch.object(preview, 'focus_get', return_value=preview.page_number_entry):
        preview._update_status(2)
    assert preview.page_number_var.get() == '7'
