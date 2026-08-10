"""Current contextual inspector with a stable read-only page preview."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import ctypes
from decimal import Decimal
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Iterable

import desktop_workbench_base as legacy_base
import app.internal_preview_core as internal_preview_core
import app.inspector_model as inspector_model
from app.config import load_layout
from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.export_naming import default_export_basename, with_export_suffix
from app.flexible_importers import save_exam
from app.github_updater import (
    check_latest_release,
    download_release_asset,
    schedule_portable_update,
)
from app.github_update_page import GITHUB_REPOSITORY, GitHubUpdatePage
from app.inspector_model import (
    ContentObject,
    build_object_locators,
    content_objects_for_block,
    format_owner,
    inline_formats_for,
    metadata_content_objects,
    objects_in_scope,
    paragraph_format_for,
    remove_paragraph_format,
    set_content_object_text,
    set_inline_format,
    set_paragraph_format,
    summary_text,
)
from app.page_layout import adjusted_layout
from app.preview_service import PreviewResult, PreviewService
from app.read_only_preview import ReadOnlyPreview
from app.score_summary import TARGET_SCORE, ScoreSummary, calculate_score_summary, format_score, parse_score
from app.windows_drop import install_file_drop, uninstall_file_drop
from app.version import APP_TITLE, APP_VERSION
from app.windows_activation import install_activation_palette, remove_duplicate_update_button
from app.windows_style import apply_windows_style
from desktop_workbench_base import DesktopApp as LegacyWorkbench


legacy_base.VERSION = APP_VERSION
legacy_base.build_documents = build_documents
internal_preview_core.adjusted_layout = adjusted_layout


def typeset_name(value: str) -> str:
    """Return a safe document name with one Chinese typeset suffix."""

    name = legacy_base._safe_filename(value.strip())
    suffix = "（排版）"
    return name if name.endswith(suffix) else f"{name}{suffix}"


_base_default_format = inspector_model.default_format_for


def _release_default_format(item: ContentObject) -> dict[str, Any]:
    """Return the confirmed template defaults for an object."""

    spec = _base_default_format(item)
    if item.role in {"material_title", "subject_name"}:
        spec["bold"] = False
    return spec


inspector_model.default_format_for = _release_default_format


FONT_CHOICES = ("宋体", "黑体", "楷体", "仿宋")
ALIGNMENT_CHOICES = ("左对齐", "居中", "右对齐", "两端对齐")
SPECIAL_INDENTS = ("无", "首行", "悬挂")
BATCH_SCOPES = ("当前题目", "当前大题", "整份试卷")


class CollapsibleGroup(ttk.Frame):
    """A compact property group with an optional template reset action."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        *,
        expanded: bool = True,
        reset_command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, style="InspectorGroup.TFrame")
        self.title = title
        self.expanded = expanded
        header = ttk.Frame(self, style="InspectorHeader.TFrame", padding=(6, 5))
        header.pack(fill=tk.X)
        self.toggle_button = ttk.Button(
            header,
            text="▾" if expanded else "▸",
            width=2,
            command=self.toggle,
            style="InspectorToggle.TButton",
        )
        self.toggle_button.pack(side=tk.LEFT)
        ttk.Label(
            header,
            text=title,
            style="InspectorHeader.TLabel",
        ).pack(side=tk.LEFT, padx=(4, 0))
        if reset_command is not None:
            ttk.Button(
                header,
                text="恢复模板值",
                command=reset_command,
                style="InspectorReset.TButton",
            ).pack(side=tk.RIGHT)
        self.body = ttk.Frame(self, padding=(8, 7, 8, 8), style="InspectorGroup.TFrame")
        if expanded:
            self.body.pack(fill=tk.X)

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.toggle_button.configure(text="▾" if expanded else "▸")
        if expanded:
            self.body.pack(fill=tk.X)
        else:
            self.body.pack_forget()


