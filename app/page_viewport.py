"""Continuous document pages with shared scrolling and deferred geometry updates."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Any, Hashable
from PIL import ImageTk


class PageViewport(ttk.Frame):
    def __init__(self, parent, *, status_variable):
        super().__init__(parent)
        self.status_variable = status_variable
        self.zoom = 0.86
        self.raw_exam: dict[str, Any] = {}
        self.locators = {}
        self.actual_pages = 1
        self.page_frames: list[tk.Frame] = []
        self.block_pages: dict[Hashable, int] = {}
        self.selected_key = None
        self._rendering = False
        self._image_preview = False
        self.preview_pages: list[Path] = []
        self.preview_image_refs: list[ImageTk.PhotoImage] = []
        self._layout_after = None
        self._scroll_after = None
        self._last_surface_width = 0
        self._last_scrollregion = None

    def _build_surface(self) -> None:
        shell = ttk.Frame(self, relief=tk.SUNKEN, borderwidth=1)
        shell.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(shell, background="#D7DBE0", highlightthickness=0)
        vertical = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=self.canvas.yview)
        horizontal = ttk.Scrollbar(shell, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)
        vertical.grid(row=0, column=1, sticky=tk.NS)
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        self.surface = tk.Frame(self.canvas, background="#D7DBE0")
        # Keep the window item at a stable NW origin. Moving the item to the
        # canvas midpoint changes the scroll-region origin whenever the
        # viewport width changes, which produces a horizontal shake.
        self.surface_window = self.canvas.create_window(
            (0, 0), window=self.surface, anchor=tk.NW
        )
        self.surface.bind("<Configure>", self._queue_layout_sync)
        self.canvas.bind("<Configure>", self._queue_layout_sync)
        # Tk sends wheel events to the widget under the pointer. Bind every
        # preview shell so scrolling also works while the pointer is over a
        # page image or its caption.
        self._bind_wheel(shell)
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.surface)

    def previous_page(self) -> None:
        page = max(0, self.current_page() - 1)
        self._scroll_page(page)

    def next_page(self) -> None:
        page = min(max(0, len(self.page_frames) - 1), self.current_page() + 1)
        self._scroll_page(page)

    def _scroll_page(self, page_index: int) -> None:
        if not self.page_frames:
            return
        self.update_idletasks()
        holder = self.page_frames[page_index].master
        self._scroll_to_y(holder.winfo_y())
        self._update_status(page_index)

    def _on_mousewheel(self, event: tk.Event) -> str:
        """Scroll on Windows and X11, including when the pointer is over a page."""

        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            units = max(1, abs(delta) // 120)
            direction = -1 if delta > 0 else 1
        else:
            number = int(getattr(event, "num", 5) or 5)
            units = 1
            direction = -1 if number == 4 else 1
        self.canvas.yview_scroll(direction * units * 3, "units")
        self._update_status(self.current_page())
        return "break"

    def _bind_wheel(self, widget: tk.Misc) -> None:
        """Bind platform wheel events to a preview widget."""

        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, self._on_mousewheel, add="+")

    def _update_scrollregion(self, _event: object | None = None) -> None:
        bbox = self.canvas.bbox("all")
        if not bbox:
            return
        region = tuple(float(value) for value in bbox)
        if self._last_scrollregion == region:
            return
        self._last_scrollregion = region
        self.canvas.configure(scrollregion=bbox)

    def _center_surface(self, event: tk.Event | None = None) -> None:
        width = self.canvas.winfo_width() if event is None else int(event.width)
        if width <= 1:
            return
        surface_width = max(width, self.surface.winfo_reqwidth())
        if surface_width != self._last_surface_width:
            self.canvas.itemconfigure(self.surface_window, width=surface_width)
            self._last_surface_width = surface_width
        # The window item is anchored at NW, so its coordinate remains stable.
        if self.canvas.coords(self.surface_window) != [0.0, 0.0]:
            self.canvas.coords(self.surface_window, 0, 0)

    def _queue_layout_sync(self, _event: object | None = None) -> None:
        """Coalesce canvas and surface configure events into one layout pass."""

        if self._layout_after:
            try:
                self.after_cancel(self._layout_after)
            except tk.TclError:
                pass
        self._layout_after = self.after(16, self._run_layout_sync)

    def _run_layout_sync(self) -> None:
        self._layout_after = None
        if not self.winfo_exists():
            return
        self._center_surface()
        self._update_scrollregion()
