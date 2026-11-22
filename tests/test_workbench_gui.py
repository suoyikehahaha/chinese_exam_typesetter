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
def workbench(tmp_path_factory):
    import os
    with patch.dict(os.environ, EXAM_TYPESETTER_STATE_DIR=str(tmp_path_factory.mktemp("workbench-state"))), \
         patch.object(CurrentDesktopApp, "request_preview"), \
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
            window._recovery.close()
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
    app._requested_revision = (app._session_generation, app._revision)
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


def test_old_page_target_is_discarded_in_both_document_scopes(app):
    app.raw_exam["metadata"].update(target_pages=8, page_spacing_scale=0.78)
    app._load_global_fields()
    widgets = list(app.global_group.body.winfo_children())
    while widgets:
        widget = widgets.pop()
        if "text" in widget.keys():
            assert widget.cget("text") not in ("分页方式", "目标页数")
        widgets.extend(widget.winfo_children())
    for scope in ("局部训练", "全卷"):
        app.document_scope_var.set(scope)
        app._scope_changed()
        assert app._commit_global_settings(show_error=False)
        assert app.raw_exam["metadata"]["target_pages"] == 0
        assert "page_spacing_scale" not in app.raw_exam["metadata"]


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


def test_new_preview_immediately_reports_the_installed_page_count(app, tmp_path):
    preview = app.document_editor
    pages = preview_pages(tmp_path, "new-pages")
    preview.set_preview_pages(pages)
    assert f"/ {len(pages)} 页" in preview.status_variable.get()


def test_page_counter_updates_while_editing_global_fields(app):
    preview = app.document_editor
    with patch.object(preview, 'focus_get', return_value=app.apply_page_settings_button):
        preview._update_status(2)
    assert preview.page_number_var.get() == '3'
    preview.page_number_var.set('7')
    with patch.object(preview, 'focus_get', return_value=preview.page_number_entry):
        preview._update_status(2)
    assert preview.page_number_var.get() == '7'

def test_queued_preview_after_body_edit_is_discarded_and_immediate_export_uses_current_text(app, tmp_path):
    from app.preview_service import PreviewResult
    import time

    select_question(app)
    # Request the older revision before editing the selected stem.
    old_token = (app._session_generation, app._revision)
    app._displayed_revision = app._requested_revision = old_token
    ticket = app._preview_ticket
    app.raw_exam["metadata"]["page_spacing_scale"] = 0.82
    app.stem_text.insert("end", "正文校订")
    app._schedule_text_commit()
    app._ui_messages.put(("preview", ticket, PreviewResult(app._preview_generation, (), "internal", {}, 8, 1, 0.82)))
    app._poll_ui_messages()
    assert "page_spacing_scale" not in app.raw_exam["metadata"]
    got = {}
    def export(raw, layout, output, basename, **kwargs):
        got.update(raw["metadata"])
        got["blocks"] = str(raw["blocks"])
        return tmp_path / "done.docx", None, "docx-only"
    with patch("app.preview_service.render_quick_preview", side_effect=AssertionError("unexpected pre-export rendering")), \
         patch("app.workbench_tasks.build_documents", side_effect=export):
        app.start_export(tmp_path, "done", True, False)
        deadline = time.monotonic() + 4
        while app._export_in_progress and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
    assert not app._export_in_progress and "正文校订" in got["blocks"]
    assert "page_spacing_scale" not in got
    assert app._displayed_revision != (app._session_generation, app._revision)


@pytest.mark.parametrize("outcome", ["cancel", "error", "success"])
def test_pending_import_protects_old_editor_and_only_current_result_wins(app, tmp_path, outcome):
    from concurrent.futures import Future
    from copy import deepcopy
    import time

    select_question(app)
    app.stem_text.insert("end", "旧稿校订")
    app._commit_current_text(schedule_preview=False)
    prior = deepcopy(app.raw_exam)
    replacement = deepcopy(prior)
    replacement["metadata"]["exam_name"] = "新试卷"
    future = Future()
    future.set_running_or_notify_cancel()
    with patch.object(app._export_executor, "submit", return_value=future), \
         patch("tkinter.messagebox.askyesnocancel", return_value=False), patch("tkinter.messagebox.showerror"):
        app._import_exam_path(tmp_path / "新试卷.docx")
        assert app._import_in_progress and str(app.stem_text.cget("state")) == "disabled"
        app.stem_text.insert("end", "误输入")
        app.undo_action()
        assert not app._commit_current_text(schedule_preview=False)
        if outcome == "cancel":
            app.cancel_import()
        if outcome == "error":
            future.set_exception(ValueError("格式损坏"))
        else:
            future.set_result(replacement)
        deadline = time.monotonic() + 2
        while app._import_in_progress and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        app.update()
    assert not app._import_in_progress and str(app.stem_text.cget("state")) == "normal"
    assert app.raw_exam["metadata"]["exam_name"] == (replacement if outcome == "success" else prior)["metadata"]["exam_name"]
    assert "误输入" not in str(app.raw_exam["blocks"])
    if outcome != "success":
        assert "旧稿校订" in str(app.raw_exam["blocks"]) and app.undo_stack


