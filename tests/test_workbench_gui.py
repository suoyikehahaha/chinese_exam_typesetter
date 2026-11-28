"""真实 PySide6 控件与完整工作台业务闭环回归测试（隔离所有外部弹窗）。"""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app.inspector_model import content_objects_for_block, paragraph_format_for, set_paragraph_format
from app.layout_presets import DEFAULT_PRESET, PRESET_LABELS
from app.preview_service import PreviewResult
from app.ui.main_window import CurrentDesktopApp, create_application
from app.workbench_state import draft_signature, suggested_export_path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qt_app():
    return create_application([])


@pytest.fixture(scope="module")
def workbench(qt_app, tmp_path_factory):
    state_dir = tmp_path_factory.mktemp("workbench-state")
    with (
        patch.dict(os.environ, EXAM_TYPESETTER_STATE_DIR=str(state_dir)),
        patch.object(CurrentDesktopApp, "request_preview"),
        patch.object(CurrentDesktopApp, "_schedule_canvas_preview"),
    ):
        window = CurrentDesktopApp(check_updates=False)
        window.resize(1400, 860)
        qt_app.processEvents()
        try:
            yield window
        finally:
            window._shutdown_services()
            window.close()
            qt_app.processEvents()


@pytest.fixture
def app(workbench, qt_app):
    raw = json.loads((ROOT / "samples/exam.json").read_text(encoding="utf-8"))
    workbench._apply_imported_exam(ROOT / "samples/exam.json", raw)
    workbench.current_exam_path = None
    qt_app.processEvents()
    return workbench


def select_question(app: CurrentDesktopApp, position: int = 0) -> dict:
    indices = [i for i, block in enumerate(app.raw_exam["blocks"]) if block["type"] == "question"]
    index = indices[position]
    app.outline_panel.select_block(index, emit=True)
    QApplication.processEvents()
    return app.raw_exam["blocks"][index]["question"]


def edit_current_rich_text(app: CurrentDesktopApp, new_text: str, *, flush: bool = False) -> None:
    """在仿真 A4 富文本画布中直接替换当前选中对象的正文文字。"""

    item = app.selected_content_object
    assert item is not None
    rich = app.preview_panel.rich_editor
    assert rich.select_object_range(item.key, 0, len(item.text))
    cursor = rich.paper_edit.textCursor()
    cursor.insertText(new_text)
    if flush:
        rich.flush_pending_text()
    QApplication.processEvents()


def preview_pages(tmp_path: Path, name: str, *, changed: bool = False) -> list[Path]:
    folder = tmp_path / name
    folder.mkdir()
    pages: list[Path] = []
    for index in range(3):
        path = folder / f"page-{index}.png"
        image = Image.new("RGB", (794, 1123), "white")
        if changed and index == 2:
            ImageDraw.Draw(image).rectangle((100, 100, 600, 130), fill="black")
        image.save(path)
        pages.append(path)
    return pages


def finish_settings_preview(app: CurrentDesktopApp, pages: list[Path]) -> None:
    app._requested_revision = (app._session_generation, app._revision)
    app._preview_generation += 1
    app._preview_settings_feedback = deepcopy(app._settings_feedback)
    app._finish_preview(
        PreviewResult(app._preview_generation, tuple(pages), "internal", {}, 8, len(pages), 1.0)
    )


def test_hierarchical_outline_and_collapsible_inspector(app):
    # 大纲树应按“整份试卷 -> 大题 -> 模块 -> 题目/材料”分层展示
    root_item = app.outline_panel.tree.topLevelItem(0)
    assert root_item is not None
    assert root_item.childCount() >= 2
    first_section = root_item.child(0)
    assert first_section.childCount() >= 1

    # 启动默认收起右栏以呈现超大沉浸 A4 纸面，支持一键展开与再收起
    if app._inspector_visible:
        app.toggle_inspector()
    assert not app._inspector_visible
    assert app.inspector_panel.isHidden()
    app.toggle_inspector()
    assert app._inspector_visible
    assert not app.inspector_panel.isHidden()
    app.toggle_inspector()
    assert not app._inspector_visible
    assert app.inspector_panel.isHidden()


