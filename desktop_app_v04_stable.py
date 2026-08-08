"""Stable v0.4 application entry point with guarded geometry callbacks."""

from __future__ import annotations

import tkinter as tk

import desktop_app_v04_release as release


APP_TITLE = release.APP_TITLE
APP_VERSION = release.APP_VERSION


class CurrentDesktopApp(release.CurrentDesktopApp):
    """Avoid nested idle processing and redundant configure feedback."""

    def __init__(self) -> None:
        self._detail_window_width = 0
        super().__init__()

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


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