def test_downloaded_update_can_wait_and_install_must_check_dirty_draft(app, tmp_path):
    select_question(app)
    app.stem_text.insert("end", "未保存")
    path = tmp_path / "ChineseExamTypesetter_0.3.1.exe"
    app._update_messages.put(("downloaded", path))
    with patch("tkinter.messagebox.askyesno", return_value=False), \
         patch("desktop_app.schedule_portable_update") as install:
        app._poll_update_messages()
        install.assert_not_called()
    assert app._pending_update == path and not app._closing
    with patch("tkinter.messagebox.askyesno", return_value=True), \
         patch("tkinter.messagebox.askyesnocancel", return_value=None) as prompt, \
         patch("desktop_app.schedule_portable_update") as install:
        app.check_for_updates()
        prompt.assert_called_once()
        install.assert_not_called()
    assert not app._closing
    with patch("tkinter.messagebox.askyesno", return_value=True), \
         patch("tkinter.messagebox.askyesnocancel", return_value=True), \
         patch.object(app, "save_project", return_value=False) as save, \
         patch("desktop_app.schedule_portable_update") as install:
        app.check_for_updates()
        save.assert_called_once()
        install.assert_not_called()
    for choice in (True, False):
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("tkinter.messagebox.askyesnocancel", return_value=choice), \
             patch.object(app, "save_project", return_value=True) as save, \
             patch("desktop_app.schedule_portable_update", side_effect=OSError("isolated installer")) as install, \
             patch("tkinter.messagebox.showerror"):
            app.check_for_updates()
            install.assert_called_once_with(path)
            assert save.call_count == int(choice)
    app._pending_update = None


def test_draft_save_shortcuts_and_issue_navigation(app, tmp_path):
    select_question(app)
    app.score_var.set("")
    app._refresh_score_display()
    assert app.review_panel.rows
    row = next(i for i, (_, index) in enumerate(app.review_panel.rows) if index == app.selected_block_index)
    app.review_panel.toggle()
    app.review_panel.listbox.selection_set(row)
    app.review_panel._go()
    assert app.inspector_tabs.index(app.inspector_tabs.select()) == 0
    first, second = tmp_path/"first.json", tmp_path/"second.json"
    with patch("tkinter.filedialog.asksaveasfilename", side_effect=[str(first), str(second)]) as ask:
        assert app.save_project()
        app.stem_text.insert("end", "草稿保存")
        assert app.save_project()
        assert "草稿保存" in first.read_text(encoding="utf8")
        assert app.save_project(save_as=True)
        assert ask.call_count == 2 and app._project_path == second


def test_zoom_keeps_frames_and_never_hashes_pages(app, tmp_path):
    preview = app.document_editor
    preview.set_preview_pages(preview_pages(tmp_path, "zoom"))
    frames = tuple(preview.page_frames)
    with patch("app.read_only_preview.sha256", side_effect=AssertionError), \
         patch.object(preview, "set_preview_pages", side_effect=AssertionError):
        preview.change_zoom(-0.08)
        preview.fit_width()
    assert frames == tuple(preview.page_frames)

def test_recover_abandoned_draft_through_file_menu(app, tmp_path):
    import json
    from copy import deepcopy
    from app.drafts import save_draft

    directory = app._recovery.directory
    directory.mkdir(parents=True, exist_ok=True)
    raw = deepcopy(app.raw_exam)
    raw["metadata"]["exam_name"] = "中断后恢复的试卷"
    leftover = save_draft(raw, directory / "previous-window.json")
    with patch("tkinter.messagebox.askyesno", return_value=True), \
         patch("tkinter.messagebox.askyesnocancel", return_value=False):
        app.recover_draft()
    assert app.raw_exam["metadata"]["exam_name"] == "中断后恢复的试卷"
    assert app._recovered_path == leftover and leftover.is_file()
    assert draft_signature(app.raw_exam) != app._saved_signature
    saved = tmp_path / "restored.json"
    with patch("tkinter.filedialog.asksaveasfilename", return_value=str(saved)):
        assert app.save_project()
    assert json.loads(saved.read_text(encoding="utf8"))["metadata"]["exam_name"] == "中断后恢复的试卷"
    assert not leftover.exists()


def test_image_and_source_marks_survive_actual_undo_redo(app, tmp_path):
    from docx import Document
    from app.current_importer import import_exam
    from app.current_pipeline import build_documents
    from tests.test_document_reliability import illustrated_file, LAYOUT
    source = illustrated_file(tmp_path)
    app._apply_imported_exam(source, import_exam(source))
    select_question(app)
    for command, changed in ((None, True), (app.undo_action, False), (app.redo_action, True)):
        if command is None:
            app.stem_text.insert("end", "补入")
            assert app._commit_current_text(schedule_preview=False)
        else:
            command()
        file, _, _ = build_documents(app.raw_exam, LAYOUT, tmp_path, "redo")
        doc = Document(file)
        paragraph = next(p for p in doc.paragraphs if "关键句" in p.text)
        assert ("补入" in paragraph.text) == changed
        assert "".join(r.text for r in paragraph.runs if r.underline) == "关键句"
        assert len(doc.inline_shapes) == 1