def test_review_drawer_hidden_when_clean_and_shown_when_missing_score(app):
    # 无待核对事项时抽屉不占空间
    assert not app.outline_panel.review_drawer.rows
    assert app.outline_panel.review_drawer.isHidden()

    # 清空一题分值后抽屉自动出现并支持跳转（自动展开右栏并切到“排版”页）
    if app._inspector_visible:
        app.toggle_inspector()
    select_question(app)
    app.inspector_panel.score_entry.setText("")
    app._refresh_score_display()
    assert app.outline_panel.review_drawer.rows
    assert not app.outline_panel.review_drawer.isHidden()
    row = next(
        i for i, (_, index) in enumerate(app.outline_panel.review_drawer.rows)
        if index == app.selected_block_index
    )
    app.outline_panel.review_drawer.toggle()
    app.outline_panel.review_drawer.activate_row(row)
    assert app._inspector_visible
    assert app.inspector_panel.inspector_tabs.currentIndex() == 0


def test_reference_template_is_automatic_and_reset_uses_it(app):
    assert app.raw_exam["metadata"]["layout_preset"] == DEFAULT_PRESET
    assert app.inspector_panel.template_label.text() == PRESET_LABELS[DEFAULT_PRESET]
    app.raw_exam["metadata"]["layout_preset"] = "guangzhou"
    app._load_global_fields()
    assert app.raw_exam["metadata"]["layout_preset"] == DEFAULT_PRESET
    select_question(app)
    app._restore_group("paragraph")
    assert app.raw_exam["blocks"][app.selected_block_index]["question"]["format"]["special_indent"] == "无"


def test_text_switch_and_undo_roundtrip(app):
    question = select_question(app)
    original = question["stem"]
    edit_current_rich_text(app, "修改后的题干", flush=False)
    option = next(item for item in app._content_objects if item.target == "option")
    app._select_object(option.key, navigate=False)
    assert question["stem"] == "修改后的题干"
    app.undo_action()
    assert app.raw_exam["blocks"][app.selected_block_index]["question"]["stem"] == original
    app.redo_action()
    assert app.raw_exam["blocks"][app.selected_block_index]["question"]["stem"] == "修改后的题干"


def test_score_sixty_and_switching_questions(app):
    question = select_question(app)
    app.inspector_panel.score_entry.setText("60")
    select_question(app, 1)
    assert question["score"] == 60
    select_question(app)
    assert app.inspector_panel.score_entry.text() == "60"


def test_header_fields_do_not_restore_stale_text(app):
    app.outline_panel.select_block(None, emit=True)
    app.inspector_panel.inspector_tabs.setCurrentIndex(1)
    QApplication.processEvents()
    app.inspector_panel.exam_name_edit.setText("页面设置修改的卷名")
    assert app._flush_edits(show_error=False)
    app.inspector_panel.inspector_tabs.setCurrentIndex(0)
    QApplication.processEvents()
    assert "页面设置修改的卷名" in app.preview_panel.rich_editor.paper_edit.toPlainText()
    assert app._flush_edits(show_error=False)
    assert app.raw_exam["metadata"]["exam_name"] == "页面设置修改的卷名"


def test_invalid_page_setting_remains_blocked_after_debounce(app):
    app.inspector_panel.margin_top_edit.setText("nan")
    assert not app._commit_global_settings(show_error=False)
    assert not app._flush_edits(show_error=False)
    app.inspector_panel.margin_top_edit.setText("25.4")
    assert app._flush_edits(show_error=False)


def test_invalid_format_cannot_export_previous_value(app):
    select_question(app)
    app.inspector_panel.left_indent_edit.setText("nan")
    assert not app._flush_edits(show_error=False)
    app.inspector_panel.left_indent_edit.setText("0")
    assert app._flush_edits(show_error=False)


def test_import_cancels_old_editor_buffer_and_save_keeps_source_path(app, tmp_path):
    select_question(app)
    edit_current_rich_text(app, "上一份文件的文字", flush=False)
    raw = json.loads((ROOT / "samples/exam.json").read_text(encoding="utf-8"))
    source = tmp_path / "新试卷.docx"
    app._apply_imported_exam(source, raw)
    QApplication.processEvents()
    assert "上一份文件的文字" not in json.dumps(app.raw_exam, ensure_ascii=False)
    draft = tmp_path / "继续编辑.json"
    with patch("app.ui.session_actions.QFileDialog.getSaveFileName", return_value=(str(draft), "")):
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
    page = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), "white").save(page)
    preview = app.preview_panel
    preview.set_preview_pages([page] * 12)
    QApplication.processEvents()
    preview._load_visible_pages()
    assert len(preview.page_frames) == 12
    assert 1 <= len(preview.preview_image_refs) <= 4


