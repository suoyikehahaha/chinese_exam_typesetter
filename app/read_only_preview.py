"""Read-only preview with content-object positioning."""

from __future__ import annotations

from pathlib import Path
from hashlib import sha256
from io import BytesIO
import tkinter as tk
from tkinter import ttk
from typing import Any, Hashable, Mapping
from PIL import Image, ImageTk

from .editable_a4_canvas import EditableA4Canvas


class ReadOnlyPreview(EditableA4Canvas):
    """Extend the stable page-image preview with precise semantic navigation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._highlight_widget: tk.Frame | None = None
        self._highlight_after: str | None = None
        self._visible_after: str | None = None
        self._fit_after: str | None = None
        self._fit_enabled = True
        self._image_labels: list[tk.Label] = []
        self._visible_photos: dict[int, ImageTk.PhotoImage] = {}
        self._page_sizes: list[tuple[int, int]] = []
        self._page_fingerprints: list[str] = []
        self.changed_pages: set[int] = set()
        self._page_captions: list[tk.Label] = []
        super().__init__(*args, **kwargs)
        self.canvas.bind("<Configure>", self._fit_on_resize, add="+")
        scrollbar_set = self.canvas.cget("yscrollcommand")

        def scrolled(first, last):
            self.tk.call(scrollbar_set, first, last)
            self._queue_visible_pages()
            self._update_status(self.current_page())

        self.canvas.configure(yscrollcommand=scrolled)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(0, 0, 0, 8))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="页面预览", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(bar, textvariable=self.status_variable, foreground="#6A7C90").pack(side=tk.RIGHT)
        controls = ttk.Frame(self, padding=(0, 0, 0, 8))
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="上一页", command=self.previous_page).pack(side=tk.LEFT)
        self.page_number_var = tk.StringVar(value="1")
        entry = ttk.Entry(controls, textvariable=self.page_number_var, width=4, justify=tk.CENTER)
        self.page_number_entry = entry
        entry.pack(side=tk.LEFT, padx=5)
        entry.bind("<Return>", self._go_to_page)
        ttk.Button(controls, text="下一页", command=self.next_page).pack(side=tk.LEFT)
        ttk.Button(controls, text="适合宽度", command=self.fit_width).pack(side=tk.RIGHT)
        ttk.Button(controls, text="放大", command=lambda: self.change_zoom(0.08)).pack(side=tk.RIGHT, padx=5)
        ttk.Button(controls, text="缩小", command=lambda: self.change_zoom(-0.08)).pack(side=tk.RIGHT)
        self.update_message_var = tk.StringVar(master=self, value="预览页数为估算；整卷设置会影响所有页面。")
        ttk.Label(self, textvariable=self.update_message_var, foreground="#245D8C", wraplength=600).pack(
            fill=tk.X, pady=(0, 6))

    def _go_to_page(self, _event=None):
        try:
            number = int(self.page_number_var.get())
        except ValueError:
            self.page_number_var.set(str(self.current_page() + 1))
            return "break"
        self._scroll_page(max(0, min(len(self.page_frames) - 1, number - 1)))
        return "break"

    def _fit_on_resize(self, _event=None) -> None:
        if not self._fit_enabled:
            return
        if self._fit_after:
            self.after_cancel(self._fit_after)
        self._fit_after = self.after(180, self.fit_width)

    def fit_width(self) -> None:
        self._fit_after = None
        self._fit_enabled = True
        if not self._page_sizes:
            return
        zoom = max(0.25, min(1.35, (self.canvas.winfo_width() - 44) / self._page_sizes[0][0]))
        if abs(self.zoom - zoom) > 0.008:
            self.zoom = zoom
            self.set_preview_pages(self.preview_pages, locators=self.locators, actual_pages=self.actual_pages)

    def change_zoom(self, delta: float) -> None:
        self._fit_enabled = False
        self.zoom = max(0.25, min(1.35, self.zoom + delta))
        if self.preview_pages:
            self.set_preview_pages(self.preview_pages, locators=self.locators, actual_pages=self.actual_pages)

    def _queue_visible_pages(self) -> None:
        if self._visible_after is None:
            self._visible_after = self.after(25, self._load_visible_pages)

    def _load_visible_pages(self) -> None:
        self._visible_after = None
        if not self.page_frames:
            return
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        visible = [i for i, page in enumerate(self.page_frames)
                   if page.master.winfo_y() <= bottom and page.master.winfo_y() + page.master.winfo_height() >= top]
        if not visible:
            visible = [self.current_page()]
        keep = set(range(max(0, min(visible) - 1), min(len(self.page_frames), max(visible) + 2)))
        for index in list(self._visible_photos):
            if index not in keep:
                self._image_labels[index].configure(image="")
                del self._visible_photos[index]
        for index in sorted(keep):
            if index in self._visible_photos:
                continue
            try:
                with Image.open(self.preview_pages[index]) as source:
                    width = max(1, round(source.width * self.zoom))
                    height = max(1, round(source.height * self.zoom))
                    resized = source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(resized, master=self)
                self._visible_photos[index] = photo
                self._image_labels[index].configure(image=photo, text="")
            except OSError:
                self._image_labels[index].configure(text="页面已过期，请刷新预览")
        self.preview_image_refs = list(self._visible_photos.values())

    def set_preview_pages(
        self,
        pages: list[Path | str] | tuple[Path | str, ...],
        *,
        locators: Mapping[Hashable, tuple[int, float]] | None = None,
        actual_pages: int | None = None,
        raw_exam: dict[str, Any] | None = None,
        selected_key: Hashable | None = None,
    ) -> bool:
        """Load rendered pages and retain both block and object locators."""

        previous_view = self._capture_page_position()
        paths = [Path(page) for page in pages]
        sizes, fingerprints = [], []
        try:
            for path in paths:
                payload = path.read_bytes()
                fingerprints.append(sha256(payload).hexdigest())
                with Image.open(BytesIO(payload)) as source:
                    sizes.append(source.size)
        except OSError as exc:
            detail = exc.strerror or str(exc)
            self.update_message_var.set(f"预览图片“{path.name}”无法读取：{detail}。请点击“应用并查看”重新生成预览。")
            return False
        self.changed_pages = {index for index, value in enumerate(fingerprints)
                              if index >= len(self._page_fingerprints) or value != self._page_fingerprints[index]}
        self._page_fingerprints = fingerprints
        self._clear_position_marker()
        self._image_preview = True
        self._rendering = True
        self.preview_pages = paths
        self._page_sizes = sizes
        if self._fit_enabled and self._page_sizes and self.canvas.winfo_width() > 50:
            self.zoom = max(0.25, min(1.35, (self.canvas.winfo_width() - 44) / self._page_sizes[0][0]))
        self.locators = dict(locators or {})
        self.actual_pages = actual_pages or len(self.preview_pages)
        if raw_exam is not None:
            self.raw_exam = raw_exam
        for child in self.surface.winfo_children():
            child.destroy()
        self.page_frames.clear()
        self.block_pages.clear()
        self._image_labels.clear()
        self._page_captions.clear()
        self._visible_photos.clear()
        self.preview_image_refs.clear()
        for index, size in enumerate(self._page_sizes):
            holder = tk.Frame(self.surface, background="#EDF2F8")
            holder.pack(pady=(14, 8))
            caption = tk.Label(holder, text=f"第 {index + 1} 页", background="#EDF2F8", foreground="#6A7C90", font=("Microsoft YaHei UI", 9))
            caption.pack(anchor=tk.W, pady=(0, 5))
            self._page_captions.append(caption)
            page = tk.Frame(holder, background="white", width=round(size[0] * self.zoom) + 2,
                            height=round(size[1] * self.zoom) + 2, highlightbackground="#CDD7E4", highlightthickness=1)
            page.pack()
            page.pack_propagate(False)
            label = tk.Label(page, text="正在显示页面…", background="white", borderwidth=0)
            label.pack(fill=tk.BOTH, expand=True)
            for widget in (holder, caption, page, label):
                self._bind_wheel(widget)
            self.page_frames.append(page)
            self._image_labels.append(label)
        if self.page_frames:
            for key, location in self.locators.items():
                page_index = max(0, min(int(location[0]), len(self.page_frames) - 1))
                self.block_pages[key] = page_index
        self.surface.configure(background="#EDF2F8")
        self._rendering = False
        self._last_surface_width = 0
        self._last_scrollregion = None
        self._queue_layout_sync()
        if previous_view is not None:
            self._restore_page_position(previous_view)
        self._queue_visible_pages()
        return True

    def _scroll_to_y(self, y: float) -> None:
        """Tk expects a fraction of the full scroll region, not the free range."""
        self.update_idletasks()
        bbox = self.canvas.bbox("all")
        if not bbox:
            return
        self._update_scrollregion()
        extent = max(1.0, float(bbox[3] - bbox[1]))
        viewport = max(1, self.canvas.winfo_height())
        fraction = max(0.0, min(max(0.0, 1.0 - viewport / extent), (y - bbox[1]) / extent))
        self.canvas.yview_moveto(fraction)

    def _capture_page_position(self):
        if not self.page_frames:
            return None
        index = self.current_page()
        holder = self.page_frames[index].master
        relative = (self.canvas.canvasy(0) - holder.winfo_y()) / max(1, holder.winfo_height())
        return index, relative

    def _restore_page_position(self, position) -> None:
        if self._scroll_after:
            self.after_cancel(self._scroll_after)
        self._scroll_after = self.after(30, lambda: self._apply_page_position(position))

    def _apply_page_position(self, position) -> None:
        self._scroll_after = None
        if not self.page_frames:
            return
        index = max(0, min(position[0], len(self.page_frames) - 1))
        self.update_idletasks()
        holder = self.page_frames[index].master
        self._scroll_to_y(holder.winfo_y() + position[1] * max(1, holder.winfo_height()))

    def show_page_update(self, page_index: int, message: str) -> None:
        if not self.page_frames:
            return
        if self._scroll_after:
            self.after_cancel(self._scroll_after)
            self._scroll_after = None
        page_index = max(0, min(page_index, len(self.page_frames) - 1))
        self._scroll_page(page_index)
        for index, page in enumerate(self.page_frames):
            active = index == page_index
            page.configure(highlightbackground="#2472B8" if active else "#CDD7E4",
                           highlightthickness=2 if active else 1)
            label = f"第 {index + 1} 页" + (" · 正在核对" if active else "")
            self._page_captions[index].configure(text=label)
        self.update_message_var.set(message)

    def _update_status(self, page_index: int) -> None:
        self.status_variable.set(f"{page_index + 1} / {max(1, len(self.page_frames))} 页 · {round(self.zoom * 100)}%")
        if hasattr(self, "page_number_var") and self.focus_get() is not self.page_number_entry:
            self.page_number_var.set(str(page_index + 1))

    def current_page(self) -> int:
        if not self.page_frames:
            return 0
        middle = self.canvas.canvasy(0) + self.canvas.winfo_height() / 2
        for index, page in enumerate(self.page_frames):
            if page.master.winfo_y() + page.master.winfo_height() >= middle:
                return index
        return len(self.page_frames) - 1

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


__all__ = ["ReadOnlyPreview"]
