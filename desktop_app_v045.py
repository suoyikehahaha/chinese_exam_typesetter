"""Chinese exam typesetter v0.4.5 with reliable Explorer file dropping."""

from __future__ import annotations

from pathlib import Path
from tkinter import ttk
import tkinter as tk
from typing import Any

import desktop_app_current as runtime
import desktop_app_current_v01 as current_v01
from app.current_importer_v14 import import_exam
from app.github_update_page_v01 import GitHubUpdatePage, GITHUB_REPOSITORY
import app.inspector_model_v04 as inspector_model
from app.preview_service_v043 import PreviewService
from app.windows_drop_v01 import uninstall_file_drop as uninstall_legacy_drop
from app.windows_drop_v02 import uninstall_file_drop as uninstall_v02_drop
from app.windows_drop_v03 import install_file_drop, uninstall_file_drop


_base_default_format = inspector_model.default_format_for


def _release_default_format(item: inspector_model.ContentObject) -> dict[str, Any]:
    """Keep the confirmed v0.4 template defaults in the merged entry point."""

    spec = _base_default_format(item)
    if item.role in {"material_title", "subject_name"}:
        spec["bold"] = False
    return spec


inspector_model.default_format_for = _release_default_format

import desktop_app_v04 as v04  # noqa: E402


APP_TITLE = v04.APP_TITLE
APP_VERSION = "0.4.5"

runtime.import_exam = import_exam
current_v01.import_exam = import_exam
runtime.PreviewService = PreviewService
runtime.APP_VERSION = APP_VERSION
current_v01.APP_VERSION = APP_VERSION
v04.APP_VERSION = APP_VERSION
v04.legacy_base.VERSION = APP_VERSION
v04.current_runtime.APP_VERSION = APP_VERSION
v04.current_v02.APP_VERSION = APP_VERSION
v04.v03.APP_VERSION = APP_VERSION


class CurrentDesktopApp(v04.CurrentDesktopApp):
    """Merged v0.4 inspector, preview, drag-and-drop and update-page workbench."""

    def __init__(self) -> None:
        self._detail_window_width = 0
        runtime.PreviewService = PreviewService
        current_v01.import_exam = import_exam
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.drop_enabled = False
        self.drop_hint = ttk.Label(
            self,
            text="可将 DOCX 或 DOC 文件直接拖入窗口进行识别",
            anchor=tk.CENTER,
            padding=(8, 4),
            foreground="#245D8C",
        )
        self.drop_hint.pack(side=tk.BOTTOM, fill=tk.X)
        self.after_idle(self._activate_file_drop)

    def _restore_editor_sash(self) -> None:
        if self._ui_sash_initialized or not hasattr(self, "editor_pane"):
            return
        height = int(self.editor_pane.winfo_height())
        if height <= 1:
            self.after(80, self._restore_editor_sash)
            return
        self._ui_sash_initialized = True
        position = int(height * 0.35)
        settings = self._read_ui_settings()
        try:
            position = int(settings.get("left_splitter", position))
        except (TypeError, ValueError):
            pass
        try:
            self.editor_pane.sashpos(0, max(150, min(max(150, height - 220), position)))
        except tk.TclError:
            return

    def _resize_detail_window(self, event: tk.Event) -> None:
        width = max(1, int(event.width))
        if width == self._detail_window_width:
            return
        self._detail_window_width = width
        self.detail_canvas.itemconfigure(self.detail_window, width=width)

    def _on_tree_select(self, event: object | None) -> None:
        super()._on_tree_select(event)
        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        siblings = [
            widget
            for widget in self.content_group.body.pack_slaves()
            if widget is not self.identity_frame
        ]
        self.identity_frame.pack_forget()
        options: dict[str, Any] = {"fill": "x", "pady": (0, 6)}
        if siblings:
            options["before"] = siblings[0]
        self.identity_frame.pack(**options)

    def _restore_group(self, group: str) -> None:
        if group in {"global", "options"}:
            super()._restore_group(group)
            return
        item = self.selected_content_object
        if item is None:
            return
        self._push_direct_history()
        current = inspector_model.paragraph_format_for(self.raw_exam, item)
        template = _release_default_format(item)
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
            )
        for key in keys:
            current[key] = template[key]
        inspector_model.set_paragraph_format(self.raw_exam, item, current)
        self._load_content_object(item)
        self.status_var.set("已恢复当前分组的模板值。")
        self._schedule_canvas_preview()

    def _select_object(self, key: str, *, navigate: bool) -> None:
        item = next((value for value in self._content_objects if value.key == key), None)
        if item is None:
            return
        if self.selected_content_object is not None and self.selected_content_object.key != item.key:
            self._commit_current_text(schedule_preview=False)
        self.selected_content_object = item
        current = self.object_tree.selection()
        if current != (item.key,):
            self.object_tree.selection_set(item.key)
        self.object_tree.focus(item.key)
        self.object_tree.see(item.key)
        self._load_content_object(item)
        if navigate and hasattr(self, "document_editor"):
            if item.key in self.document_editor.locators:
                self.after_idle(lambda: self.document_editor.scroll_to(item.key, highlight=True))
            else:
                self._pending_preview_block = item.block_index

    def _build_menu(self) -> None:
        super()._build_menu()
        menu = self.nametowidget(self["menu"])
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="GitHub 更新页面", command=self.open_github_update_page)
        help_menu.add_command(
            label="打开 GitHub 仓库",
            command=lambda: __import__("webbrowser").open(GITHUB_REPOSITORY),
        )
        menu.add_cascade(label="帮助", menu=help_menu)

    def open_github_update_page(self) -> None:
        GitHubUpdatePage(self, APP_VERSION)

    def _activate_file_drop(self) -> None:
        uninstall_legacy_drop(self)
        uninstall_v02_drop(self)
        uninstall_file_drop(self)
        self.update_idletasks()
        self.drop_enabled = install_file_drop(self, self._handle_dropped_files)
        if not self.drop_enabled:
            self.drop_hint.configure(text="拖放注册失败，请使用“导入试题”按钮")

    def _handle_dropped_files(self, paths: tuple[Path, ...]) -> None:
        supported = [path for path in paths if path.suffix.lower() in {".docx", ".doc"}]
        if supported:
            self.status_var.set(f"正在识别拖入文件：{supported[0].name}")
        super()._handle_dropped_files(paths)

    def _on_close(self) -> None:
        uninstall_file_drop(self)
        super()._on_close()


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "import_exam", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