def test_global_panel_is_compact_and_scope_is_clear(app):
    select_question(app)
    ins = app.inspector_panel
    assert ins.inspector_tabs.count() == 2
    assert ins.inspector_tabs.tabText(0) == "排版"
    assert ins.inspector_tabs.tabText(1) == "整卷"
    ins.inspector_tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert ins.selection_title() == "整卷设置 · 影响所有页面"
    ins.header_settings_group.set_expanded(False)
    assert not ins.header_settings_group.expanded
    assert ins.header_settings_group.body.isHidden()
    assert ins.apply_page_settings_button.text() == "应用并查看"
    ins.inspector_tabs.setCurrentIndex(0)
    QApplication.processEvents()
    assert "影响所有页面" not in ins.selection_title()


def test_old_page_target_is_discarded_in_both_document_scopes(app):
    app.raw_exam["metadata"].update(target_pages=8, page_spacing_scale=0.78)
    app._load_global_fields()
    for scope in ("局部训练", "全卷"):
        app.inspector_panel.document_scope_combo.setCurrentText(scope)
        app._on_scope_changed()
        assert app._commit_global_settings(show_error=False)
        assert app.raw_exam["metadata"]["target_pages"] == 0
        assert "page_spacing_scale" not in app.raw_exam["metadata"]


def test_applying_margins_returns_to_viewed_page_and_reports_visible_change(app, tmp_path):
    preview = app.preview_panel
    preview.set_preview_pages(preview_pages(tmp_path, "before"))
    with patch.object(preview, "current_page", return_value=2):
        app.inspector_panel.margin_left_edit.setText("28")
        assert app._commit_global_settings(show_error=False)
    assert app._settings_feedback["page"] == 2
    with patch.object(preview, "_scroll_page") as scroll:
        finish_settings_preview(app, preview_pages(tmp_path, "after", changed=True))
        scroll.assert_called_with(2)
    assert "第 3 页画面已更新" in app.inspector_panel.page_settings_status()
    assert preview.update_message() == app.inspector_panel.page_settings_status()


def test_unchanged_preview_is_reported_honestly(app, tmp_path):
    preview = app.preview_panel
    pages = preview_pages(tmp_path, "same")
    preview.set_preview_pages(pages)
    with patch.object(preview, "current_page", return_value=1):
        app._queue_settings_feedback()
    with patch.object(preview, "_scroll_page"):
        finish_settings_preview(app, pages)
    assert "第 2 页画面无变化" in app.inspector_panel.page_settings_status()


def test_missing_preview_keeps_previous_pages_and_settings_for_retry(app, tmp_path):
    preview = app.preview_panel
    pages = preview_pages(tmp_path, "retained")
    finish_settings_preview(app, pages)
    fingerprints = list(preview._page_fingerprints)
    app._queue_settings_feedback()
    with patch.object(app, "_show_error") as error:
        finish_settings_preview(app, [tmp_path / "expired.png"])
    assert preview.preview_pages == pages
    assert app.preview_pages == pages
    assert preview._page_fingerprints == fingerprints
    assert app._settings_feedback is not None
    assert "更新失败" in app.inspector_panel.page_settings_status()
    assert "重试" in app.inspector_panel.page_settings_status()
    assert "expired.png" in error.call_args.args[0]
    assert "重新生成" in error.call_args.args[0]


def test_header_edit_goes_to_first_page(app, tmp_path):
    preview = app.preview_panel
    pages = preview_pages(tmp_path, "header")
    preview.set_preview_pages(pages)
    with patch.object(preview, "current_page", return_value=2):
        app.inspector_panel.exam_name_edit.setText("修改后卷名")
        assert app._commit_global_settings(show_error=False)
    with patch.object(preview, "_scroll_page") as scroll:
        finish_settings_preview(app, pages)
        scroll.assert_called_with(0)
    assert "已定位卷首" in app.inspector_panel.page_settings_status()