class ExportWarningDialog(tk.Toplevel):
    """Nonblocking validation choice shown immediately before DOCX export."""

    def __init__(self, parent: tk.Misc, lines: Iterable[str]) -> None:
        super().__init__(parent)
        self.result = False
        self.title("导出前提醒")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="当前试卷还有需要检查的项目",
            font=("Microsoft YaHei UI", 11, "bold"),
            foreground="#8A5700",
        ).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text="\n".join(lines),
            justify=tk.LEFT,
            wraplength=470,
        ).pack(anchor=tk.W, pady=(9, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="返回检查", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            buttons,
            text="继续导出",
            style="Primary.TButton",
            command=self._continue,
        ).pack(side=tk.RIGHT, padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self.geometry(f"520x{max(220, self.winfo_reqheight())}")

    def _continue(self) -> None:
        self.result = True
        self.destroy()


class CurrentDesktopApp(LegacyWorkbench):
    """Context-sensitive left inspector paired with a read-only A4 preview."""

    def __init__(self) -> None:
        self.undo_stack: list[dict[str, Any]] = []
        self.redo_stack: list[dict[str, Any]] = []
        self.history_transaction_open = False
        self.current_line_map: list[dict[str, Any]] = []
        self.inline_tag_specs: dict[str, dict[str, Any]] = {}
        self.selection_dirty = False
        self.loading_fields = False
        self.live_after_id: str | None = None
        self.right_after_id: str | None = None
        self.preview_pending = False
        self.preview_photos: list[Any] = []
        self.page_y_positions: list[int] = []
        self.preview_total_height = 1
        self.selected_block_type: str | None = None
        self._preview_service = PreviewService(build_documents)
        self._export_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="exam-export",
        )
        self._update_messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._preview_generation = 0
        self._export_generation = 0
        self._detail_window_width = 0
        self._canvas_committing = False
        self._canvas_preview_after: str | None = None
        self._preview_block_locators: dict[int, tuple[int, float]] = {}
        self._pending_preview_block: int | None = None
        self._editor_tag_specs: dict[str, tuple[str, float, bool]] = {}
        self.current_paragraph_formats: list[dict[str, Any]] = []
        self._startup_preview_deferred = True
        self._startup_preview_pending = False
        self.selected_content_object: ContentObject | None = None
        self._content_objects: list[ContentObject] = []
        self._text_commit_after: str | None = None
        self._global_commit_after: str | None = None
        self._search_after: str | None = None
        self._ui_sash_initialized = False
        self._warning_refreshing = False
        super().__init__()
        self.geometry("1500x900")
        self.minsize(1180, 720)
        self.configure(background="#F3F3F3")
        self._polish_widgets(self)
        self._enable_windows_rounding()
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
        self.after_idle(self._restore_editor_sash)
        self.after_idle(self._activate_file_drop)
        install_activation_palette(self)
        self.stem_text.bind("<ButtonRelease-1>", self._cursor_style_event, add="+")
        self.status_var.set("工作台已就绪，正在准备首次预览。")
        self.after(1300, self._release_startup_preview)
        self._set_application_icon()
        self.bind_all("<Control-z>", self.undo_action)
        self.bind_all("<Control-y>", self.redo_action)

    def _set_application_icon(self) -> None:
        """Load the bundled Windows application icon when available."""

        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        icon = root / "assets" / "app-icon.png"
        if not icon.exists():
            return
        try:
            self._app_icon = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self._app_icon)
        except tk.TclError:
            return

    def _polish_widgets(self, widget: tk.Misc) -> None:
        """Apply the Windows surface treatment to already-built widgets."""

        for child in widget.winfo_children():
            if isinstance(child, ttk.Panedwindow):
                child.configure(style="TFrame")
                for pane in child.winfo_children():
                    if isinstance(pane, ttk.Frame):
                        pane.configure(style="Panel.TFrame")
            elif isinstance(child, ttk.LabelFrame):
                child.configure(style="Card.TLabelframe")
            elif isinstance(child, tk.Text):
                child.configure(
                    background="#FFFFFF",
                    foreground="#1F1F1F",
                    insertbackground="#1F1F1F",
                    selectbackground="#0078D4",
                    selectforeground="#FFFFFF",
                    relief=tk.FLAT,
                    highlightthickness=1,
                    highlightbackground="#D7D7D7",
                    highlightcolor="#0067C0",
                    padx=10,
                    pady=9,
                    font=("SimSun", 10),
                )
            elif isinstance(child, tk.Canvas):
                child.configure(background="#E7E7E7", highlightthickness=0)
            self._polish_widgets(child)

    def _enable_windows_rounding(self) -> None:
        """Request rounded corners when the host Windows build supports them."""

        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            preference = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                33,
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except Exception:
            return

    def _build_ui(self) -> None:
        """Build the inherited workbench and keep one update entry."""

        super()._build_ui()
        for widget in self._walk_widgets(self):
            if not isinstance(widget, ttk.Label):
                continue
            text = str(widget.cget("text"))
            textvariable = str(widget.cget("textvariable"))
            if text.startswith("本人制作｜"):
                widget.configure(
                    text="个人制作｜公众号：蓑衣微言｜拒绝商用",
                    style="Prominent.Attribution.TLabel",
                )
                if isinstance(widget.master, ttk.Frame):
                    widget.master.configure(style="Prominent.Status.TFrame")
            elif textvariable == str(self.status_var):
                widget.configure(style="Prominent.Status.TLabel")
                if isinstance(widget.master, ttk.Frame):
                    widget.master.configure(style="Prominent.Status.TFrame")
            elif hasattr(self, "selection_hint_var") and textvariable == str(self.selection_hint_var):
                widget.configure(style="EditorHint.TLabel")
        remove_duplicate_update_button(self)

    @staticmethod
    def _walk_widgets(widget: tk.Misc) -> list[tk.Misc]:
        result: list[tk.Misc] = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(CurrentDesktopApp._walk_widgets(child))
        return result

    def _tag_block_lines(self, block: dict[str, Any]) -> None:
        """Add the right-aligned semantic style for material publication notes."""

        if str(block.get("type", "")).startswith("answer_"):
            self._tag_answer_block_lines(block)
            return
        inherited_tagger = getattr(super(), "_tag_block_lines", None)
        if callable(inherited_tagger):
            inherited_tagger(block)
        if block.get("type") != "material":
            return
        line = 1 + int(bool(block.get("title"))) + int(bool(block.get("author")))
        roles = [str(value) for value in block.get("paragraph_roles", [])]
        for index, _text in enumerate(block.get("paragraphs", [])):
            role = roles[index] if index < len(roles) else "body"
            if role == "subheading":
                self._add_semantic_tag(
                    f"semantic_role_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "SimHei",
                    10.5,
                    False,
                    justify="center",
                )
            elif role == "source":
                self._add_semantic_tag(
                    f"semantic_role_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "FangSong",
                    10.5,
                    False,
                    justify="right",
                )
            elif role == "label":
                self._add_semantic_tag(
                    f"semantic_role_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "SimHei",
                    10.5,
                    False,
                )
            elif role == "publication_note":
                self._add_semantic_tag(
                    f"semantic_publication_note_{line}",
                    f"{line}.0",
                    f"{line}.end",
                    "SimSun",
                    10.5,
                    False,
                    justify="right",
                )
            line += 1

    def _add_semantic_tag(
        self,
        name: str,
        start: str,
        end: str,
        font: str,
        size: float,
        bold: bool,
        *,
        justify: str = "left",
    ) -> None:
        """Apply a semantic font tag to the optional left editor widget."""

        self._editor_tag_specs[name] = (font, size, bold)
        self.stem_text.tag_configure(
            name,
            font=(font, max(7, int(round(size))), "bold" if bold else "normal"),
            justify=justify,
        )
        self.stem_text.tag_add(name, start, end)

    def _tag_answer_block_lines(self, block: dict[str, Any]) -> None:
        """Preserve the established answer-section semantic font mapping."""

        kind = block.get("type")
        if kind == "answer_section":
            self._add_semantic_tag(
                "semantic_answer_section", "1.0", "1.end", "SimHei", 12, False
            )
            return
        if kind == "answer_subsection":
            self._add_semantic_tag(
                "semantic_answer_subsection", "1.0", "1.end", "SimSun", 10.5, True
            )
            return
        if kind == "answer_table":
            self._add_semantic_tag(
                "semantic_answer_table",
                "1.0",
                "1.end",
                "SimSun",
                10.5,
                False,
                justify="center",
            )
            return
        line = 1
        if block.get("header"):
            self._add_semantic_tag(
                f"semantic_answer_header_{line}",
                f"{line}.0",
                f"{line}.end",
                "SimSun",
                10.5,
                False,
            )
            line += 1
        for entry in block.get("paragraphs", []):
            role = str(entry.get("role", "subjective_answer"))
            font = {
                "objective_answer": "SimSun",
                "subjective_answer": "KaiTi",
                "mixed_answer": "KaiTi",
                "answer_label": "SimSun",
                "example_label": "SimHei",
                "scoring_label": "SimHei",
                "scoring_rule": "SimSun",
                "translation_label": "SimHei",
                "translation_body": "SimSun",
                "composition": "SimSun",
            }.get(role, "KaiTi")
            self._add_semantic_tag(
                f"semantic_answer_{line}",
                f"{line}.0",
                f"{line}.end",
                font,
                10.5,
                False,
            )
            line += 1

    def _save_selected_paragraph_formats(self) -> None:
        """Keep paragraph overrides semantic when applying a font selection."""

        if not hasattr(self, "current_line_map") or not self.stem_text.tag_ranges(tk.SEL):
            return
        selected_lines = self._selected_line_numbers()
        spec = self._format_spec_from_controls()
        for line_number in selected_lines:
            if line_number >= len(self.current_line_map):
                continue
            mapping = dict(self.current_line_map[line_number])
            entry = {
                **mapping,
                "left_indent_chars": spec["left_indent_chars"],
                "right_indent_chars": spec["right_indent_chars"],
                "special_indent": spec["special_indent"],
                "special_indent_chars": spec["special_indent_chars"],
                "alignment": spec["alignment"],
                "line_spacing": spec["line_spacing"],
                "space_before_pt": spec["space_before_pt"],
                "space_after_pt": spec["space_after_pt"],
            }
            key = (entry.get("target"), int(entry.get("target_index", 0)))
            self.current_paragraph_formats = [
                value
                for value in self.current_paragraph_formats
                if (
                    value.get("target"),
                    int(value.get("target_index", 0)),
                )
                != key
            ]
            self.current_paragraph_formats.append(entry)

    def _selected_line_numbers(self) -> list[int]:
        """Return zero-based paragraph lines touched by the current selection."""

        start = self.stem_text.index(tk.SEL_FIRST)
        end = self.stem_text.index(tk.SEL_LAST)
        start_line, _start_col = map(int, start.split("."))
        end_line, end_col = map(int, end.split("."))
        if end_col == 0 and end_line > start_line:
            end_line -= 1
        return list(range(start_line - 1, end_line))

    def _push_direct_history(self) -> None:
        """Record one complete-document snapshot before inspector changes."""

        if not hasattr(self, "undo_stack"):
            return
        self.undo_stack.append(deepcopy(self.raw_exam))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.history_transaction_open = False

    @staticmethod
    def _score_difference_text(summary: ScoreSummary) -> str:
        """Describe the distance between entered scores and the 150-point target."""

        if summary.difference > 0:
            return f"还差 {format_score(summary.difference)} 分"
        if summary.difference < 0:
            return f"超出 {format_score(-summary.difference)} 分"
        return "总分已达到 150 分"

    def _show_current_page(self) -> None:
        """Keep the preview status synchronized with the A4 page canvas."""

        if hasattr(self, "document_editor"):
            self.document_editor._update_status(self.document_editor.current_page())

    def previous_page(self) -> None:
        """Show the preceding rendered page."""

        if hasattr(self, "document_editor"):
            self.document_editor.previous_page()

    def next_page(self) -> None:
        """Show the following rendered page."""

        if hasattr(self, "document_editor"):
            self.document_editor.next_page()

    def change_zoom(self, delta: float) -> None:
        """Adjust the A4 preview zoom while retaining the current page."""

        if hasattr(self, "document_editor"):
            self.document_editor.change_zoom(delta)

    def _release_startup_preview(self) -> None:
        """Release the delayed first preview after the main window is ready."""

        self._startup_preview_deferred = False
        if self._startup_preview_pending:
            self._startup_preview_pending = False
            self.request_preview()

    def _run_canvas_preview(self) -> None:
        """Run the debounced preview request scheduled by inspector edits."""

        self._canvas_preview_after = None
        self.request_preview()

    def request_preview(self) -> None:
        """Submit one cancellable internal-preview task for the current draft."""

        if getattr(self, "_startup_preview_deferred", False):
            self._startup_preview_pending = True
            return
        if self.busy:
            self._preview_service.cancel()
            self.busy = False
            self.busy_bar.stop()
        self.apply_current_question(silent=True)
        selection = self.tree.selection()
        if selection and selection[0].startswith("block-"):
            self._pending_preview_block = int(selection[0].split("-", 1)[1])
        raw = deepcopy(self.raw_exam)
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在生成预览，请稍候…")
        try:
            self._preview_generation = self._preview_service.submit(
                raw,
                self.layout_path,
                self.template_path,
                self._receive_preview_result,
            )
        except Exception as exc:  # noqa: BLE001
            self._finish_task_error(exc)

    def _receive_preview_result(self, result: PreviewResult | Exception) -> None:
        """Marshal the worker result back to Tk's main thread."""

        try:
            self.after(0, lambda: self._finish_preview(result))
        except tk.TclError:
            return

    def _finish_task_error(self, error: Exception) -> None:
        """Stop the busy indicators and show an actionable preview error."""

        self.busy = False
        self.busy_bar.stop()
        self.status_var.set("任务失败，请查看提示")
        try:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
        except tk.TclError:
            return

    def start_export(
        self,
        output_dir: Path,
        basename: str,
        export_docx: bool,
        export_pdf: bool,
    ) -> None:
        """Export the current draft through the unified DOCX pipeline."""

        if self.busy:
            messagebox.showinfo(APP_TITLE, "请等待当前任务完成。", parent=self)
            return
        self.apply_current_question(silent=True)
        safe_name = basename.strip() or "语文试卷"
        if not safe_name.endswith("（排版）"):
            safe_name += "（排版）"
        raw = deepcopy(self.raw_exam)
        template = self.template_path
        self._export_generation += 1
        generation = self._export_generation
        self.busy = True
        self.busy_bar.start(12)
        self.status_var.set("正在导出 Word 文件，请稍候…")

        def worker() -> tuple[int, Path | None, Path | None, str] | Exception:
            try:
                docx, pdf, engine = build_documents(
                    raw,
                    self.layout_path,
                    output_dir,
                    safe_name,
                    template_path=template,
                    export_docx=export_docx,
                    export_pdf=export_pdf,
                    temporary_dir=self.temp_dir,
                )
                return generation, docx, pdf, engine
            except Exception as exc:  # noqa: BLE001
                return exc

        future = self._export_executor.submit(worker)

        def done(completed: Any) -> None:
            result = completed.result()
            try:
                self.after(0, lambda: self._finish_export(result))
            except tk.TclError:
                return

        future.add_done_callback(done)

    def _finish_export(self, result: Any) -> None:
        """Publish export completion on the Tk thread."""

        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        generation, docx, pdf, engine = result
        if generation != self._export_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        lines = ["导出完成。"]
        if docx:
            lines.append(f"Word：{docx}")
        if pdf:
            lines.append(f"PDF：{pdf}")
            lines.append(f"转换引擎：{engine}")
        self.status_var.set("正式文件已导出")
        messagebox.showinfo(APP_TITLE, "\n".join(lines), parent=self)

    def _create_variables(self) -> None:
        super()._create_variables()
        self.target_pages_var = tk.StringVar(master=self, value="8")
        self.search_var = tk.StringVar()
        self.warning_var = tk.StringVar()
        self.batch_scope_var = tk.StringVar(value="当前题目")
        self.margin_top_var = tk.StringVar(value="20")
        self.margin_bottom_var = tk.StringVar(value="18")
        self.margin_left_var = tk.StringVar(value="22")
        self.margin_right_var = tk.StringVar(value="18")
        if not hasattr(self, "bold_var"):
            self.bold_var = tk.BooleanVar(value=False)
        if not hasattr(self, "left_indent_var"):
            self.left_indent_var = tk.StringVar(value="0")
        if not hasattr(self, "right_indent_var"):
            self.right_indent_var = tk.StringVar(value="0")
        if not hasattr(self, "special_indent_var"):
            self.special_indent_var = tk.StringVar(value="无")
        if not hasattr(self, "special_indent_amount_var"):
            self.special_indent_amount_var = tk.StringVar(value="0")

    def _setup_styles(self) -> None:
        super()._setup_styles()
        apply_windows_style(self)
        style = ttk.Style(self)
        style.configure(
            "Prominent.Status.TLabel",
            background="#E6F2FF",
            foreground="#004578",
            font=("Segoe UI", 10, "bold"),
            padding=(8, 4),
        )
        style.configure(
            "Prominent.Attribution.TLabel",
            background="#E6F2FF",
            foreground="#3B5266",
            font=("Segoe UI", 9),
            padding=(6, 4),
        )
        style.configure("Prominent.Status.TFrame", background="#E6F2FF")
        style.configure(
            "EditorHint.TLabel",
            background="#FFF4CE",
            foreground="#6A4500",
            font=("Segoe UI", 9, "bold"),
            padding=(7, 4),
        )
        style.configure("InspectorGroup.TFrame", background="#FFFFFF")
        style.configure("InspectorHeader.TFrame", background="#F3F5F7")
        style.configure(
            "InspectorHeader.TLabel",
            background="#F3F5F7",
            foreground="#202124",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure("InspectorToggle.TButton", padding=(1, 1))
        style.configure("InspectorReset.TButton", padding=(5, 2), font=("Microsoft YaHei UI", 8))
        style.configure("ScoreStrip.TFrame", background="#EEF5FC")
        style.configure(
            "ScoreStrip.TLabel",
            background="#EEF5FC",
            foreground="#17324D",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure("Object.Treeview", rowheight=26)
        style.configure("Property.TSpinbox", arrowsize=12)

    def _build_local_format_controls(self) -> None:
        """The inspector owns selection-aware formatting controls."""

        return

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        top_actions = ttk.Frame(parent)
        top_actions.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(top_actions, text="试卷结构", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(top_actions, text="撤回", command=self.undo_action).pack(side=tk.RIGHT)
        ttk.Button(top_actions, text="前进", command=self.redo_action).pack(side=tk.RIGHT, padx=(0, 6))

        score_strip = ttk.Frame(parent, style="ScoreStrip.TFrame", padding=(8, 6))
        score_strip.pack(fill=tk.X, pady=(0, 5))
        self.score_total_label = ttk.Label(score_strip, text="当前 0 / 150 分", style="ScoreStrip.TLabel")
        self.score_total_label.pack(side=tk.LEFT)
        self.score_delta_label = ttk.Label(score_strip, text="待录入分值", style="ScoreStrip.TLabel")
        self.score_delta_label.pack(side=tk.RIGHT)

        self.warning_banner = tk.Label(
            parent,
            textvariable=self.warning_var,
            background="#FFF4CE",
            foreground="#6A4500",
            anchor=tk.W,
            justify=tk.LEFT,
            padx=8,
            pady=5,
            wraplength=470,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.warning_banner.pack(fill=tk.X, pady=(0, 6))

        self.editor_pane = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        self.editor_pane.pack(fill=tk.BOTH, expand=True)
        structure_panel = ttk.Frame(self.editor_pane)
        inspector_panel = ttk.Frame(self.editor_pane)
        self.editor_pane.add(structure_panel, weight=35)
        self.editor_pane.add(inspector_panel, weight=65)
        self.editor_pane.bind("<ButtonRelease-1>", self._save_editor_sash, add="+")

        search_row = ttk.Frame(structure_panel)
        search_row.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(search_row, text="查找").pack(side=tk.LEFT)
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(search_row, text="全部展开", command=lambda: self._set_tree_open(True)).pack(side=tk.LEFT)
        ttk.Button(search_row, text="全部收起", command=lambda: self._set_tree_open(False)).pack(side=tk.LEFT, padx=(4, 0))

        tree_frame = ttk.Frame(structure_panel)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("kind", "score"),
            show="tree headings",
            height=10,
        )
        self.tree.heading("#0", text="内容")
        self.tree.heading("kind", text="类型")
        self.tree.heading("score", text="分值")
        self.tree.column("#0", width=250, stretch=True)
        self.tree.column("kind", width=58, anchor=tk.CENTER)
        self.tree.column("score", width=48, anchor=tk.CENTER)
        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("warning", background="#FFF4CE", foreground="#6A4500")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self.selection_title_var = tk.StringVar(value="请选择结构节点")
        ttk.Label(
            inspector_panel,
            textvariable=self.selection_title_var,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor=tk.W, pady=(5, 5))
        shell = ttk.Frame(inspector_panel)
        shell.pack(fill=tk.BOTH, expand=True)
        self.detail_canvas = tk.Canvas(shell, highlightthickness=0, background="#FFFFFF")
        detail_scroll = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=detail_scroll.set)
        self.detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_frame = ttk.Frame(self.detail_canvas, padding=(2, 2, 8, 12), style="InspectorGroup.TFrame")
        self.detail_window = self.detail_canvas.create_window((0, 0), window=self.detail_frame, anchor=tk.NW)
        self.detail_frame.bind("<Configure>", self._sync_detail_scrollregion)
        self.detail_canvas.bind("<Configure>", self._resize_detail_window)
        self.detail_canvas.bind("<MouseWheel>", self._on_detail_wheel)
        self._build_inspector_groups()

    def _build_inspector_groups(self) -> None:
        self.content_group = CollapsibleGroup(self.detail_frame, "内容与题型", expanded=True)
        self.font_group = CollapsibleGroup(
            self.detail_frame,
            "字体",
            expanded=True,
            reset_command=lambda: self._restore_group("font"),
        )
        self.paragraph_group = CollapsibleGroup(
            self.detail_frame,
            "段落",
            expanded=True,
            reset_command=lambda: self._restore_group("paragraph"),
        )
        self.options_group = CollapsibleGroup(
            self.detail_frame,
            "选择项",
            expanded=False,
            reset_command=lambda: self._restore_group("options"),
        )
        self.pagination_group = CollapsibleGroup(
            self.detail_frame,
            "分页",
            expanded=False,
            reset_command=lambda: self._restore_group("pagination"),
        )
        self.global_group = CollapsibleGroup(
            self.detail_frame,
            "整卷设置",
            expanded=True,
            reset_command=lambda: self._restore_group("global"),
        )
        self._all_groups = (
            self.content_group,
            self.font_group,
            self.paragraph_group,
            self.options_group,
            self.pagination_group,
            self.global_group,
        )
        self._build_content_group(self.content_group.body)
        self._build_font_group(self.font_group.body)
        self._build_paragraph_group(self.paragraph_group.body)
        self._build_options_group(self.options_group.body)
        self._build_pagination_group(self.pagination_group.body)
        self._build_global_group(self.global_group.body)
        self._show_groups({"内容与题型", "整卷设置"})

    def _build_content_group(self, parent: ttk.Frame) -> None:
        self.identity_frame = ttk.Frame(parent)
        self.identity_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(self.identity_frame, text="题型").grid(row=0, column=0, sticky=tk.W)
        self.kind_combo = ttk.Combobox(
            self.identity_frame,
            textvariable=self.kind_var,
            values=("客观题", "主观题", "结构内容"),
            state="readonly",
            width=9,
        )
        self.kind_combo.grid(row=0, column=1, sticky=tk.W, padx=(5, 12))
        ttk.Label(self.identity_frame, text="分值").grid(row=0, column=2, sticky=tk.W)
        self.score_entry = ttk.Entry(self.identity_frame, textvariable=self.score_var, width=7)
        self.score_entry.grid(row=0, column=3, sticky=tk.W, padx=(5, 0))

        ttk.Label(parent, text="内容对象").pack(anchor=tk.W)
        object_frame = ttk.Frame(parent)
        object_frame.pack(fill=tk.X, pady=(4, 6))
        self.object_tree = ttk.Treeview(
            object_frame,
            columns=("summary",),
            show="tree headings",
            height=5,
            style="Object.Treeview",
        )
        self.object_tree.heading("#0", text="对象")
        self.object_tree.heading("summary", text="内容摘要")
        self.object_tree.column("#0", width=92, stretch=False)
        self.object_tree.column("summary", width=290, stretch=True)
        object_scroll = ttk.Scrollbar(object_frame, orient=tk.VERTICAL, command=self.object_tree.yview)
        self.object_tree.configure(yscrollcommand=object_scroll.set)
        self.object_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        object_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.object_tree.bind("<<TreeviewSelect>>", self._on_content_object_select)

        ttk.Label(parent, text="选中内容").pack(anchor=tk.W)
        self.stem_text = tk.Text(
            parent,
            height=7,
            wrap=tk.WORD,
            relief=tk.SOLID,
            borderwidth=1,
            padx=8,
            pady=7,
            undo=True,
            autoseparators=True,
            maxundo=-1,
            exportselection=False,
        )
        self.stem_text.pack(fill=tk.X, pady=(4, 4))
        self.selection_hint_var = tk.StringVar(value="未选择文字时，字体设置作用于当前内容对象。")
        ttk.Label(
            parent,
            textvariable=self.selection_hint_var,
            foreground="#5B6470",
            wraplength=430,
        ).pack(anchor=tk.W)

    def _build_font_group(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="字体").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(
            parent,
            textvariable=self.font_var,
            values=FONT_CHOICES,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky=tk.EW, padx=(5, 12))
        ttk.Label(parent, text="大小").grid(row=0, column=2, sticky=tk.W)
        ttk.Spinbox(
            parent,
            textvariable=self.size_var,
            values=("9", "10.5", "12", "15", "16", "18", "22"),
            width=7,
            style="Property.TSpinbox",
        ).grid(row=0, column=3, sticky=tk.W, padx=(5, 10))
        ttk.Checkbutton(parent, text="加粗", variable=self.bold_var).grid(row=0, column=4, sticky=tk.W)
        ttk.Separator(parent).grid(row=1, column=0, columnspan=5, sticky=tk.EW, pady=7)
        ttk.Label(parent, text="批量范围").grid(row=2, column=0, sticky=tk.W)
        ttk.Combobox(
            parent,
            textvariable=self.batch_scope_var,
            values=BATCH_SCOPES,
            state="readonly",
            width=10,
        ).grid(row=2, column=1, sticky=tk.W, padx=(5, 8))
        ttk.Button(parent, text="应用到同类型", command=self._apply_same_type).grid(
            row=2, column=2, columnspan=3, sticky=tk.E
        )
        parent.columnconfigure(1, weight=1)

    def _build_paragraph_group(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="缩进", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=0, columnspan=6, sticky=tk.W, pady=(0, 5)
        )
        self._spin_row(parent, 1, "文本之前", self.left_indent_var, "字符", "文本之后", self.right_indent_var, "字符")
        ttk.Label(parent, text="特殊格式").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Combobox(
            parent,
            textvariable=self.special_indent_var,
            values=SPECIAL_INDENTS,
            state="readonly",
            width=8,
        ).grid(row=2, column=1, sticky=tk.W, padx=(5, 4), pady=(5, 0))
        ttk.Label(parent, text="度量值").grid(row=2, column=3, sticky=tk.W, padx=(12, 0), pady=(5, 0))
        ttk.Spinbox(parent, textvariable=self.special_indent_amount_var, from_=0, to=20, increment=0.1, width=7).grid(
            row=2, column=4, sticky=tk.W, padx=(5, 4), pady=(5, 0)
        )
        ttk.Label(parent, text="字符").grid(row=2, column=5, sticky=tk.W, pady=(5, 0))
        ttk.Separator(parent).grid(row=3, column=0, columnspan=6, sticky=tk.EW, pady=8)
        ttk.Label(parent, text="间距", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=4, column=0, columnspan=6, sticky=tk.W, pady=(0, 5)
        )
        self._spin_row(parent, 5, "段前", self.space_before_var, "磅", "段后", self.space_after_var, "磅")
        ttk.Label(parent, text="行距").grid(row=6, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Combobox(
            parent,
            textvariable=self.line_spacing_var,
            values=("1.0", "1.05", "1.25", "1.5", "2.0"),
            width=8,
        ).grid(row=6, column=1, sticky=tk.W, padx=(5, 4), pady=(5, 0))
        ttk.Label(parent, text="倍").grid(row=6, column=2, sticky=tk.W, pady=(5, 0))
        ttk.Label(parent, text="对齐").grid(row=6, column=3, sticky=tk.W, padx=(12, 0), pady=(5, 0))
        ttk.Combobox(
            parent,
            textvariable=self.alignment_var,
            values=ALIGNMENT_CHOICES,
            state="readonly",
            width=9,
        ).grid(row=6, column=4, columnspan=2, sticky=tk.W, padx=(5, 0), pady=(5, 0))

    @staticmethod
    def _spin_row(
        parent: ttk.Frame,
        row: int,
        label_a: str,
        variable_a: tk.StringVar,
        unit_a: str,
        label_b: str,
        variable_b: tk.StringVar,
        unit_b: str,
    ) -> None:
        ttk.Label(parent, text=label_a).grid(row=row, column=0, sticky=tk.W)
        ttk.Spinbox(parent, textvariable=variable_a, from_=0, to=40, increment=0.1, width=7).grid(
            row=row, column=1, sticky=tk.W, padx=(5, 4)
        )
        ttk.Label(parent, text=unit_a).grid(row=row, column=2, sticky=tk.W)
        ttk.Label(parent, text=label_b).grid(row=row, column=3, sticky=tk.W, padx=(12, 0))
        ttk.Spinbox(parent, textvariable=variable_b, from_=0, to=40, increment=0.1, width=7).grid(
            row=row, column=4, sticky=tk.W, padx=(5, 4)
        )
        ttk.Label(parent, text=unit_b).grid(row=row, column=5, sticky=tk.W)

    def _build_options_group(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="排列").grid(row=0, column=0, sticky=tk.W)
        self.option_combo = ttk.Combobox(
            parent,
            textvariable=self.option_layout_var,
            values=("四行单列", "两行两列"),
            state="readonly",
            width=10,
        )
        self.option_combo.grid(row=0, column=1, sticky=tk.W, padx=(5, 12))
        ttk.Label(parent, text="字体").grid(row=0, column=2, sticky=tk.W)
        ttk.Combobox(
            parent,
            textvariable=self.option_font_var,
            values=FONT_CHOICES,
            state="readonly",
            width=9,
        ).grid(row=0, column=3, sticky=tk.W, padx=(5, 0))
        self._spin_row(parent, 1, "文本之前", self.option_left_var, "字符", "悬挂", self.option_hanging_var, "字符")
        ttk.Label(parent, text="字号").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Spinbox(parent, textvariable=self.option_size_var, values=("9", "10.5", "12"), width=7).grid(
            row=2, column=1, sticky=tk.W, padx=(5, 4), pady=(5, 0)
        )

    def _build_pagination_group(self, parent: ttk.Frame) -> None:
        ttk.Checkbutton(parent, text="与下一段同页", variable=self.keep_next_var).pack(anchor=tk.W)
        ttk.Checkbutton(parent, text="当前对象前分页", variable=self.page_break_var).pack(anchor=tk.W, pady=(5, 0))

    def _build_global_group(self, parent: ttk.Frame) -> None:
        self._global_entry(parent, 0, "试卷名称", self.exam_name_var)
        self._global_entry(parent, 1, "科目名称", self.subject_name_var)
        self._global_entry(parent, 2, "试卷说明", self.exam_meta_var)
        self._global_entry(parent, 3, "目标页数", self.target_pages_var, width=8, hint="默认 8 页")
        ttk.Separator(parent).grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=8)
        ttk.Label(parent, text="页边距（毫米）", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=5, column=0, columnspan=3, sticky=tk.W, pady=(0, 5)
        )
        margins = ttk.Frame(parent)
        margins.grid(row=6, column=0, columnspan=3, sticky=tk.EW)
        for column, (label, variable) in enumerate(
            (("上", self.margin_top_var), ("下", self.margin_bottom_var), ("左", self.margin_left_var), ("右", self.margin_right_var))
        ):
            ttk.Label(margins, text=label).grid(row=0, column=column * 2, sticky=tk.W)
            ttk.Spinbox(margins, textvariable=variable, from_=5, to=45, increment=1, width=6).grid(
                row=0, column=column * 2 + 1, sticky=tk.W, padx=(3, 8)
            )
        ttk.Separator(parent).grid(row=7, column=0, columnspan=3, sticky=tk.EW, pady=8)
        ttk.Label(parent, textvariable=self.template_status_var, foreground="#4B5563", wraplength=390).grid(
            row=8, column=0, columnspan=3, sticky=tk.W
        )
        buttons = ttk.Frame(parent)
        buttons.grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
        ttk.Button(buttons, text="导入 Word 母版", command=self.import_template).pack(side=tk.LEFT)
        ttk.Button(buttons, text="使用默认预设", command=self.use_default_template).pack(side=tk.LEFT, padx=(6, 0))
        parent.columnconfigure(1, weight=1)

    @staticmethod
    def _global_entry(
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        width: int | None = None,
        hint: str = "",
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=1, sticky=tk.EW, padx=(7, 6), pady=3)
        if hint:
            ttk.Label(parent, text=hint, foreground="#6B7280").grid(row=row, column=2, sticky=tk.W, pady=3)

    def _build_preview_panel(self, parent: ttk.Frame) -> None:
        self.document_editor = ReadOnlyPreview(
            parent,
            on_select=lambda _key: None,
            on_change=lambda _key, _value, _old: None,
            on_inline_format=lambda _key, _start, _end, _spec: None,
            on_undo=lambda: self.undo_action(),
            on_redo=lambda: self.redo_action(),
            status_variable=self.page_status_var,
        )
        self.document_editor.pack(fill=tk.BOTH, expand=True)
        self.canvas = self.document_editor.canvas

    def _install_live_bindings(self) -> None:
        for variable in (self.kind_var, self.score_var):
            variable.trace_add("write", lambda *_args: self._identity_changed())
        for variable in (self.font_var, self.size_var, self.bold_var):
            variable.trace_add("write", lambda *_args: self._font_changed())
        for variable in (
            self.left_indent_var,
            self.right_indent_var,
            self.special_indent_var,
            self.special_indent_amount_var,
            self.alignment_var,
            self.line_spacing_var,
            self.space_before_var,
            self.space_after_var,
        ):
            variable.trace_add("write", lambda *_args: self._paragraph_changed())
        for variable in (
            self.option_layout_var,
            self.option_font_var,
            self.option_size_var,
            self.option_left_var,
            self.option_hanging_var,
        ):
            variable.trace_add("write", lambda *_args: self._options_changed())
        for variable in (self.keep_next_var, self.page_break_var):
            variable.trace_add("write", lambda *_args: self._paragraph_changed())
        for variable in (
            self.exam_name_var,
            self.subject_name_var,
            self.exam_meta_var,
            self.target_pages_var,
            self.margin_top_var,
            self.margin_bottom_var,
            self.margin_left_var,
            self.margin_right_var,
        ):
            variable.trace_add("write", lambda *_args: self._schedule_global_commit())
        self.search_var.trace_add("write", lambda *_args: self._schedule_search())
        self.stem_text.bind("<KeyRelease>", self._schedule_text_commit)
        self.stem_text.bind("<ButtonRelease-1>", lambda _event: self.after(20, self._selection_status), add="+")
        self.score_entry.bind("<Return>", self._commit_score_entry, add="+")
        self.score_entry.bind("<FocusOut>", self._commit_score_entry, add="+")

    def _sync_detail_scrollregion(self, _event: object | None = None) -> None:
        self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all"))

    def _on_detail_wheel(self, event: tk.Event) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        self.detail_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def _show_groups(self, names: set[str]) -> None:
        for group in self._all_groups:
            group.pack_forget()
        for group in self._all_groups:
            if group.title in names:
                group.pack(fill=tk.X, pady=(0, 6))
        self.after_idle(self._sync_detail_scrollregion)

    def _populate_tree(self) -> None:
        super()._populate_tree()
        roots = self.tree.get_children("") if hasattr(self, "tree") else ()
        if not roots:
            return
        root = roots[0]
        self.root_iid = root
        root_text = "答案信息" if self.raw_exam.get("document_kind") == "answer" else "整份试卷"
        self.tree.item(root, text=root_text, open=True)
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            kind = str(block.get("type", ""))
            if not kind.startswith("answer_"):
                continue
            label, category = self._answer_tree_label(block)
            iid = f"block-{index}"
            if not self.tree.exists(iid):
                self.tree.insert(
                    root,
                    tk.END,
                    iid=iid,
                    text=label,
                    values=(category, ""),
                )
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        for child in list(self.tree.get_children(root)):
            if not child.startswith("block-"):
                continue
            index = int(child.split("-", 1)[1])
            block = self.raw_exam.get("blocks", [])[index]
            if query and query not in self._block_search_text(block).lower():
                self.tree.detach(child)
        summary = calculate_score_summary(self.raw_exam)
        missing = set(summary.missing_questions)
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            iid = f"block-{index}"
            if not self.tree.exists(iid) or block.get("type") != "question":
                continue
            number = str(block.get("question", {}).get("number", "?"))
            self.tree.item(iid, tags=("warning",) if number in missing else ())
        self._refresh_score_display(summary)

    @staticmethod
    def _answer_tree_label(block: dict[str, Any]) -> tuple[str, str]:
        """Return the answer block label and its structure category."""

        kind = block.get("type")
        if kind == "answer_section":
            return str(block.get("text", "答案章节")), "答案章节"
        if kind == "answer_subsection":
            return f"{block.get('name', '')}{block.get('meta', '')}", "答案模块"
        if kind == "answer_question":
            return str(block.get("header", "答案题目")), "答案"
        if kind == "answer_table":
            return "作文等级评分表", "原表格"
        paragraphs = block.get("paragraphs", [])
        text = str(paragraphs[0].get("text", "")) if paragraphs else "答案说明"
        return text[:24], "答案说明"

    def _select_first_question(self) -> None:
        """Select the first question, or the first answer block for answer files."""

        for item in self.tree.get_children():
            for child in self.tree.get_children(item):
                index = int(child.split("-", 1)[1]) if child.startswith("block-") else -1
                if index >= 0 and self.raw_exam["blocks"][index].get("type") == "question":
                    self.tree.selection_set(child)
                    self.tree.focus(child)
                    self.tree.see(child)
                    self._on_tree_select(None)
                    return
        if self.tree.selection():
            return
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            if str(block.get("type", "")).startswith("answer_"):
                iid = f"block-{index}"
                if self.tree.exists(iid):
                    self.tree.selection_set(iid)
                    self.tree.focus(iid)
                    self._on_tree_select(None)
                    return

    def _block_label(self, block: dict[str, Any]) -> str:
        if str(block.get("type", "")).startswith("answer_"):
            return self._answer_tree_label(block)[0]
        return str(
            block.get("text")
            or block.get("name")
            or block.get("title")
            or {
                "material": "阅读材料",
                "poetry": "诗歌",
                "instruction": "阅读提示",
            }.get(block.get("type"), "结构内容")
        )

    def _block_edit_text(self, block: dict[str, Any]) -> str:
        kind = block.get("type")
        if kind == "answer_section":
            return str(block.get("text", ""))
        if kind == "answer_subsection":
            return f"{block.get('name', '')}{block.get('meta', '')}"
        if kind in {"answer_question", "answer_text"}:
            values: list[str] = []
            if block.get("header"):
                values.append(str(block["header"]))
            values.extend(str(entry.get("text", "")) for entry in block.get("paragraphs", []))
            return "\n".join(values)
        if kind == "answer_table":
            return "原答案中的作文等级评分表将保持表格结构。"
        block_type = block.get("type")
        if block_type in {"section_title", "instruction"}:
            return str(block.get("text", ""))
        if block_type == "subsection":
            return str(block.get("name", "")) + str(block.get("meta", ""))
        if block_type in {"material", "poetry"}:
            values = []
            for key in ("title", "author"):
                if block.get(key):
                    values.append(str(block[key]))
            values.extend(str(item) for item in block.get("paragraphs", []))
            for key in ("note", "source"):
                if block.get(key):
                    values.append(str(block[key]))
            return "\n".join(values)
        return ""

    def _commit_nonquestion_content(
        self,
        block: dict[str, Any],
        content: str,
    ) -> None:
        """Persist editable answer text while leaving answer tables intact."""

        kind = block.get("type")
        if kind == "answer_section":
            block["text"] = content
            return
        if kind == "answer_subsection":
            block["name"] = content
            block["meta"] = ""
            return
        if kind in {"answer_question", "answer_text"}:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if kind == "answer_question" and lines:
                block["header"] = lines.pop(0)
            old = list(block.get("paragraphs", []))
            paragraphs: list[dict[str, Any]] = []
            for index, text in enumerate(lines):
                role = (
                    str(old[index].get("role", "subjective_answer"))
                    if index < len(old)
                    else "subjective_answer"
                )
                paragraphs.append({"text": text, "role": role, "runs": []})
            block["paragraphs"] = paragraphs
            return
        if kind == "answer_table":
            return
        block_type = block.get("type")
        if block_type in {"section_title", "instruction"}:
            block["text"] = content
        elif block_type == "subsection":
            block["name"] = content
            block["meta"] = ""
        elif block_type in {"material", "poetry"}:
            block["paragraphs"] = [
                line.strip()
                for line in content.splitlines()
                if line.strip()
            ]
            for key in ("title", "author", "note", "source"):
                block[key] = ""

    def _load_nonquestion_fields(self, block: dict[str, Any]) -> None:
        """Load answer block defaults into the shared formatting inspector."""

        block_type = block.get("type")
        defaults = {
            "section_title": ("黑体", 12, 0, "左对齐"),
            "subsection": ("宋体", 10.5, 0, "左对齐"),
            "instruction": ("宋体", 10.5, 2, "左对齐"),
            "material": ("楷体", 10.5, 2, "左对齐"),
            "poetry": ("楷体", 10.5, 0, "居中"),
        }
        font, size, indent, alignment = defaults.get(
            str(block_type),
            ("宋体", 10.5, 0, "左对齐"),
        )
        spec = block.get("format", {})
        self.kind_var.set("结构内容")
        self.score_var.set("")
        self.option_layout_var.set("四行单列")
        self.stem_text.delete("1.0", tk.END)
        self.stem_text.insert("1.0", self._block_edit_text(block))
        self.font_var.set(str(spec.get("font", font)))
        self.size_var.set(str(spec.get("size_pt", size)))
        self.indent_var.set(str(spec.get("first_line_indent_chars", indent)))
        self.alignment_var.set(str(spec.get("alignment", alignment)))
        self.line_spacing_var.set(str(spec.get("line_spacing", 1.25)))
        self.space_before_var.set(str(spec.get("space_before_pt", 0)))
        self.space_after_var.set(str(spec.get("space_after_pt", 0)))
        self.keep_next_var.set(bool(spec.get("keep_with_next", False)))
        self.page_break_var.set(bool(spec.get("page_break_before", False)))
        self.option_font_var.set("宋体")
        self.option_size_var.set("10.5")
        self.option_left_var.set("1.5")
        self.option_hanging_var.set("1.7")
        lines = self._block_edit_text(block).splitlines()
        self.current_line_map = [
            {"target": "block", "target_index": index}
            for index in range(len(lines))
        ]
        self._render_inline_tags(block.get("inline_formats", []))
        defaults = {
            "answer_section": ("黑体", "12", "0", "左对齐"),
            "answer_subsection": ("宋体", "10.5", "2", "左对齐"),
            "answer_question": ("宋体", "10.5", "0", "左对齐"),
            "answer_text": ("楷体", "10.5", "2", "左对齐"),
            "answer_table": ("宋体", "10.5", "0", "居中"),
        }
        kind = block.get("type")
        if kind not in defaults:
            return
        font, size, indent, alignment = defaults[kind]
        spec = block.get("format", {})
        self.font_var.set(str(spec.get("font", font)))
        self.size_var.set(str(spec.get("size_pt", size)))
        self.indent_var.set(str(spec.get("first_line_indent_chars", indent)))
        self.alignment_var.set(str(spec.get("alignment", alignment)))
        self.line_spacing_var.set(str(spec.get("line_spacing", 1.25)))

    @staticmethod
    def _block_search_text(block: dict[str, Any]) -> str:
        values = [str(value) for value in block.values() if not isinstance(value, (dict, list))]
        question = block.get("question", {})
        if isinstance(question, dict):
            values.extend(str(question.get(key, "")) for key in ("number", "stem"))
            values.extend(str(value) for value in question.get("options", []))
        values.extend(str(value) for value in block.get("paragraphs", []))
        return " ".join(values)

    def _schedule_search(self) -> None:
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(180, self._run_search)

    def _run_search(self) -> None:
        self._search_after = None
        current = self.tree.selection()
        self._populate_tree()
        if current and self.tree.exists(current[0]):
            self.tree.selection_set(current[0])
            self.tree.see(current[0])

    def _set_tree_open(self, opened: bool) -> None:
        def visit(iid: str) -> None:
            self.tree.item(iid, open=opened)
            for child in self.tree.get_children(iid):
                visit(child)

        for root in self.tree.get_children(""):
            visit(root)

    def _populate_object_tree(self) -> None:
        self.object_tree.delete(*self.object_tree.get_children())
        for item in self._content_objects:
            self.object_tree.insert("", tk.END, iid=item.key, text=item.label, values=(summary_text(item),))

    def _on_content_object_select(self, _event: object | None) -> None:
        selection = self.object_tree.selection()
        if selection:
            self._select_object(str(selection[0]), navigate=True)

    def _load_content_object(self, item: ContentObject) -> None:
        spec = paragraph_format_for(self.raw_exam, item)
        self.loading_fields = True
        try:
            self.stem_text.delete("1.0", tk.END)
            self.stem_text.insert("1.0", item.text)
            self.current_line_map = [{"target": item.target, "target_index": item.target_index}]
            self.current_paragraph_formats = []
            owner = format_owner(self.raw_exam, item)
            if owner is not None:
                self.current_paragraph_formats = [dict(value) for value in owner.get("paragraph_formats", [])]
            self.font_var.set(str(spec.get("font", "宋体")))
            self.size_var.set(str(spec.get("size_pt", 10.5)))
            self.bold_var.set(bool(spec.get("bold", False)))
            self.left_indent_var.set(str(spec.get("left_indent_chars", 0)))
            self.right_indent_var.set(str(spec.get("right_indent_chars", 0)))
            self.special_indent_var.set(str(spec.get("special_indent", "无")))
            self.special_indent_amount_var.set(str(spec.get("special_indent_chars", 0)))
            first = float(spec.get("first_line_indent_chars", 0) or 0)
            self.indent_var.set(str(first))
            self.alignment_var.set(str(spec.get("alignment", "左对齐")))
            self.line_spacing_var.set(str(spec.get("line_spacing", 1.25)))
            self.space_before_var.set(str(spec.get("space_before_pt", 0)))
            self.space_after_var.set(str(spec.get("space_after_pt", 0)))
            self.keep_next_var.set(bool(spec.get("keep_with_next", False)))
            self.page_break_var.set(bool(spec.get("page_break_before", False)))
            self._load_option_controls()
            self._render_object_editor_style(item, spec)
        finally:
            self.loading_fields = False
        self.selection_hint_var.set(f"当前对象：{item.label}。未选择文字时，字体设置作用于整个对象。")

    def _load_question_fields(self, question: dict[str, Any]) -> None:
        """Load a question into the multi-line left editor."""

        super()._load_question_fields(question)
        lines, mapping = self._question_editor_lines(question)
        self.loading_fields = True
        try:
            self.stem_text.delete("1.0", tk.END)
            self.stem_text.insert("1.0", "\n".join(lines))
            self.current_line_map = mapping
            self._render_inline_tags(question.get("inline_formats", []))
        finally:
            self.loading_fields = False

    @staticmethod
    def _question_editor_lines(
        question: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Flatten editable question parts while retaining their targets."""

        lines = [str(question.get("stem", ""))]
        mapping = [{"target": "stem", "target_index": 0}]
        for index, text in enumerate(question.get("options", [])):
            lines.append(str(text))
            mapping.append({"target": "option", "target_index": index})
        for index, segments in enumerate(question.get("embedded_segments", [])):
            lines.append("".join(str(item.get("text", "")) for item in segments))
            mapping.append({"target": "embedded", "target_index": index})
        if question.get("segmentation_text"):
            lines.append(str(question["segmentation_text"]))
            mapping.append({"target": "segmentation", "target_index": 0})
        for index, text in enumerate(question.get("subquestions", [])):
            lines.append(str(text))
            mapping.append({"target": "subquestion", "target_index": index})
        for key in (
            "composition_material",
            "composition_prompt",
            "composition_requirements",
        ):
            for index, text in enumerate(question.get(key, [])):
                lines.append(str(text))
                mapping.append({"target": key, "target_index": index})
        return lines, mapping

    def _commit_question_lines(
        self,
        question: dict[str, Any],
        lines: list[str],
    ) -> None:
        """Persist flattened editor lines to their structured question fields."""

        for index, mapping in enumerate(self.current_line_map):
            if index >= len(lines):
                break
            value = lines[index]
            target = mapping["target"]
            target_index = int(mapping["target_index"])
            if target == "stem":
                question["stem"] = value
            elif target == "option":
                question["options"][target_index] = value
            elif target == "embedded":
                question["embedded_segments"][target_index] = [
                    {"text": value, "role": "body"}
                ]
            elif target == "segmentation":
                question["segmentation_text"] = value
            else:
                question[target][target_index] = value

    def _render_inline_tags(self, entries: list[dict[str, Any]]) -> None:
        """Restore character-level tags in the left editor."""

        for name in list(self.inline_tag_specs):
            self.stem_text.tag_delete(name)
        self.inline_tag_specs = {}
        for index, entry in enumerate(entries):
            line = int(entry.get("line", 0)) + 1
            name = f"inlinefmt_{index + 1}"
            spec = {
                "font": str(entry.get("font", "宋体")),
                "size_pt": float(entry.get("size_pt", 10.5)),
                "bold": bool(entry.get("bold", False)),
            }
            self.inline_tag_specs[name] = spec
            weight = "bold" if spec["bold"] else "normal"
            self.stem_text.tag_configure(
                name,
                font=(spec["font"], max(7, int(round(spec["size_pt"]))), weight),
            )
            self.stem_text.tag_add(
                name,
                f"{line}.{int(entry.get('start', 0))}",
                f"{line}.{int(entry.get('end', 0))}",
            )

    def _load_option_controls(self) -> None:
        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        question = block.get("question", {})
        spec = question.get("format", {})
        self.option_font_var.set(str(spec.get("option_font", "宋体")))
        self.option_size_var.set(str(spec.get("option_size_pt", 10.5)))
        self.option_left_var.set(str(spec.get("option_left_indent_chars", 1.5)))
        self.option_hanging_var.set(str(spec.get("option_hanging_indent_chars", 1.7)))

    def _render_object_editor_style(self, item: ContentObject, spec: dict[str, Any]) -> None:
        for tag in list(self.stem_text.tag_names()):
            if tag.startswith(("object_", "inlinefmt_", "semantic_")):
                self.stem_text.tag_delete(tag)
        font = str(spec.get("font", "宋体"))
        size = max(7, int(round(float(spec.get("size_pt", 10.5)))))
        weight = "bold" if spec.get("bold") else "normal"
        self.stem_text.configure(font=(font, size, weight))
        self.inline_tag_specs = {}
        for index, entry in enumerate(inline_formats_for(self.raw_exam, item)):
            name = f"object_inline_{index}"
            entry_font = str(entry.get("font", font))
            entry_size = max(7, int(round(float(entry.get("size_pt", size)))))
            entry_weight = "bold" if entry.get("bold") else "normal"
            self.stem_text.tag_configure(name, font=(entry_font, entry_size, entry_weight))
            self.stem_text.tag_add(name, f"1.0+{int(entry.get('start', 0))}c", f"1.0+{int(entry.get('end', 0))}c")
        if item.role == "segmentation":
            text = self.stem_text.get("1.0", "end-1c")
            for index, character in enumerate(text):
                if character in "ABCDEFGH":
                    name = f"object_marker_{index}"
                    self.stem_text.tag_configure(name, font=("宋体", size), relief=tk.SOLID, borderwidth=1)
                    self.stem_text.tag_add(name, f"1.0+{index}c", f"1.0+{index + 1}c")
        self.stem_text.tag_raise(tk.SEL)

    def _schedule_text_commit(self, _event: object | None = None) -> None:
        if self.loading_fields or self.selected_content_object is None:
            return
        if self._text_commit_after:
            self.after_cancel(self._text_commit_after)
        self.status_var.set("文字已修改，正在等待同步…")
        self._text_commit_after = self.after(600, self._commit_text_after_delay)

    def _commit_text_after_delay(self) -> None:
        self._text_commit_after = None
        self._commit_current_text(schedule_preview=True)

    def _commit_current_text(self, *, schedule_preview: bool) -> bool:
        item = self.selected_content_object
        if item is None or self.loading_fields:
            return False
        value = self.stem_text.get("1.0", "end-1c")
        if value == item.text:
            return False
        self._push_direct_history()
        set_content_object_text(self.raw_exam, item, value)
        updated = ContentObject(
            item.key,
            item.block_index,
            item.target,
            item.target_index,
            item.role,
            item.label,
            value,
            item.line_index,
        )
        self.selected_content_object = updated
        self._content_objects = [updated if current.key == item.key else current for current in self._content_objects]
        if self.object_tree.exists(item.key):
            self.object_tree.item(item.key, values=(summary_text(updated),))
        self._update_tree_block_label(item.block_index)
        self.status_var.set("内容已同步，正在更新预览。")
        if schedule_preview:
            self._schedule_canvas_preview()
        return True

    def _identity_changed(self) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        score_text = self.score_var.get().strip()
        if score_text and parse_score(score_text) is None:
            self.warning_var.set("当前分值格式需要检查，分值可留空后继续编辑。")
            return
        self._push_direct_history()
        question = block["question"]
        question["kind"] = "objective" if self.kind_var.get() == "客观题" else "subjective"
        question["score"] = None if not score_text else float(parse_score(score_text) or Decimal("0"))
        self._update_tree_block_label(self.selected_block_index)
        self._refresh_score_display()
        self._schedule_canvas_preview()

    def _font_changed(self) -> None:
        if self.loading_fields or self.selected_content_object is None:
            return
        try:
            font_spec = {
                "font": self.font_var.get(),
                "size_pt": float(self.size_var.get()),
                "bold": self.bold_var.get(),
            }
        except ValueError:
            return
        self._push_direct_history()
        ranges = self.stem_text.tag_ranges(tk.SEL)
        if ranges:
            start = int(self.stem_text.count("1.0", ranges[0], "chars")[0])
            end = int(self.stem_text.count("1.0", ranges[1], "chars")[0])
            set_inline_format(self.raw_exam, self.selected_content_object, start, end, font_spec)
            self.selection_hint_var.set("字体已应用到选中文字。")
        else:
            spec = self._format_spec_from_controls()
            set_paragraph_format(self.raw_exam, self.selected_content_object, spec)
            self.selection_hint_var.set("字体已应用到当前内容对象。")
        self._render_object_editor_style(
            self.selected_content_object,
            paragraph_format_for(self.raw_exam, self.selected_content_object),
        )
        self._schedule_canvas_preview()

    def _paragraph_changed(self) -> None:
        if self.loading_fields or self.selected_content_object is None:
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError:
            return
        self._push_direct_history()
        set_paragraph_format(self.raw_exam, self.selected_content_object, spec)
        self._schedule_canvas_preview()

    def _format_spec_from_controls(self) -> dict[str, Any]:
        special = self.special_indent_var.get()
        amount = float(self.special_indent_amount_var.get())
        first = amount if special == "首行" else (-amount if special == "悬挂" else 0.0)
        return {
            "font": self.font_var.get(),
            "size_pt": float(self.size_var.get()),
            "bold": self.bold_var.get(),
            "left_indent_chars": float(self.left_indent_var.get()),
            "right_indent_chars": float(self.right_indent_var.get()),
            "special_indent": special,
            "special_indent_chars": amount,
            "first_line_indent_chars": first,
            "alignment": self.alignment_var.get(),
            "line_spacing": float(self.line_spacing_var.get()),
            "space_before_pt": float(self.space_before_var.get()),
            "space_after_pt": float(self.space_after_var.get()),
            "keep_with_next": self.keep_next_var.get(),
            "page_break_before": self.page_break_var.get(),
        }

    def _options_changed(self) -> None:
        if self.loading_fields or self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        try:
            size = float(self.option_size_var.get())
            left = float(self.option_left_var.get())
            hanging = float(self.option_hanging_var.get())
        except ValueError:
            return
        self._push_direct_history()
        question = block["question"]
        question["option_layout"] = "two_column" if self.option_layout_var.get() == "两行两列" else "vertical"
        question.setdefault("format", {}).update(
            {
                "option_font": self.option_font_var.get(),
                "option_size_pt": size,
                "option_left_indent_chars": left,
                "option_hanging_indent_chars": hanging,
            }
        )
        self._schedule_canvas_preview()

    def _apply_same_type(self) -> None:
        item = self.selected_content_object
        if item is None:
            return
        try:
            spec = self._format_spec_from_controls()
        except ValueError:
            messagebox.showerror(APP_TITLE, "格式参数需要填写数字。", parent=self)
            return
        targets = objects_in_scope(self.raw_exam, item, self.batch_scope_var.get())
        self._push_direct_history()
        for target in targets:
            set_paragraph_format(self.raw_exam, target, spec)
        self.status_var.set(f"已将格式应用到 {len(targets)} 个同类型内容对象。")
        self._schedule_canvas_preview()

    def _schedule_global_commit(self) -> None:
        if self.loading_fields:
            return
        if self._global_commit_after:
            self.after_cancel(self._global_commit_after)
        self._global_commit_after = self.after(600, lambda: self._commit_global_settings(show_error=False))

    def _commit_global_settings(self, *, show_error: bool) -> bool:
        self._global_commit_after = None
        try:
            target_pages = int(self.target_pages_var.get().strip())
            margins = {
                "margin_top_mm": float(self.margin_top_var.get()),
                "margin_bottom_mm": float(self.margin_bottom_var.get()),
                "margin_left_mm": float(self.margin_left_var.get()),
                "margin_right_mm": float(self.margin_right_var.get()),
            }
            if not 1 <= target_pages <= 32:
                raise ValueError("目标页数应在 1 到 32 页之间。")
            if any(not 5 <= value <= 45 for value in margins.values()):
                raise ValueError("页边距应在 5 到 45 毫米之间。")
        except ValueError as exc:
            if show_error:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return False
        metadata = self.raw_exam.setdefault("metadata", {})
        metadata["exam_name"] = self.exam_name_var.get().strip()
        metadata["subject_name"] = self.subject_name_var.get().strip()
        metadata["meta_text"] = self.exam_meta_var.get().strip()
        metadata["total_score"] = float(TARGET_SCORE)
        metadata["target_pages"] = target_pages
        metadata["page_overrides"] = margins
        self.status_var.set("整卷设置已同步。")
        self._schedule_canvas_preview()
        return True

    def apply_global_settings(self) -> None:
        self._commit_global_settings(show_error=True)

    def _load_global_fields(self) -> None:
        super()._load_global_fields()
        metadata = self.raw_exam.get("metadata", {})
        page = load_layout(self.layout_path).get("page", {})
        overrides = metadata.get("page_overrides", {})
        self.loading_fields = True
        try:
            self.target_pages_var.set(str(metadata.get("target_pages", 8)))
            self.margin_top_var.set(str(overrides.get("margin_top_mm", page.get("margin_top_mm", 20))))
            self.margin_bottom_var.set(str(overrides.get("margin_bottom_mm", page.get("margin_bottom_mm", 18))))
            self.margin_left_var.set(str(overrides.get("margin_left_mm", page.get("margin_left_mm", 22))))
            self.margin_right_var.set(str(overrides.get("margin_right_mm", page.get("margin_right_mm", 18))))
        finally:
            self.loading_fields = False

    def apply_current_question(self, *, silent: bool = False) -> bool:
        self._commit_current_text(schedule_preview=False)
        if self.selected_block_index is None:
            return True
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return True
        score_text = self.score_var.get().strip()
        if score_text and parse_score(score_text) is None:
            if not silent:
                messagebox.showerror(APP_TITLE, "分值需要填写非负数字，也可以暂时留空。", parent=self)
            return False
        question = block["question"]
        question["kind"] = "objective" if self.kind_var.get() == "客观题" else "subjective"
        question["score"] = None if not score_text else float(parse_score(score_text) or Decimal("0"))
        question["option_layout"] = "two_column" if self.option_layout_var.get() == "两行两列" else "vertical"
        self.raw_exam.setdefault("metadata", {})["total_score"] = float(TARGET_SCORE)
        self._refresh_score_display()
        return True

    def _commit_score_entry(self, _event: object | None = None) -> str:
        if self.score_var.get().strip() and parse_score(self.score_var.get()) is None:
            self.warning_var.set("当前题分值格式需要检查，分值可留空后继续编辑。")
            return "break"
        self.apply_current_question(silent=True)
        self._populate_tree()
        self._schedule_canvas_preview()
        return "break"

    def _update_tree_block_label(self, block_index: int | None) -> None:
        if block_index is None:
            return
        iid = f"block-{block_index}"
        if not self.tree.exists(iid):
            return
        block = self.raw_exam["blocks"][block_index]
        if block.get("type") == "question":
            question = block["question"]
            stem = str(question.get("stem", ""))
            kind = "客观" if question.get("kind") == "objective" else "主观"
            score = question.get("score")
            self.tree.item(
                iid,
                text=f"{question.get('number')}．{stem[:18]}",
                values=(kind, "" if score is None else score),
            )

    def _refresh_score_display(self, summary: ScoreSummary | None = None) -> None:
        if not hasattr(self, "score_total_label"):
            return
        state = summary or calculate_score_summary(self.raw_exam)
        self.score_total_label.configure(text=f"当前 {format_score(state.total)} / 150 分")
        delta = self._score_difference_text(state)
        if state.missing_questions:
            delta += f"，{len(state.missing_questions)} 题待填"
        color = "#107C10" if state.complete else ("#C42B1C" if state.difference < 0 else "#8A5700")
        self.score_total_label.configure(foreground=color)
        self.score_delta_label.configure(text=delta, foreground=color)
        self._refresh_warning_banner(state)

    def _refresh_warning_banner(self, summary: ScoreSummary) -> None:
        if self._warning_refreshing or not hasattr(self, "warning_var"):
            return
        self._warning_refreshing = True
        try:
            warnings: list[str] = []
            if summary.missing_questions:
                warnings.append("待填写分值：" + "、".join(summary.missing_questions))
            question_count = sum(1 for block in self.raw_exam.get("blocks", []) if block.get("type") == "question")
            if self.raw_exam.get("document_kind") != "answer" and question_count != 23:
                warnings.append(f"当前识别 {question_count} 道题，仍可继续编辑和导出")
            diagnostics = self.raw_exam.get("diagnostics", [])
            if diagnostics:
                warnings.append(f"另有 {len(diagnostics)} 条识别提示")
            self.warning_var.set("；".join(warnings) if warnings else "结构与分值检查暂无提醒")
        finally:
            self._warning_refreshing = False

    def _schedule_canvas_preview(self) -> None:
        if self._canvas_preview_after:
            self.after_cancel(self._canvas_preview_after)
        self._canvas_preview_after = self.after(260, self._run_canvas_preview)

    def _finish_preview(self, result: object) -> None:
        if isinstance(result, Exception):
            self._finish_task_error(result)
            return
        if result.generation != self._preview_generation:
            return
        self.busy = False
        self.busy_bar.stop()
        object_locators = build_object_locators(self.raw_exam, result.locators)
        self._preview_block_locators = dict(result.locators)
        self.preview_pages = list(result.pages)
        self.preview_page_index = min(self.preview_page_index, max(0, len(self.preview_pages) - 1))
        selected_key: object = self.selected_block_index
        if self.selected_content_object is not None:
            selected_key = self.selected_content_object.key
        self.document_editor.set_preview_pages(
            self.preview_pages,
            locators=object_locators,
            actual_pages=result.actual_pages,
            raw_exam=self.raw_exam,
            selected_key=selected_key,
        )
        self.status_var.set(f"预览已更新：实际 {result.actual_pages} 页，目标 {result.target_pages} 页")
        if self.selected_content_object is not None:
            self.after(50, lambda: self.document_editor.scroll_to(self.selected_content_object.key, highlight=False))

    def _jump_to_block(self, block_index: int) -> None:
        key: object = block_index
        if self.selected_content_object is not None and self.selected_content_object.block_index == block_index:
            key = self.selected_content_object.key
        if hasattr(self, "document_editor"):
            self._pending_preview_block = None
            self.document_editor.scroll_to(key, highlight=True)

    def _render_editable_document(self) -> None:
        return

    def _apply_editor_visual_styles(self) -> None:
        return

    def _cursor_style_event(self, _event: tk.Event) -> None:
        self.after(20, self._selection_status)

    def _selection_status(self) -> None:
        if self.stem_text.tag_ranges(tk.SEL):
            self.selection_hint_var.set("已选择文字，字体、字号和加粗只作用于选中文字。")
        elif self.selected_content_object is not None:
            self.selection_hint_var.set(f"当前对象：{self.selected_content_object.label}。段落设置作用于整个对象。")

    def undo_action(self, _event: object | None = None) -> str:
        if not self.undo_stack:
            return "break"
        if self.history_transaction_open:
            self.apply_current_question(silent=True)
        self.redo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.undo_stack.pop()
        self.history_transaction_open = False
        self.selection_dirty = False
        self._restore_selection()
        self.request_preview()
        return "break"

    def redo_action(self, _event: object | None = None) -> str:
        if not self.redo_stack:
            return "break"
        self.undo_stack.append(deepcopy(self.raw_exam))
        self.raw_exam = self.redo_stack.pop()
        self.history_transaction_open = False
        self.selection_dirty = False
        self._restore_selection()
        self.request_preview()
        return "break"

    def _restore_selection(self) -> None:
        key = self.selected_content_object.key if self.selected_content_object else None
        if self.selected_block_index is not None and self.tree.exists(f"block-{self.selected_block_index}"):
            self.tree.selection_set(f"block-{self.selected_block_index}")
            self._on_tree_select(None)
            if key and self.object_tree.exists(key):
                self._select_object(key, navigate=False)
        self._refresh_score_display()

    def default_export_basename(self) -> str:
        """Use the imported Word filename as the export dialog basename."""

        metadata = self.raw_exam.get("metadata", {})
        fallback = str(metadata.get("exam_name", "语文试卷"))
        return default_export_basename(self.current_exam_path, fallback)

    def save_project(self) -> None:
        """Save the editable structured draft for later continuation."""

        self.apply_current_question(silent=True)
        path = filedialog.asksaveasfilename(
            title="保存结构化项目",
            defaultextension=".json",
            filetypes=[("结构化试题", "*.json")],
            initialfile="exam.json",
        )
        if path:
            save_exam(self.raw_exam, path)
            self.current_exam_path = Path(path)
            self.status_var.set(f"项目已保存：{path}")

    def import_new_exam(self) -> None:
        """Open the Word-only import dialog."""

        path = filedialog.askopenfilename(
            title="导入 Word 试题",
            filetypes=[
                ("Word 文档", "*.docx *.doc"),
                ("DOCX 文档", "*.docx"),
                ("旧版 DOC 文档", "*.doc"),
            ],
        )
        if path:
            self._import_exam_path(Path(path))

    def _handle_dropped_files(self, paths: tuple[Path, ...]) -> None:
        """Accept the first dropped DOCX or DOC document."""

        supported = [path for path in paths if path.suffix.lower() in {".docx", ".doc"}]
        if not supported:
            messagebox.showwarning(APP_TITLE, "请拖入 .docx 或 .doc 文件。", parent=self)
            return
        self._import_exam_path(supported[0])

    def _import_exam_path(self, path: Path) -> None:
        try:
            self.raw_exam = import_exam(path)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        self.current_exam_path = path
        self.selected_block_index = None
        if hasattr(self, "undo_stack"):
            self.undo_stack.clear()
            self.redo_stack.clear()
        self._populate_tree()
        self._load_global_fields()
        diagnostics = self.raw_exam.get("diagnostics", [])
        suffix = f"，识别提示 {len(diagnostics)} 条" if diagnostics else ""
        self.status_var.set(f"已导入：{path.name}{suffix}")
        self.after(100, self._select_first_question)
        self.request_preview()
        self.selected_content_object = None
        self._refresh_score_display()

    def open_export_dialog(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "请等待当前预览任务完成。", parent=self)
            return
        self.apply_current_question(silent=True)
        summary = calculate_score_summary(self.raw_exam)
        lines: list[str] = []
        if summary.missing_questions:
            lines.append("未填写分值：" + "、".join(summary.missing_questions))
        if summary.total != TARGET_SCORE:
            lines.append(self._score_difference_text(summary))
        question_count = sum(1 for block in self.raw_exam.get("blocks", []) if block.get("type") == "question")
        if self.raw_exam.get("document_kind") != "answer" and question_count != 23:
            lines.append(f"当前识别 {question_count} 道题，与默认模板题量不同")
        diagnostics = self.raw_exam.get("diagnostics", [])
        if diagnostics:
            lines.append(f"存在 {len(diagnostics)} 条识别提示")
        if lines:
            dialog = ExportWarningDialog(self, lines)
            self.wait_window(dialog)
            if not dialog.result:
                return
        initial = with_export_suffix(self.default_export_basename())
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 Word 文档",
            defaultextension=".docx",
            filetypes=[("Word 文档（DOCX）", "*.docx")],
            initialfile=f"{initial}.docx",
        )
        if not path:
            return
        target = Path(path)
        basename = target.stem
        if basename.endswith("（排版）"):
            basename = basename[:-4]
        self.start_export(target.parent, basename, True, False)

    def _save_editor_sash(self, _event: object | None = None) -> None:
        if not hasattr(self, "editor_pane"):
            return
        try:
            position = int(self.editor_pane.sashpos(0))
        except tk.TclError:
            return
        settings = self._read_ui_settings()
        settings["left_splitter"] = position
        path = self._ui_settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    @staticmethod
    def _ui_settings_path() -> Path:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return root / "SuoyiExamTypesetter" / "ui.json"

    def _read_ui_settings(self) -> dict[str, Any]:
        path = self._ui_settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # Current release behavior is kept in the formal desktop entry point. These
        # Keep all current workbench behavior in this authoritative entry module.
    # wrapper layer around the actual workbench.
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
        if not self.selection_dirty:
            self.selected_block_index = None
        self.apply_current_question(silent=True)
        selection = self.tree.selection()
        if not selection:
            return
        iid = str(selection[0])
        if not iid.startswith("block-"):
            self.selected_block_index = None
            return
        index = int(iid.split("-", 1)[1])
        block = self.raw_exam["blocks"][index]
        self.selected_block_index = index
        self.selected_block_type = str(block.get("type", ""))
        self.loading_fields = True
        try:
            if block.get("type") == "question":
                question = block["question"]
                self.selection_title_var.set(f"第 {question['number']} 题")
                self._load_question_fields(question)
            else:
                self.selection_title_var.set(self._block_label(block))
                self._load_nonquestion_fields(block)
            self._load_right_text(block)
        finally:
            self.loading_fields = False
        self.selection_dirty = False
        if selection and str(selection[0]).startswith("block-"):
            index = int(str(selection[0]).split("-", 1)[1])
            self._pending_preview_block = index
            self._apply_editor_visual_styles()
            self._jump_to_block(index)
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

    def _load_right_text(self, _block: dict[str, Any]) -> None:
        """Compatibility hook for the retired mirrored-text editor."""

        return

    def _restore_group(self, group: str) -> None:
        if group == "global":
            self._restore_global_group()
            return
        if group == "options":
            self._restore_options_group()
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

    def _restore_global_group(self) -> None:
        """Restore pagination defaults without replacing imported exam text."""

        self._push_direct_history()
        if self._global_commit_after:
            self.after_cancel(self._global_commit_after)
            self._global_commit_after = None
        page = load_layout(self.layout_path).get("page", {})
        margins = {
            "margin_top_mm": float(page.get("margin_top_mm", 20)),
            "margin_bottom_mm": float(page.get("margin_bottom_mm", 18)),
            "margin_left_mm": float(page.get("margin_left_mm", 22)),
            "margin_right_mm": float(page.get("margin_right_mm", 18)),
        }
        metadata = self.raw_exam.setdefault("metadata", {})
        metadata["target_pages"] = 8
        metadata["page_overrides"] = margins
        self.loading_fields = True
        try:
            self.target_pages_var.set("8")
            self.margin_top_var.set(str(margins["margin_top_mm"]).rstrip("0").rstrip("."))
            self.margin_bottom_var.set(str(margins["margin_bottom_mm"]).rstrip("0").rstrip("."))
            self.margin_left_var.set(str(margins["margin_left_mm"]).rstrip("0").rstrip("."))
            self.margin_right_var.set(str(margins["margin_right_mm"]).rstrip("0").rstrip("."))
        finally:
            self.loading_fields = False
        self.status_var.set("已恢复目标页数和页边距模板值。")
        self._schedule_canvas_preview()

    def _restore_options_group(self) -> None:
        """Restore the confirmed choice-option defaults for the selected question."""

        if self.selected_block_index is None:
            return
        block = self.raw_exam["blocks"][self.selected_block_index]
        if block.get("type") != "question":
            return
        self._push_direct_history()
        question = block["question"]
        question["option_layout"] = "vertical"
        question.setdefault("format", {}).update(
            {
                "option_font": "宋体",
                "option_size_pt": 10.5,
                "option_left_indent_chars": 1.5,
                "option_hanging_indent_chars": 1.7,
            }
        )
        self.loading_fields = True
        try:
            self.option_layout_var.set("四行单列")
            self.option_font_var.set("宋体")
            self.option_size_var.set("10.5")
            self.option_left_var.set("1.5")
            self.option_hanging_var.set("1.7")
        finally:
            self.loading_fields = False
        self.status_var.set("已恢复选择项模板值。")
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
        help_menu.add_command(label="检查更新", command=self.check_for_updates)
        help_menu.add_separator()
        help_menu.add_command(label="关于与使用许可", command=self.show_about)
        help_menu.add_separator()
        help_menu.add_command(label="GitHub 更新页面", command=self.open_github_update_page)
        help_menu.add_command(
            label="打开 GitHub 仓库",
            command=lambda: __import__("webbrowser").open(GITHUB_REPOSITORY),
        )
        menu.add_cascade(label="帮助", menu=help_menu)

    def show_about(self) -> None:
        """Show the software attribution and current version."""

        messagebox.showinfo(
            "关于与使用许可",
            "高中语文试卷智能排版工作台\n\n"
            "本软件为本人（公众号：蓑衣微言）为高中语文试题排版而做。\n\n"
            "未经本人书面许可，不得销售、出租、收费分发、嵌入商业服务或用于其他营利活动。\n\n"
            f"当前版本：{APP_VERSION}",
            parent=self,
        )

    def check_for_updates(self) -> None:
        """Check GitHub for a newer release without blocking the UI."""

        self.status_var.set("正在连接 GitHub 检查更新……")
        self.busy_bar.start(10)

        def worker() -> None:
            try:
                info = check_latest_release(APP_VERSION)
                self._update_messages.put(("checked", info))
            except Exception as exc:  # noqa: BLE001
                self._update_messages.put(("update_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_update_messages)

    def _poll_update_messages(self) -> None:
        try:
            kind, payload = self._update_messages.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_update_messages)
            return
        if kind == "update_error":
            self.busy_bar.stop()
            self.status_var.set("检查更新失败。")
            messagebox.showerror(APP_TITLE, str(payload), parent=self)
            return
        if kind == "checked":
            self.busy_bar.stop()
            info = payload
            if not info.newer:
                self.status_var.set("当前已是最新版。")
                messagebox.showinfo(
                    APP_TITLE,
                    f"当前版本 {APP_VERSION} 已是最新版。",
                    parent=self,
                )
                return
            install = messagebox.askyesno(
                "发现新版本",
                f"发现版本 {info.version}\n\n{info.name}\n\n"
                f"更新包：{info.asset_name}\n"
                f"大小：{info.asset_size / 1024 / 1024:.1f} MB\n\n"
                "是否下载并安装？",
                parent=self,
            )
            if install:
                self._download_update(info)
            return
        if kind == "downloaded":
            self.busy_bar.stop()
            self.status_var.set("更新包已下载，准备安装。")
            try:
                schedule_portable_update(Path(payload))
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(APP_TITLE, str(exc), parent=self)
                return
            messagebox.showinfo(
                "准备更新",
                "软件将关闭并在后台完成更新，更新后会自动重新打开。",
                parent=self,
            )
            self.after(200, self.destroy)

    def _download_update(self, info: object) -> None:
        """Download the selected portable release in a worker thread."""

        self.status_var.set("正在下载 GitHub 更新包……")
        self.busy_bar.start(10)

        def worker() -> None:
            try:
                path = download_release_asset(info)
                self._update_messages.put(("downloaded", path))
            except Exception as exc:  # noqa: BLE001
                self._update_messages.put(("update_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_update_messages)

    def open_github_update_page(self) -> None:
        GitHubUpdatePage(self, APP_VERSION)

    def _activate_file_drop(self) -> None:
        uninstall_file_drop(self)
        self.update_idletasks()
        self.drop_enabled = install_file_drop(self, self._handle_dropped_files)
        if not self.drop_enabled:
            self.drop_hint.configure(text="拖放注册失败，请使用“导入试题”按钮")

    def _on_close(self) -> None:
        uninstall_file_drop(self)
        self._preview_service.close()
        self._export_executor.shutdown(wait=True, cancel_futures=True)
        super()._on_close()


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "import_exam", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
