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
