"""Final v0.4 application entry point with stable object selection."""

from __future__ import annotations

import desktop_app_v04_stable as stable


APP_TITLE = stable.APP_TITLE
APP_VERSION = stable.APP_VERSION


class CurrentDesktopApp(stable.CurrentDesktopApp):
    """Prevent repeated Treeview selection events for an unchanged object."""

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


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