def test_new_global_edit_discards_queued_old_preview(app):
    ticket = app._preview_ticket
    app._busy = True
    app.inspector_panel.margin_top_edit.setText("27")
    with patch.object(app, "_finish_preview") as finish:
        app._on_preview_ready(ticket, SimpleNamespace(generation=app._preview_generation))
        finish.assert_not_called()
    assert "等待更新" in app.inspector_panel.page_settings_status()


def test_new_preview_immediately_reports_the_installed_page_count(app, tmp_path):
    preview = app.preview_panel
    pages = preview_pages(tmp_path, "new-pages")
    preview.set_preview_pages(pages)
    assert f"/ {len(pages)} 页" in preview.status_text()


def test_queued_preview_after_body_edit_is_discarded_and_immediate_export_uses_current_text(app, tmp_path):
    question = select_question(app)
    old_token = (app._session_generation, app._revision)
    app._displayed_revision = app._requested_revision = old_token
    ticket = app._preview_ticket
    app.raw_exam["metadata"]["page_spacing_scale"] = 0.82
    edit_current_rich_text(app, question["stem"] + "正文校订", flush=True)
    app._on_preview_ready(
        ticket, PreviewResult(app._preview_generation, (), "internal", {}, 8, 1, 0.82)
    )
    assert "page_spacing_scale" not in app.raw_exam["metadata"]
    got: dict = {}

    def fake_export(raw, layout, output, basename, **kwargs):
        got.update(raw["metadata"])
        got["blocks"] = str(raw["blocks"])
        return tmp_path / "done.docx", None, "docx-only"

    with (
        patch("app.preview_service.render_quick_preview", side_effect=AssertionError("unexpected pre-export rendering")),
        patch("app.ui.session_actions.build_documents", side_effect=fake_export),
    ):
        app.start_export(tmp_path, "done", True, False)
        deadline = time.monotonic() + 4
        while app._export_in_progress and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
    assert not app._export_in_progress
    assert "正文校订" in got["blocks"]
    assert "page_spacing_scale" not in got
    assert app._displayed_revision != (app._session_generation, app._revision)


@pytest.mark.parametrize("outcome", ["cancel", "error", "success"])
def test_pending_import_protects_old_editor_and_only_current_result_wins(app, tmp_path, outcome):
    question = select_question(app)
    edit_current_rich_text(app, question["stem"] + "旧稿校订", flush=True)
    prior = deepcopy(app.raw_exam)
    replacement = deepcopy(prior)
    replacement["metadata"]["exam_name"] = "新试卷"
    future: Future = Future()
    future.set_running_or_notify_cancel()
    with (
        patch.object(app._export_executor, "submit", return_value=future),
        patch.object(app, "_ask_yes_no_cancel", return_value=False),
        patch.object(app, "_show_error"),
    ):
        app._import_exam_path(tmp_path / "新试卷.docx")
        assert app._import_in_progress and not app.inspector_panel.pages_stack.isEnabled()
        assert not app.preview_panel.rich_editor.isEnabled()
        app.undo_action()
        if outcome == "cancel":
            app.cancel_import()
        if outcome == "error":
            future.set_exception(ValueError("格式损坏"))
        else:
            future.set_result(replacement)
        deadline = time.monotonic() + 2
        while app._import_in_progress and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()
    assert not app._import_in_progress and app.inspector_panel.pages_stack.isEnabled()
    expected = replacement if outcome == "success" else prior
    assert app.raw_exam["metadata"]["exam_name"] == expected["metadata"]["exam_name"]
    if outcome != "success":
        assert "旧稿校订" in str(app.raw_exam["blocks"]) and app.undo_stack


