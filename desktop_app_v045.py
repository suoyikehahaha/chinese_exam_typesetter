"""Chinese exam typesetter v0.4.5 with reliable Explorer file dropping."""

from __future__ import annotations

from pathlib import Path
from tkinter import ttk
import tkinter as tk

import desktop_app_current as runtime
import desktop_app_current_v01 as current_v01
import desktop_app_v041 as v041
from app.current_importer_v14 import import_exam
from app.github_update_page_v01 import GitHubUpdatePage, GITHUB_REPOSITORY
from app.preview_service_v043 import PreviewService
from app.windows_drop_v01 import uninstall_file_drop as uninstall_legacy_drop
from app.windows_drop_v02 import uninstall_file_drop as uninstall_v02_drop
from app.windows_drop_v03 import install_file_drop, uninstall_file_drop


APP_TITLE = v041.APP_TITLE
APP_VERSION = "0.4.5"

runtime.import_exam = import_exam
current_v01.import_exam = import_exam
runtime.PreviewService = PreviewService


class CurrentDesktopApp(v041.CurrentDesktopApp):
    def __init__(self) -> None:
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
