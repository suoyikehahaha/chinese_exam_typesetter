"""Read-only, Office-independent A4 preview surface."""

from __future__ import annotations

from math import ceil
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Hashable

from PIL import Image, ImageTk


DocumentKey = Hashable
FONT_TO_TK = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
    "SimSun": "SimSun",
    "SimHei": "SimHei",
    "KaiTi": "KaiTi",
    "FangSong": "FangSong",
}


def question_editor_lines(question: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Serialize one question and retain a mapping for round-trip edits."""

    lines = [str(question.get("stem", ""))]
    mapping: list[dict[str, Any]] = [{"target": "stem", "target_index": 0}]
    for index, value in enumerate(question.get("options", [])):
        lines.append(str(value))
        mapping.append({"target": "option", "target_index": index})
    for index, segments in enumerate(question.get("embedded_segments", [])):
        lines.append("".join(str(item.get("text", "")) for item in segments))
        mapping.append({"target": "embedded", "target_index": index})
    if question.get("segmentation_text"):
        lines.append(str(question["segmentation_text"]))
        mapping.append({"target": "segmentation", "target_index": 0})
    for index, value in enumerate(question.get("subquestions", [])):
        lines.append(str(value))
        mapping.append({"target": "subquestion", "target_index": index})
    for target in (
        "composition_material",
        "composition_prompt",
        "composition_requirements",
    ):
        for index, value in enumerate(question.get(target, [])):
            lines.append(str(value))
            mapping.append({"target": target, "target_index": index})
    return lines, mapping


def block_editor_text(block: dict[str, Any]) -> str:
    """Return the editable text shown for one structured block."""

    kind = str(block.get("type", ""))
    if kind in {"section_title", "instruction", "answer_section"}:
        return str(block.get("text", ""))
    if kind in {"subsection", "answer_subsection"}:
        return f"{block.get('name', '')}{block.get('meta', '')}"
    if kind == "question":
        return "\n".join(question_editor_lines(block.get("question", {}))[0])
    if kind in {"material", "poetry"}:
        values: list[str] = []
        if block.get("title"):
            values.append(str(block["title"]))
        if block.get("author"):
            values.append(str(block["author"]))
        values.extend(str(value) for value in block.get("paragraphs", []))
        if block.get("note"):
            values.append(str(block["note"]))
        if block.get("source"):
            values.append(str(block["source"]))
        return "\n".join(values)
    if kind in {"answer_question", "answer_text"}:
        values = [str(block["header"])] if block.get("header") else []
        values.extend(str(item.get("text", "")) for item in block.get("paragraphs", []))
        return "\n".join(values)
    if kind == "answer_table":
        return "[原答案表格，导出时保持原格式]"
    return ""


class EditableA4Canvas(ttk.Frame):
    """A continuous A4 surface made of read-only semantic preview blocks."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_select: Callable[[DocumentKey], None],
        on_change: Callable[[DocumentKey, str, str], None],
        on_inline_format: Callable[[DocumentKey, str, str, dict[str, Any]], None],
        on_undo: Callable[[], Any],
        on_redo: Callable[[], Any],
        status_variable: tk.StringVar,
    ) -> None:
        super().__init__(parent)
        self.on_select = on_select
        self.on_change = on_change
        self.on_inline_format = on_inline_format
        self.on_undo = on_undo
        self.on_redo = on_redo
        self.status_variable = status_variable
        self.zoom = 0.86
        self.raw_exam: dict[str, Any] = {}
        self.locators: dict[int, tuple[int, float]] = {}
        self.actual_pages = 1
        self.text_widgets: dict[DocumentKey, tk.Text] = {}
        self.page_frames: list[tk.Frame] = []
        self.block_pages: dict[DocumentKey, int] = {}
        self.pending_changes: dict[DocumentKey, str] = {}
        self.selected_key: DocumentKey | None = None
        self._rendering = False
        self._image_preview = False
        self.preview_pages: list[Path] = []
        self.preview_image_refs: list[ImageTk.PhotoImage] = []
        # Coalesce bursts of Tk geometry events while pages are populated.
        # Repeated geometry updates were making the preview surface visibly
        # jump during startup and after a live preview completed.
        self._layout_after: str | None = None
        self._scroll_after: str | None = None
        self._last_surface_width = 0
        self._last_scrollregion: tuple[float, float, float, float] | None = None
        self._pagination_signature: tuple[Any, ...] | None = None
        self._selection_syncing = False

        self.font_var = tk.StringVar(value="宋体")
        self.size_var = tk.StringVar(value="10.5")
        self.bold_var = tk.BooleanVar(value=False)
        self.alignment_var = tk.StringVar(value="左对齐")
        self._build_toolbar()
        self._build_surface()

    def _build_toolbar(self) -> None:
        """Build compact controls for the read-only rendered-page preview."""

        bar = ttk.Frame(self, padding=(0, 0, 0, 8))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="整卷预览", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(
            bar, text="上一页", command=self.previous_page
        ).pack(side=tk.LEFT, padx=(16, 3))
        ttk.Button(
            bar, text="下一页", command=self.next_page
        ).pack(side=tk.LEFT, padx=3)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=8
        )
        ttk.Button(
            bar, text="缩小", command=lambda: self.change_zoom(-0.08)
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(
            bar, text="放大", command=lambda: self.change_zoom(0.08)
        ).pack(side=tk.LEFT, padx=3)
        ttk.Label(bar, textvariable=self.status_variable).pack(side=tk.RIGHT)

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

    def set_preview_pages(
        self,
        pages: list[Path | str] | tuple[Path | str, ...],
        *,
        locators: dict[int, tuple[int, float]] | None = None,
        actual_pages: int | None = None,
        raw_exam: dict[str, Any] | None = None,
        selected_key: DocumentKey | None = None,
    ) -> None:
        """Show the actual internally rendered pages as a stable read-only canvas.

        The page images come from the same rendering pipeline used for the
        preview. This avoids the old semantic Text-widget approximation, which
        could reflow lines differently from the generated document.
        """

        previous_view = self._capture_view()
        self._rendering = True
        self._image_preview = True
        if raw_exam is not None:
            self.raw_exam = raw_exam
        if locators is not None:
            self.locators = dict(locators)
        if actual_pages is not None:
            self.actual_pages = max(1, int(actual_pages))
        if selected_key is not None:
            self.selected_key = selected_key
        self.preview_pages = [Path(item) for item in pages if Path(item).is_file()]

        for child in self.surface.winfo_children():
            child.destroy()
        self.text_widgets.clear()
        self.page_frames.clear()
        self.block_pages.clear()
        self.preview_image_refs.clear()

        for page_index, page_path in enumerate(self.preview_pages):
            with Image.open(page_path) as source:
                image = source.convert("RGB")
                target_width = max(400, int(image.width * self.zoom))
                target_height = max(1, int(image.height * target_width / image.width))
                resized = image.resize(
                    (target_width, target_height),
                    Image.Resampling.LANCZOS,
                )
            photo = ImageTk.PhotoImage(resized)
            self.preview_image_refs.append(photo)

            holder = tk.Frame(self.surface, background="#D7DBE0")
            holder.pack(pady=(14, 8))
            caption = tk.Label(
                holder,
                text=f"第 {page_index + 1} 页",
                background="#D7DBE0",
                foreground="#5C6370",
                font=("Microsoft YaHei UI", 9),
            )
            caption.pack(anchor=tk.W, padx=4, pady=(0, 3))
            self._bind_wheel(holder)
            self._bind_wheel(caption)
            page = tk.Frame(
                holder,
                background="#FFFFFF",
                width=target_width + 2,
                height=target_height + 2,
                highlightbackground="#B8BDC4",
                highlightthickness=1,
            )
            page.pack()
            page.pack_propagate(False)
            image_label = tk.Label(
                page,
                image=photo,
                background="#FFFFFF",
                borderwidth=0,
                highlightthickness=0,
            )
            image_label.pack(fill=tk.BOTH, expand=True)
            self._bind_wheel(page)
            self._bind_wheel(image_label)
            self.page_frames.append(page)

        if self.page_frames:
            for key, location in self.locators.items():
                if isinstance(key, int):
                    page_index = max(0, min(int(location[0]), len(self.page_frames) - 1))
                    self.block_pages[key] = page_index

        self._rendering = False
        self._last_surface_width = 0
        self._last_scrollregion = None
        self._queue_layout_sync()
        if selected_key is not None and selected_key in self.block_pages:
            self._schedule_scroll_to(selected_key)
        elif previous_view is not None:
            self._restore_view(previous_view)
        else:
            self._update_status(0)

    def render(
        self,
        raw_exam: dict[str, Any],
        *,
        locators: dict[int, tuple[int, float]] | None = None,
        actual_pages: int | None = None,
        selected_key: DocumentKey | None = None,
    ) -> None:
        """Render the entire structured document as read-only A4 pages."""

        previous_view = self._capture_view()
        self._rendering = True
        self._image_preview = False
        self.preview_pages = []
        self.raw_exam = raw_exam
        if locators is not None:
            self.locators = dict(locators)
        if actual_pages is not None:
            self.actual_pages = max(1, int(actual_pages))
        self.selected_key = selected_key
        for child in self.surface.winfo_children():
            child.destroy()
        self.text_widgets.clear()
        self.page_frames.clear()
        self.block_pages.clear()

        assignments, page_count = self._page_assignments(raw_exam)
        for page_index in range(page_count):
            self._create_page(page_index)

        metadata = raw_exam.get("metadata", {})
        metadata_entries = (
            ("meta:exam_name", str(metadata.get("exam_name", "")), "exam_name"),
            ("meta:subject_name", str(metadata.get("subject_name", "语　文")), "subject_name"),
            ("meta:meta_text", str(metadata.get("meta_text", "")), "exam_meta"),
        )
        for key, value, role in metadata_entries:
            if value.strip():
                self._add_editor(0, key, value, None, role)
        notices = [str(value) for value in metadata.get("notices", []) if str(value).strip()]
        if notices:
            self._add_editor(0, "meta:notices", "注意事项：\n" + "\n".join(
                f"{index}．{text}" for index, text in enumerate(notices, start=1)
            ), None, "notices")

        for index, block in enumerate(raw_exam.get("blocks", [])):
            if not isinstance(block, dict):
                continue
            text = block_editor_text(block)
            if not text and block.get("type") not in {"answer_table"}:
                continue
            page_index = assignments.get(index, 0)
            self._add_editor(page_index, index, text, block, str(block.get("type", "")))

        self._rendering = False
        self._last_surface_width = 0
        self._last_scrollregion = None
        self._queue_layout_sync()
        if selected_key is not None:
            self._schedule_scroll_to(selected_key)
        elif previous_view is not None:
            self._restore_view(previous_view)
        else:
            self._update_status(0)

    def _page_assignments(self, raw_exam: dict[str, Any]) -> tuple[dict[int, int], int]:
        if self.locators:
            assignments = {
                index: max(0, int(self.locators.get(index, (0, 0.0))[0]))
                for index, _block in enumerate(raw_exam.get("blocks", []))
            }
            highest = max(assignments.values(), default=0) + 1
            return assignments, max(self.actual_pages, highest)

        assignments: dict[int, int] = {}
        page = 0
        used = 250
        capacity = 930
        for index, block in enumerate(raw_exam.get("blocks", [])):
            text = block_editor_text(block) if isinstance(block, dict) else ""
            height = self._estimated_height(text)
            if used + height > capacity and used > 250:
                page += 1
                used = 70
            assignments[index] = page
            used += height
        return assignments, max(1, page + 1)

    def _create_page(self, page_index: int) -> None:
        width = max(620, int(794 * self.zoom))
        height = max(880, int(1123 * self.zoom))
        holder = tk.Frame(self.surface, background="#D7DBE0")
        holder.pack(pady=(14, 8))
        caption = tk.Label(
            holder,
            text=f"第 {page_index + 1} 页",
            background="#D7DBE0",
            foreground="#5C6370",
            font=("Microsoft YaHei UI", 9),
        )
        caption.pack(anchor=tk.W, padx=4, pady=(0, 3))
        self._bind_wheel(holder)
        self._bind_wheel(caption)
        page = tk.Frame(
            holder,
            background="#FFFFFF",
            width=width,
            height=height,
            highlightbackground="#B8BDC4",
            highlightthickness=1,
        )
        page.pack()
        page.pack_propagate(False)
        content = tk.Frame(page, background="#FFFFFF")
        content.pack(
            fill=tk.BOTH,
            expand=True,
            padx=max(38, int(66 * self.zoom)),
            pady=max(34, int(58 * self.zoom)),
        )
        page.content = content  # type: ignore[attr-defined]
        self.page_frames.append(page)

    def _add_editor(
        self,
        page_index: int,
        key: DocumentKey,
        value: str,
        block: dict[str, Any] | None,
        role: str,
    ) -> None:
        page_index = min(max(0, page_index), len(self.page_frames) - 1)
        content = self.page_frames[page_index].content  # type: ignore[attr-defined]
        lines = self._visual_line_count(value)
        editor = tk.Text(
            content,
            height=max(1, lines),
            wrap=tk.CHAR,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=1 if key == self.selected_key else 0,
            highlightbackground="#0078D4",
            highlightcolor="#0078D4",
            background="#FFFFFF",
            foreground="#111111",
            insertbackground="#111111",
            selectbackground="#CCE8FF",
            selectforeground="#111111",
            padx=2,
            pady=1,
            undo=True,
            maxundo=-1,
            font=("SimSun", max(8, int(round(10.5 * self.zoom)))),
        )
        editor.pack(fill=tk.X, pady=(0, max(1, int(2 * self.zoom))))
        editor.insert("1.0", value)
        editor.edit_modified(False)
        editor._document_key = key  # type: ignore[attr-defined]
        editor._last_value = value  # type: ignore[attr-defined]
        editor._pending_after = None  # type: ignore[attr-defined]
        editor.bind("<<Modified>>", lambda _event, item=editor: self._modified(item))
        editor.bind("<FocusIn>", lambda _event, item=editor: self._focus_editor(item))
        editor.bind("<ButtonRelease-1>", lambda _event, item=editor: self._sync_toolbar(item), add="+")
        self.text_widgets[key] = editor
        self.block_pages[key] = page_index
        self._apply_semantic_style(editor, block, role)
        # The right side is a preview surface. Content and formatting changes
        # are deliberately routed through the left editor panel.
        editor.configure(state=tk.DISABLED, takefocus=False, cursor="arrow")

    def _apply_semantic_style(
        self,
        editor: tk.Text,
        block: dict[str, Any] | None,
        role: str,
    ) -> None:
        editor.tag_configure("all", spacing2=2, spacing3=1)
        editor.tag_add("all", "1.0", tk.END)
        if role == "exam_name":
            self._line_tag(editor, 1, "SimSun", 16, False, "center")
            return
        if role == "subject_name":
            self._line_tag(editor, 1, "SimHei", 22, True, "center")
            return
        if role == "exam_meta":
            self._line_tag(editor, 1, "SimSun", 10.5, False, "center")
            return
        if role == "notices":
            self._line_tag(editor, 1, "SimHei", 10.5, True, "left")
            for line in range(2, int(editor.index("end-1c").split(".")[0]) + 1):
                self._line_tag(editor, line, "SimSun", 10.5, False, "left", indent=2)
            return
        if block is None:
            return
        kind = str(block.get("type", ""))
        if kind == "section_title":
            self._line_tag(editor, 1, "SimHei", 12, True, "left")
        elif kind == "subsection":
            self._line_tag(editor, 1, "SimSun", 10.5, True, "left")
        elif kind == "instruction":
            self._line_tag(editor, 1, "SimSun", 10.5, False, "left", indent=2)
        elif kind in {"material", "poetry"}:
            self._style_material(editor, block)
        elif kind == "question":
            self._style_question(editor, block.get("question", {}))
        elif kind.startswith("answer_"):
            family = "SimHei" if kind in {"answer_section", "answer_subsection"} else "SimSun"
            self._line_tag(editor, 1, family, 10.5 if kind != "answer_section" else 12, kind == "answer_subsection", "left")

    def _style_material(self, editor: tk.Text, block: dict[str, Any]) -> None:
        line = 1
        if block.get("title"):
            self._line_tag(editor, line, "SimHei", 10.5, False, "center")
            line += 1
        if block.get("author"):
            self._line_tag(editor, line, "FangSong", 10.5, False, "center")
            line += 1
        body_align = "center" if block.get("type") == "poetry" else "left"
        roles = [str(value) for value in block.get("paragraph_roles", [])]
        for index, _value in enumerate(block.get("paragraphs", [])):
            paragraph_role = roles[index] if index < len(roles) else "body"
            if paragraph_role in {"source", "publication_note"}:
                self._line_tag(editor, line, "FangSong", 10.5, False, "right")
            elif paragraph_role == "author":
                self._line_tag(editor, line, "FangSong", 10.5, False, "center")
            else:
                self._line_tag(editor, line, "KaiTi", 10.5, False, body_align, indent=0 if body_align == "center" else 2)
            line += 1
        if block.get("note"):
            self._line_tag(editor, line, "FangSong", 9, False, "left", indent=2)
            line += 1
        if block.get("source"):
            self._line_tag(editor, line, "FangSong", 10.5, False, "right")

    def _style_question(self, editor: tk.Text, question: dict[str, Any]) -> None:
        lines, mapping = question_editor_lines(question)
        spec = question.get("format", {}) if isinstance(question.get("format"), dict) else {}
        for line, item in enumerate(mapping, start=1):
            target = str(item.get("target", "stem"))
            if target in {"stem", "option", "subquestion", "composition_prompt", "composition_requirements"}:
                family = "SimSun"
            else:
                family = "KaiTi"
            size = float(spec.get("size_pt", 10.5)) if target == "stem" else 10.5
            align = self._alignment_code(str(spec.get("alignment", "左对齐"))) if target == "stem" else "left"
            indent = 1.5 if target == "option" else (2 if target in {"subquestion", "composition_material", "composition_prompt", "composition_requirements", "segmentation"} else 0)
            self._line_tag(editor, line, family, size, bool(spec.get("bold", False)) if target == "stem" else False, align, indent=indent)
            if target == "embedded":
                segments = question.get("embedded_segments", [])
                target_index = int(item.get("target_index", 0))
                if target_index < len(segments):
                    cursor = 0
                    for segment_index, segment in enumerate(segments[target_index]):
                        text = str(segment.get("text", ""))
                        if segment.get("role") == "label":
                            name = f"embedded-label-{line}-{segment_index}"
                            editor.tag_configure(name, font=("SimSun", max(7, int(round(10.5 * self.zoom)))))
                            editor.tag_add(name, f"{line}.{cursor}", f"{line}.{cursor + len(text)}")
                        cursor += len(text)
        for index, entry in enumerate(question.get("inline_formats", [])):
            try:
                line = int(entry.get("line", 0)) + 1
                start = int(entry.get("start", 0))
                end = int(entry.get("end", 0))
            except (TypeError, ValueError):
                continue
            name = f"inline-{index}"
            family = FONT_TO_TK.get(str(entry.get("font", "宋体")), str(entry.get("font", "SimSun")))
            size = max(7, int(round(float(entry.get("size_pt", 10.5)) * self.zoom)))
            editor.tag_configure(name, font=(family, size, "bold" if entry.get("bold") else "normal"))
            editor.tag_add(name, f"{line}.{start}", f"{line}.{end}")
            editor.tag_raise(name)

    def _line_tag(
        self,
        editor: tk.Text,
        line: int,
        family: str,
        size: float,
        bold: bool,
        justify: str,
        *,
        indent: float = 0,
    ) -> None:
        name = f"semantic-{line}"
        font = (family, max(7, int(round(size * self.zoom))), "bold" if bold else "normal")
        margin = int(size * indent * self.zoom)
        editor.tag_configure(
            name,
            font=font,
            justify=justify,
            lmargin1=margin,
            lmargin2=margin,
            spacing2=max(1, int(2 * self.zoom)),
            spacing3=max(1, int(2 * self.zoom)),
        )
        editor.tag_add(name, f"{line}.0", f"{line}.end")

    @staticmethod
    def _alignment_code(value: str) -> str:
        return {
            "居中": "center",
            "右对齐": "right",
            "两端对齐": "left",
            "center": "center",
            "right": "right",
        }.get(value, "left")

    def _modified(self, editor: tk.Text) -> None:
        if (
            self._rendering
            or str(editor.cget("state")) == tk.DISABLED
            or not editor.edit_modified()
        ):
            return
        editor.edit_modified(False)
        value = editor.get("1.0", "end-1c")
        editor.configure(height=max(1, self._visual_line_count(value)))
        pending = getattr(editor, "_pending_after", None)
        if pending:
            editor.after_cancel(pending)
        editor._pending_after = editor.after(260, lambda: self._commit_editor(editor))  # type: ignore[attr-defined]
        self.status_variable.set("正在同步文字和分页……")

    def _commit_editor(self, editor: tk.Text) -> None:
        editor._pending_after = None  # type: ignore[attr-defined]
        key = editor._document_key  # type: ignore[attr-defined]
        old_value = str(editor._last_value)  # type: ignore[attr-defined]
        new_value = editor.get("1.0", "end-1c")
        if new_value == old_value:
            return
        editor._last_value = new_value  # type: ignore[attr-defined]
        self.on_change(key, new_value, old_value)

    def _focus_editor(self, editor: tk.Text) -> None:
        key = editor._document_key  # type: ignore[attr-defined]
        if self._selection_syncing:
            return
        previous_key = self.selected_key
        self.selected_key = key
        for item_key, item in self.text_widgets.items():
            item.configure(highlightthickness=1 if item_key == key else 0)
        self._sync_toolbar(editor)
        # Focus is also used when restoring the current viewport. Notify the
        # owner only when the semantic selection actually changes, otherwise
        # tree-selection callbacks can recursively move the canvas again.
        if previous_key != key:
            self._selection_syncing = True
            try:
                self.on_select(key)
            finally:
                self._selection_syncing = False
        self._update_status(self.block_pages.get(key, 0))

    def _sync_toolbar(self, editor: tk.Text) -> None:
        index = editor.index(tk.INSERT)
        tags = editor.tag_names(index)
        for tag in reversed(tags):
            config = editor.tag_configure(tag)
            font_value = config.get("font", (None, None, None, None, None))[-1]
            if not font_value:
                continue
            try:
                family, size, *rest = editor.tk.splitlist(font_value)
                ui_font = next((key for key, value in FONT_TO_TK.items() if value == family and key in {"宋体", "黑体", "楷体", "仿宋"}), family)
                self.font_var.set(ui_font)
                self.size_var.set(str(round(abs(float(size)) / self.zoom, 1)))
                self.bold_var.set("bold" in rest)
            except (ValueError, tk.TclError):
                pass
            break

    def apply_selection_format(self) -> None:
        """Keep the preview surface read-only."""

        messagebox.showinfo(
            "提示",
            "右侧预览为只读，请在左侧内容与格式区域进行修改。",
            parent=self,
        )

    def update_block(
        self,
        key: DocumentKey,
        value: str,
        block: dict[str, Any] | None = None,
    ) -> None:
        """Update one visible preview block while keeping it read-only."""

        editor = self.text_widgets.get(key)
        if editor is None:
            return
        previous_state = str(editor.cget("state"))
        editor.configure(state=tk.NORMAL)
        try:
            current = editor.get("1.0", "end-1c")
            if current != value:
                cursor = editor.index(tk.INSERT)
                editor.delete("1.0", tk.END)
                editor.insert("1.0", value)
                editor.mark_set(tk.INSERT, cursor)
                editor._last_value = value  # type: ignore[attr-defined]
                editor.edit_modified(False)
                editor.configure(
                    height=max(1, self._visual_line_count(value))
                )
            if block is not None:
                for tag in list(editor.tag_names()):
                    if tag.startswith(("semantic-", "embedded-label-", "inline-")):
                        editor.tag_delete(tag)
                self._apply_semantic_style(
                    editor, block, str(block.get("type", ""))
                )
        finally:
            editor.configure(
                state=previous_state,
                takefocus=False,
                cursor="arrow",
            )

    def set_pagination(
        self,
        raw_exam: dict[str, Any],
        locators: dict[int, tuple[int, float]],
        actual_pages: int,
    ) -> None:
        """Apply background preview pagination after the user pauses typing."""

        signature = (
            id(raw_exam),
            int(actual_pages),
            tuple(
                (int(index), int(page), round(float(vertical), 6))
                for index, (page, vertical) in sorted(locators.items())
            ),
        )
        if signature == self._pagination_signature:
            self.raw_exam = raw_exam
            return
        self._pagination_signature = signature
        focus_key = self.selected_key
        self.render(
            raw_exam,
            locators=locators,
            actual_pages=actual_pages,
            selected_key=focus_key,
        )

    def _scroll_to_y(self, y: float) -> None:
        """Move the canvas to a surface coordinate using the real scroll extent."""

        self.update_idletasks()
        bbox = self.canvas.bbox("all")
        if not bbox:
            return
        viewport = max(1, self.canvas.winfo_height())
        extent = max(1.0, float(bbox[3] - bbox[1]) - viewport)
        target = max(0.0, float(y) - float(bbox[1]))
        self.canvas.yview_moveto(max(0.0, min(1.0, target / extent)))

    def scroll_to(self, key: DocumentKey) -> None:
        if key not in self.block_pages or not self.page_frames:
            return
        page_index = self.block_pages.get(key, 0)
        page = self.page_frames[min(page_index, len(self.page_frames) - 1)]
        self._scroll_to_y(page.master.winfo_y())
        self.selected_key = key
        for item_key, item in self.text_widgets.items():
            item.configure(highlightthickness=1 if item_key == key else 0)
        self._update_status(page_index)

    def previous_page(self) -> None:
        page = max(0, self.current_page() - 1)
        self._scroll_page(page)

    def next_page(self) -> None:
        page = min(max(0, len(self.page_frames) - 1), self.current_page() + 1)
        self._scroll_page(page)

    def current_page(self) -> int:
        if not self.page_frames:
            return 0
        if not self._image_preview and self.selected_key in self.block_pages:
            return self.block_pages[self.selected_key]
        top = self.canvas.canvasy(0)
        positions = [page.master.winfo_y() for page in self.page_frames]
        return min(range(len(positions)), key=lambda index: abs(positions[index] - top))

    def _scroll_page(self, page_index: int) -> None:
        if not self.page_frames:
            return
        self.update_idletasks()
        holder = self.page_frames[page_index].master
        self._scroll_to_y(holder.winfo_y())
        self._update_status(page_index)

    def change_zoom(self, delta: float) -> None:
        self.zoom = min(1.35, max(0.58, self.zoom + delta))
        if self._image_preview and self.preview_pages:
            self.set_preview_pages(
                self.preview_pages,
                locators=self.locators,
                actual_pages=self.actual_pages,
                raw_exam=self.raw_exam,
                selected_key=self.selected_key,
            )
            return
        self.render(
            self.raw_exam,
            locators=self.locators,
            actual_pages=self.actual_pages,
            selected_key=self.selected_key,
        )

    def _update_status(self, page_index: int) -> None:
        page_count = max(1, len(self.page_frames))
        self.status_variable.set(
            f"第 {page_index + 1} / {page_count} 页 "
            f"{int(self.zoom * 100)}% " + "只读预览"
        )

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

    def _capture_view(self) -> tuple[float, float] | None:
        if not hasattr(self, "canvas") or not self.canvas.winfo_exists():
            return None
        try:
            x_view = self.canvas.xview()
            y_view = self.canvas.yview()
        except tk.TclError:
            return None
        if not x_view or not y_view:
            return None
        return float(x_view[0]), float(y_view[0])

    def _restore_view(self, view: tuple[float, float]) -> None:
        if self._scroll_after:
            try:
                self.after_cancel(self._scroll_after)
            except tk.TclError:
                pass
        self._scroll_after = self.after(30, lambda: self._apply_view(view))

    def _apply_view(self, view: tuple[float, float]) -> None:
        self._scroll_after = None
        if not self.winfo_exists():
            return
        try:
            self.canvas.xview_moveto(max(0.0, min(1.0, view[0])))
            self.canvas.yview_moveto(max(0.0, min(1.0, view[1])))
        except tk.TclError:
            return

    def _schedule_scroll_to(self, key: DocumentKey) -> None:
        if self._scroll_after:
            try:
                self.after_cancel(self._scroll_after)
            except tk.TclError:
                pass
        self._scroll_after = self.after(30, lambda: self.scroll_to(key))

    @staticmethod
    def _visual_line_count(value: str) -> int:
        return max(1, sum(max(1, ceil(len(line) / 62)) for line in value.splitlines() or [""]))

    @classmethod
    def _estimated_height(cls, value: str) -> int:
        return cls._visual_line_count(value) * 19 + 6


__all__ = [
    "EditableA4Canvas",
    "block_editor_text",
    "question_editor_lines",
]