def test_downloaded_update_can_wait_and_install_must_check_dirty_draft(app, tmp_path):
    question = select_question(app)
    edit_current_rich_text(app, question["stem"] + "未保存", flush=True)
    path = tmp_path / "ChineseExamTypesetter_0.4.1.exe"
    with (
        patch.object(app, "_ask_yes_no", return_value=False),
        patch("app.ui.main_window.schedule_portable_update") as install,
    ):
        app._on_update_downloaded(path)
        install.assert_not_called()
    assert app._pending_update == path and not app._closing

    with (
        patch.object(app, "_ask_yes_no", return_value=True),
        patch.object(app, "_ask_yes_no_cancel", return_value=None) as prompt,
        patch("app.ui.main_window.schedule_portable_update") as install,
    ):
        app.check_for_updates()
        prompt.assert_called_once()
        install.assert_not_called()
    assert not app._closing

    with (
        patch.object(app, "_ask_yes_no", return_value=True),
        patch.object(app, "_ask_yes_no_cancel", return_value=True),
        patch.object(app, "save_project", return_value=False) as save,
        patch("app.ui.main_window.schedule_portable_update") as install,
    ):
        app.check_for_updates()
        save.assert_called_once()
        install.assert_not_called()

    for choice in (True, False):
        with (
            patch.object(app, "_ask_yes_no", return_value=True),
            patch.object(app, "_ask_yes_no_cancel", return_value=choice),
            patch.object(app, "save_project", return_value=True) as save,
            patch("app.ui.main_window.schedule_portable_update", side_effect=OSError("isolated installer")) as install,
            patch.object(app, "_show_error"),
        ):
            app.check_for_updates()
            install.assert_called_once_with(path)
            assert save.call_count == int(choice)
    app._pending_update = None


def test_draft_save_and_save_as(app, tmp_path):
    question = select_question(app)
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    with patch(
        "app.ui.session_actions.QFileDialog.getSaveFileName",
        side_effect=[(str(first), ""), (str(second), "")],
    ) as ask:
        assert app.save_project()
        edit_current_rich_text(app, question["stem"] + "草稿保存", flush=True)
        assert app.save_project()
        assert "草稿保存" in first.read_text(encoding="utf-8")
        assert app.save_project(save_as=True)
        assert ask.call_count == 2 and app._project_path == second


def test_zoom_keeps_frames_and_never_hashes_pages(app, tmp_path):
    preview = app.preview_panel
    preview.set_preview_pages(preview_pages(tmp_path, "zoom"))
    frames = tuple(preview.page_frames)
    with (
        patch("app.ui.preview_panel.sha256", side_effect=AssertionError),
        patch.object(preview, "set_preview_pages", side_effect=AssertionError),
    ):
        preview.change_zoom(-0.08)
        preview.fit_width()
    assert frames == tuple(preview.page_frames)


def test_recover_abandoned_draft_through_file_menu(app, tmp_path):
    from app.drafts import save_draft

    directory = app._recovery.directory
    directory.mkdir(parents=True, exist_ok=True)
    raw = deepcopy(app.raw_exam)
    raw["metadata"]["exam_name"] = "中断后恢复的试卷"
    leftover = save_draft(raw, directory / "previous-window.json")
    with (
        patch.object(app, "_ask_yes_no", return_value=True),
        patch.object(app, "_ask_yes_no_cancel", return_value=False),
    ):
        app.recover_draft()
    assert app.raw_exam["metadata"]["exam_name"] == "中断后恢复的试卷"
    assert app._recovered_path == leftover and leftover.is_file()
    assert draft_signature(app.raw_exam) != app._saved_signature
    saved = tmp_path / "restored.json"
    with patch("app.ui.session_actions.QFileDialog.getSaveFileName", return_value=(str(saved), "")):
        assert app.save_project()
    assert json.loads(saved.read_text(encoding="utf-8"))["metadata"]["exam_name"] == "中断后恢复的试卷"
    assert not leftover.exists()


def test_image_and_source_marks_survive_actual_undo_redo(app, tmp_path):
    from docx import Document
    from app.current_importer import import_exam
    from app.current_pipeline import build_documents
    from tests.test_document_reliability import LAYOUT, illustrated_file

    source = illustrated_file(tmp_path)
    app._apply_imported_exam(source, import_exam(source))
    question = select_question(app)
    for command, changed in ((None, True), (app.undo_action, False), (app.redo_action, True)):
        if command is None:
            edit_current_rich_text(app, question["stem"] + "补入", flush=True)
        else:
            command()
        file, _, _ = build_documents(app.raw_exam, LAYOUT, tmp_path, "redo")
        doc = Document(file)
        paragraph = next(p for p in doc.paragraphs if "关键句" in p.text)
        assert ("补入" in paragraph.text) == changed
        assert "".join(r.text for r in paragraph.runs if r.underline) == "关键句"
        assert len(doc.inline_shapes) == 1


