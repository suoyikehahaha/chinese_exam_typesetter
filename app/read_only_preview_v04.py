"""Read-only preview with content-object positioning for v0.4."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from typing import Any, Hashable, Mapping

from .editable_a4_canvas_v03 import EditableA4Canvas


class ReadOnlyPreviewV04(EditableA4Canvas):
    """Extend the stable page-image preview with precise semantic navigation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._highlight_widget: tk.Frame | None = None
        self._highlight_after: str | None = None
        super().__init__(*args, **kwargs)

    def set_preview_pages(
        self,
        pages: list[Path | str] | tuple[Path | str, ...],
        *,
        locators: Mapping[Hashable, tuple[int, float]] | None = None,
        actual_pages: int | None = None,
        raw_exam: dict[str, Any] | None = None,
        selected_key: Hashable | None = None,
    ) -> None:
        """Load rendered pages and retain both block and object locators."""

        parent_locators = dict(locators or {})
        super().set_preview_pages(
            pages,
            locators=parent_locators,
            actual_pages=actual_pages,
            raw_exam=raw_exam,
            selected_key=selected_key,
        )
        self.locators = parent_locators
        if self.page_frames:
            for key, location in parent_locators.items():
                page_index = max(0, min(int(location[0]), len(self.page_frames) - 1))
                self.block_pages[key] = page_index
        if selected_key is not None and selected_key in self.block_pages:
            self.after(40, lambda: self.scroll_to(selected_key, highlight=False))

    def scroll_to(self, key: Hashable, *, highlight: bool = True) -> None:
        """Center the selected object and briefly mark its page position."""

        locator = self.locators.get(key)
        if locator is None or not self.page_frames:
            return
        page_index = max(0, min(int(locator[0]), len(self.page_frames) - 1))
        vertical = max(0.0, min(1.0, float(locator[1])))
        page = self.page_frames[page_index]
        holder = page.master
        self.update_idletasks()
        page_height = max(1, page.winfo_height(), page.winfo_reqheight())
        caption_height = sum(max(0, child.winfo_height()) for child in holder.winfo_children() if child is not page)
        local_y = int(page_height * (0.075 + vertical * 0.84))
        surface_y = holder.winfo_y() + caption_height + local_y
        viewport = max(1, self.canvas.winfo_height())
        self._scroll_to_y(max(0, surface_y - viewport * 0.46))
        self.selected_key = key
        self._update_status(page_index)
        if highlight:
            self._show_position_marker(page, local_y)

    def _show_position_marker(self, page: tk.Frame, local_y: int) -> None:
        """Show a pale, borderless marker for roughly 1.5 seconds."""

        self._clear_position_marker()
        height = max(24, int(30 * self.zoom))
        marker = tk.Frame(
            page,
            background="#FFF1A8",
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        marker.place(x=2, y=max(2, local_y - height // 2), width=max(5, int(7 * self.zoom)), height=height)
        marker.lift()
        self._highlight_widget = marker
        self._highlight_after = self.after(1500, self._clear_position_marker)

    def _clear_position_marker(self) -> None:
        if self._highlight_after:
            try:
                self.after_cancel(self._highlight_after)
            except tk.TclError:
                pass
            self._highlight_after = None
        if self._highlight_widget is not None:
            try:
                self._highlight_widget.destroy()
            except tk.TclError:
                pass
            self._highlight_widget = None


__all__ = ["ReadOnlyPreviewV04"]
