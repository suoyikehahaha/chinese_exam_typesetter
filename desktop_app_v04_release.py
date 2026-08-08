"""Release refinements for the confirmed v0.4 inspector design."""

from __future__ import annotations

from typing import Any

import desktop_app_current_v01 as current_v02
import app.inspector_model_v04 as inspector_model


_base_default_format = inspector_model.default_format_for


def _release_default_format(item: inspector_model.ContentObject) -> dict[str, Any]:
    """Align the inspector state with the confirmed template typography."""

    spec = _base_default_format(item)
    if item.role == "material_title":
        spec["bold"] = False
    if item.role == "subject_name":
        spec["bold"] = False
    return spec


inspector_model.default_format_for = _release_default_format

from desktop_app_v04 import (  # noqa: E402
    APP_TITLE,
    APP_VERSION,
    CurrentDesktopApp as InspectorDesktopApp,
)


class CurrentDesktopApp(InspectorDesktopApp):
    """Final v0.4 UI polish without changing the document pipeline."""

    def _on_tree_select(self, event: object | None) -> None:
        super()._on_tree_select(event)
        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        siblings = [widget for widget in self.content_group.body.pack_slaves() if widget is not self.identity_frame]
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


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "current_v02", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