def test_rich_editor_default_mode_and_view_switch(app):
    assert app.preview_panel.view_mode == "rich"
    assert app.preview_panel.view_stack.currentIndex() == 0
    app.preview_panel.set_view_mode("pages")
    assert app.preview_panel.view_mode == "pages"
    assert app.preview_panel.view_stack.currentIndex() == 1
    app.preview_panel.set_view_mode("rich")
    assert app.preview_panel.view_mode == "rich"


def test_rich_editor_inline_selection_formats_and_docx_export(app, tmp_path):
    from docx import Document
    from docx.oxml.ns import qn
    from app.current_pipeline import build_documents
    from app.inspector_model import inline_formats_for

    question = select_question(app)
    stem_item = app.selected_content_object
    assert stem_item is not None and len(question["stem"]) >= 8

    rich = app.preview_panel.rich_editor
    assert rich.select_object_range(stem_item.key, 0, 4, show_mini_bar=True)
    QApplication.processEvents()
    assert not rich.mini_bar.isHidden()

    # 连续叠加加粗 + 下划线 + 着重号（模拟 Word 浮动工具栏操作）
    rich.mini_bar.boldToggled.emit(True)
    rich.mini_bar.underlineToggled.emit(True)
    rich.mini_bar.emphasisToggled.emit(True)
    QApplication.processEvents()

    inlines = inline_formats_for(app.raw_exam, stem_item)
    assert len(inlines) == 1
    assert inlines[0]["start"] == 0 and inlines[0]["end"] == 4
    assert inlines[0]["bold"] is True
    assert inlines[0]["underline"] is True
    assert inlines[0]["emphasis"] == "dot"

    # 导出真实 DOCX 并验证底层 OOXML 字符样式无损写入
    target_text = question["stem"][:4]
    docx_path, _, _ = build_documents(app.raw_exam, app.layout_path, tmp_path, "rich_inline")
    doc = Document(docx_path)
    para = next(p for p in doc.paragraphs if question["stem"] in p.text)
    styled_runs = [r for r in para.runs if r.text and target_text in r.text or (r.bold and r.underline)]
    combined = "".join(r.text for r in styled_runs if r.bold and r.underline)
    assert target_text in combined
    em_nodes = [
        em.get(qn("w:val"))
        for r in styled_runs
        for em in r._r.findall(f".//{qn('w:em')}")
    ]
    assert "dot" in em_nodes

    # 一键清除选区局部格式
    rich.mini_bar.clearFormatRequested.emit()
    QApplication.processEvents()
    assert inline_formats_for(app.raw_exam, stem_item) == []


def test_rich_editor_multi_block_selection_and_single_undo(app):
    select_question(app)
    options = [obj for obj in app._content_objects if obj.target == "option"]
    assert len(options) >= 2
    before_fonts = [paragraph_format_for(app.raw_exam, opt)["font"] for opt in options[:2]]
    before_aligns = [paragraph_format_for(app.raw_exam, opt)["alignment"] for opt in options[:2]]

    rich = app.preview_panel.rich_editor
    assert rich.select_blocks_span(options[0].key, options[1].key)
    rich.inlineStyleRequested.emit({"font": "黑体"})
    assert paragraph_format_for(app.raw_exam, options[0])["font"] == "黑体"
    assert paragraph_format_for(app.raw_exam, options[1])["font"] == "黑体"

    rich.paragraphStyleRequested.emit({"alignment": "居中"})
    assert paragraph_format_for(app.raw_exam, options[0])["alignment"] == "居中"
    assert paragraph_format_for(app.raw_exam, options[1])["alignment"] == "居中"

    # 单步撤销还原对齐，再撤销还原字体
    app.undo_action()
    assert [paragraph_format_for(app.raw_exam, opt)["alignment"] for opt in options[:2]] == before_aligns
    app.undo_action()
    assert [paragraph_format_for(app.raw_exam, opt)["font"] for opt in options[:2]] == before_fonts


def test_rich_editor_direct_text_edit_syncs_model_and_inspector(app):
    question = select_question(app)
    stem_item = app.selected_content_object
    assert stem_item is not None
    rich = app.preview_panel.rich_editor
    assert rich.select_object_range(stem_item.key, 0, 0)
    cursor = rich.paper_edit.textCursor()
    cursor.insertText("【直编验证】")
    rich.flush_pending_text()
    QApplication.processEvents()

    assert question["stem"].startswith("【直编验证】")
    assert "【直编验证】" in rich.paper_edit.toPlainText()
