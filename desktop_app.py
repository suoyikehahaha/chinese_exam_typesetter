"""Current contextual inspector with a stable read-only page preview."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
import app.inspector_model as inspector_model
from app.config import load_layout
from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.export_naming import default_export_basename
from app.drafts import DraftRecovery
from app.github_updater import (
    check_latest_release,
    download_release_asset,
    schedule_portable_update,
)
from app.github_update_page import GITHUB_REPOSITORY, GitHubUpdatePage
from app.inspector_model import (
    ContentObject,
    content_objects_for_block,
    format_owner,
    inline_formats_for,
    metadata_content_objects,
    paragraph_format_for,
    summary_text,
)
from app.layout_presets import DEFAULT_PRESET, PRESET_LABELS, preset_page
from app.page_target import use_natural_pagination
from app.page_settings import DocumentSettingsPanel, build_page_settings
from app.preview_service import PreviewService
from app.review_items import ReviewPanel, review_items
from app.read_only_preview import ReadOnlyPreview
from app.score_summary import ScoreSummary, calculate_score_summary, format_score, parse_score
from app.windows_drop import install_file_drop, uninstall_file_drop
from app.version import APP_TITLE, APP_VERSION
from app.windows_style import apply_windows_style
from app.workbench_state import draft_signature, suggested_export_path
from app.workbench_session import WorkbenchSession
from app.workbench_editing import WorkbenchEditing
from app.workbench_tasks import WorkbenchTasks
from app.workbench_ui import apply_workbench_styles, build_workbench_shell
from desktop_workbench_base import DesktopApp as LegacyWorkbench


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


class CurrentDesktopApp(WorkbenchSession, WorkbenchTasks, WorkbenchEditing, LegacyWorkbench):
    """Context-sensitive left inspector paired with a read-only A4 preview."""

    def __init__(self, *, check_updates: bool = True) -> None:
        self._closing = False
        self._ui_messages: queue.Queue = queue.Queue()
        self._preview_ticket = 0
        self._session_generation = 0
        self._revision = 0
        self._proof_requested = False
        self._requested_revision = None
        self._displayed_revision = None
        self._import_in_progress = False
        self._export_in_progress = False
        self._import_future = None
        self._import_generation = 0
        self._pending_update = None
        self._locked_controls = []
        self._autosave_after = None
        self._preview_warnings = ()
        self._recovery_claim = None
        self._recovered_path = None
        self._recovery = DraftRecovery(self._ui_settings_path().parent / "recovery")
        self._saved_signature = ""
        self._global_invalid = False
        self._settings_feedback: dict[str, Any] | None = None
        self._preview_settings_feedback: dict[str, Any] | None = None
        self._project_path: Path | None = None
        self._last_export_path: Path | None = None
        self._export_signature = ""
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
        self._update_in_progress = False
        self._update_automatic = False
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
        super().__init__(legacy_tasks=False)
        width = min(1500, self.winfo_screenwidth() - 60)
        height = min(940, self.winfo_screenheight() - 100)
        self.geometry(f"{width}x{height}")
        self.minsize(1060, 650)
        self.configure(background="#F4F7FB")
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
        # Drag-and-drop remains available; no extra footer consumes editing space.
        self.after_idle(self._restore_editor_sash)
        self.after_idle(self._activate_file_drop)
        self._install_live_bindings()
        self._saved_signature = draft_signature(self.raw_exam)
        self.tree.selection_set(self.root_iid)
        self._on_tree_select(None)
        self.after(60, self._poll_ui_messages)
        self.stem_text.bind("<ButtonRelease-1>", self._cursor_style_event, add="+")
        self.status_var.set("内置示例已就绪。导入 Word 开始排版，修改会自动同步到预览。")
        self._startup_preview_pending = True
        self.after(300, self._release_startup_preview)
        if check_updates:
            self.after(450, lambda: self.recover_draft(automatic=True))
            self.after(6000, lambda: self.check_for_updates(automatic=True))
        self._set_application_icon()
        for shortcut, command in (("<Control-o>", self.import_new_exam), ("<Control-s>", self.save_project),
                                  ("<Control-e>", self.open_export_dialog), ("<Control-f>", self.focus_search),
                                  ("<F5>", self.request_preview)):
            self.bind(shortcut, lambda _event, action=command: (action(), "break")[-1])
        self.bind("<Control-Shift-S>", lambda _e: (self.save_project(save_as=True), "break")[-1])
        self.bind("<Control-z>", self.undo_action)
        self.bind("<Control-y>", self.redo_action)

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
                    font=("Microsoft YaHei UI", 11),
                )
            elif isinstance(child, tk.Canvas):
                child.configure(background="#EDF2F8", highlightthickness=0)
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
        build_workbench_shell(self)

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

    @staticmethod
    def _score_difference_text(summary: ScoreSummary) -> str:
        """Describe the distance between entered scores and the 150-point target."""

        if summary.difference > 0:
            return f"还差 {format_score(summary.difference)} 分"
        if summary.difference < 0:
            return f"超出 {format_score(-summary.difference)} 分"
        return f"已录入合计 {format_score(summary.total)} 分"

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

    def _create_variables(self) -> None:
        super()._create_variables()
        self.layout_preset_var = tk.StringVar(master=self, value=PRESET_LABELS[DEFAULT_PRESET])
        self.search_var = tk.StringVar()
        self.warning_var = tk.StringVar()
        self.format_error_var = tk.StringVar()
        self._invalid_option = False
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
        apply_workbench_styles(self)

    def _build_local_format_controls(self) -> None:
        """The inspector owns selection-aware formatting controls."""

        return

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        top_actions = ttk.Frame(parent)
        top_actions.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(top_actions, text="试卷结构", style="Title.TLabel").pack(side=tk.LEFT)
        self.redo_button = ttk.Button(top_actions, text="重做", command=self.redo_action, state="disabled")
        self.redo_button.pack(side=tk.RIGHT)
        self.undo_button = ttk.Button(top_actions, text="撤销", command=self.undo_action, state="disabled")
        self.undo_button.pack(side=tk.RIGHT, padx=(0, 6))

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
        self.warning_banner.pack(fill=tk.X, pady=(0, 2))
        self.review_panel = ReviewPanel(parent, self._go_to_issue)
        self.review_panel.pack(fill=tk.X, pady=(0, 5))

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
        self.search_entry = search_entry
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(search_row, text="清除", command=lambda: self.search_var.set("")).pack(side=tk.LEFT)
        search_entry.bind("<Escape>", lambda _event: self.search_var.set(""))

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
        self.tree.column("kind", width=54, minwidth=45, stretch=False, anchor=tk.CENTER)
        self.tree.column("score", width=44, minwidth=40, stretch=False, anchor=tk.CENTER)
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
        self.inspector_tabs = ttk.Notebook(inspector_panel)
        self.inspector_tabs.pack(fill=tk.X, pady=(0, 6))
        for label in ("内容", "格式", "整卷"):
            self.inspector_tabs.add(ttk.Frame(self.inspector_tabs, height=1), text=label)
        self.inspector_tabs.bind("<<NotebookTabChanged>>", self._inspector_tab_changed)
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
        self.global_group = DocumentSettingsPanel(self.detail_frame)
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
        for panel in (self.font_group.body, self.paragraph_group.body, self.options_group.body):
            ttk.Label(panel, textvariable=self.format_error_var, foreground="#9D2D18").grid(
                row=panel.grid_size()[1], column=0, columnspan=4, sticky=tk.W)
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
            height=3,
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
            undo=False,
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
        build_page_settings(self, parent, CollapsibleGroup)

    def _scope_changed(self, _event=None) -> None:
        self._commit_global_settings(show_error=True)
        self._refresh_score_display()

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
            status_variable=self.page_status_var, on_print_proof=self.toggle_print_proof,
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
            self.margin_top_var,
            self.margin_bottom_var,
            self.margin_left_var,
            self.margin_right_var,
        ):
            variable.trace_add("write", lambda *_args: self._schedule_global_commit())
        self.search_var.trace_add("write", lambda *_args: self._schedule_search())
        self.stem_text.edit_modified(False)
        self.stem_text.bind("<<Modified>>", self._editor_text_modified, add="+")
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

    def _inspector_tab_changed(self, _event=None) -> None:
        if not hasattr(self, "_all_groups"):
            return
        selected = self.inspector_tabs.index(self.inspector_tabs.select())
        if selected != 0:
            self._commit_current_text(schedule_preview=True)
        header_selected = self.selected_content_object is None or self.selected_content_object.block_index is None
        self.inspector_tabs.tab(1, state="disabled" if header_selected else "normal")
        if selected == 1 and header_selected:
            self.inspector_tabs.select(0)
            selected = 0
        groups = {0: {"内容与题型"}, 1: {"字体", "段落", "分页"}, 2: {"整卷设置"}}[selected]
        if selected == 1:
            item = self.selected_content_object
            if item is None or item.block_index is None:
                groups = set()
            elif self.raw_exam["blocks"][item.block_index].get("type") == "question":
                groups.add("选择项")
        self._show_groups(groups)
        if selected == 2:
            self.selection_title_var.set("整卷设置 · 影响所有页面")
        elif self.selected_block_index is not None:
            self.selection_title_var.set(self._block_label(self.raw_exam["blocks"][self.selected_block_index])[:40])
        else:
            self.selection_title_var.set("卷首信息")
        self.detail_canvas.yview_moveto(0)

    def show_page_settings(self) -> None:
        self._flush_edits(show_error=False)
        self.inspector_tabs.select(2)

    def focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)

    def _populate_tree(self) -> None:
        current = self.tree.selection()
        self.tree.delete(*self.tree.get_children(""))
        # Detached search matches from previous passes must not survive.
        for index in range(len(self.raw_exam.get("blocks", []))):
            if self.tree.exists(f"block-{index}"):
                self.tree.delete(f"block-{index}")
        root_text = "答案信息" if self.raw_exam.get("document_kind") == "answer" else "整份试卷"
        root = self.tree.insert("", tk.END, iid="document", text=root_text, open=True)
        self.root_iid = root
        query = self.search_var.get().strip().casefold()
        for index, block in enumerate(self.raw_exam.get("blocks", [])):
            kind = str(block.get("type", ""))
            if query and query not in self._block_search_text(block).casefold():
                continue
            score = ""
            if kind == "question":
                q = block["question"]
                label = f"{q.get('number', '?')}．{str(q.get('stem', ''))[:24]}"
                category = "客观" if q.get("kind") == "objective" else "主观"
                score = "" if q.get("score") is None else str(q["score"])
            elif kind.startswith("answer_"):
                label, category = self._answer_tree_label(block)
            else:
                label = self._block_label(block)
                category = {"section_title": "大题", "subsection": "模块", "instruction": "提示", "material": "材料", "poetry": "诗歌"}.get(kind, "内容")
            self.tree.insert(root, tk.END, iid=f"block-{index}", text=label[:42], values=(category, score))
        if current and self.tree.exists(current[0]):
            self.tree.selection_set(current[0])
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
        if self.loading_fields:
            return
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
        self.format_error_var.set("")
        self._invalid_option = False
        spec = paragraph_format_for(self.raw_exam, item)
        self.loading_fields = True
        try:
            self.stem_text.delete("1.0", tk.END)
            self.stem_text.insert("1.0", item.text)
            self.stem_text.edit_modified(False)
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

    def _schedule_global_commit(self) -> None:
        if self._import_in_progress:
            return
        if self.loading_fields:
            return
        if self._global_commit_after:
            self.after_cancel(self._global_commit_after)
        self._set_settings_message("设置已修改，正在等待更新…")
        self._invalidate_edit()
        self._global_commit_after = self.after(280, lambda: self._commit_global_settings(show_error=False))

    def _commit_global_settings(self, *, show_error: bool) -> bool:
        if self._global_commit_after:
            self.after_cancel(self._global_commit_after)
        self._global_commit_after = None
        try:
            margins = {
                "margin_top_mm": float(self.margin_top_var.get()),
                "margin_bottom_mm": float(self.margin_bottom_var.get()),
                "margin_left_mm": float(self.margin_left_var.get()),
                "margin_right_mm": float(self.margin_right_var.get()),
            }
            if any(not 5 <= value <= 45 for value in margins.values()):
                raise ValueError("页边距应在 5 到 45 毫米之间。")
        except ValueError as exc:
            self._global_invalid = True
            self._set_settings_message(str(exc))
            if show_error:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return False
        self._global_invalid = False
        metadata = self.raw_exam.setdefault("metadata", {})
        updates = {"exam_name": self.exam_name_var.get().strip(),
                   "subject_name": self.subject_name_var.get().strip(),
                   "meta_text": self.exam_meta_var.get().strip(),
                   "layout_preset": DEFAULT_PRESET,
                   "document_scope": "practice" if self.document_scope_var.get() == "局部训练" else "full_exam",
                   "page_overrides": margins}
        if all(metadata.get(key) == value for key, value in updates.items()):
            return True
        self._push_direct_history()
        header_changed = any(metadata.get(key, "") != updates[key] for key in ("exam_name", "subject_name", "meta_text"))
        self._queue_settings_feedback(header_changed=header_changed)
        metadata.update(updates)
        metadata.pop("page_spacing_scale", None)
        if self.selected_content_object is not None and self.selected_content_object.block_index is None:
            key = self.selected_content_object.key
            self._content_objects = metadata_content_objects(self.raw_exam)
            self.selected_content_object = next((item for item in self._content_objects if item.key == key), None)
            self._populate_object_tree()
            if self.selected_content_object is not None:
                self._load_content_object(self.selected_content_object)
        self._set_settings_message("设置已应用，正在更新预览…")
        self._schedule_canvas_preview()
        return True

    def apply_global_settings(self) -> None:
        if self._import_in_progress:
            return
        if not self._commit_global_settings(show_error=True):
            return
        if self._settings_feedback is None:
            self._queue_settings_feedback()
        if self._canvas_preview_after:
            self.after_cancel(self._canvas_preview_after)
            self._canvas_preview_after = None
        self.request_preview()

    def _set_settings_message(self, message: str) -> None:
        if "page_settings_status_var" in self.__dict__:
            self.page_settings_status_var.set(message)
        if "document_editor" in self.__dict__:
            self.document_editor.update_message_var.set(message)

    def _queue_settings_feedback(self, *, header_changed: bool = False) -> None:
        if "document_editor" not in self.__dict__:
            return
        previous = self._settings_feedback or {}
        header_changed = header_changed or bool(previous.get("header_changed"))
        self._settings_feedback = {"page": 0 if header_changed else self.document_editor.current_page(),
                                   "header_changed": header_changed}

    def _load_global_fields(self) -> None:
        self._global_invalid = False
        self.loading_fields = True
        super()._load_global_fields()
        metadata = self.raw_exam.setdefault("metadata", {})
        metadata["layout_preset"] = DEFAULT_PRESET
        use_natural_pagination(metadata)
        page = load_layout(self.layout_path).get("page", {})
        overrides = metadata.get("page_overrides", {})
        defaults = preset_page(self.raw_exam, DEFAULT_PRESET)
        self.loading_fields = True
        try:
            self.layout_preset_var.set(PRESET_LABELS[DEFAULT_PRESET])
            if hasattr(self, "document_scope_var"):
                self.document_scope_var.set("局部训练" if metadata.get("document_scope") == "practice" else "全卷")
            self.margin_top_var.set(str(overrides.get("margin_top_mm", defaults.get("margin_top_mm", page.get("margin_top_mm", 25.4)))))
            self.margin_bottom_var.set(str(overrides.get("margin_bottom_mm", defaults.get("margin_bottom_mm", page.get("margin_bottom_mm", 25.4)))))
            self.margin_left_var.set(str(overrides.get("margin_left_mm", defaults.get("margin_left_mm", page.get("margin_left_mm", 31.75)))))
            self.margin_right_var.set(str(overrides.get("margin_right_mm", defaults.get("margin_right_mm", page.get("margin_right_mm", 31.75)))))
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
        self._refresh_score_display()
        return True

    def _commit_score_entry(self, _event: object | None = None) -> str:
        if self._import_in_progress:
            return "break"
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
        if self.raw_exam.get("document_kind") == "answer":
            self.score_total_label.configure(text="独立答案文档", foreground="#107C10")
            self.score_delta_label.configure(text="不作试卷总分校验", foreground="#107C10")
            self._refresh_warning_banner(calculate_score_summary(self.raw_exam))
            return
        state = summary or calculate_score_summary(self.raw_exam)
        metadata = self.raw_exam.get("metadata", {})
        text = f"当前 {format_score(state.total)} / {format_score(state.target)} 分"
        if metadata.get("document_scope") == "practice" and not metadata.get("declared_total_score"):
            text = f"已录入合计 {format_score(state.total)} 分"
        self.score_total_label.configure(text=text)
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
            if self.raw_exam.get("document_kind") != "answer" and question_count == 0:
                warnings.append(f"当前识别 {question_count} 道题，仍可继续编辑和导出")
            diagnostics = self.raw_exam.get("diagnostics", [])
            if diagnostics:
                warnings.append(f"另有 {len(diagnostics)} 条识别提示")
            if self._preview_warnings:
                warnings.append(f"预览提示 {len(self._preview_warnings)} 条")
            self.warning_var.set("；".join(warnings) if warnings else "结构与分值检查暂无提醒")
            self.review_panel.set_items(review_items(self.raw_exam, self._preview_warnings))
        finally:
            self._warning_refreshing = False

    def _go_to_issue(self, index) -> None:
        if self._import_in_progress:
            return
        if self.search_var.get():
            self.search_var.set("")
            self._populate_tree()
        item = self.root_iid if index is None else f"block-{index}"
        if self.tree.exists(item):
            self.tree.selection_set(item)
            self.tree.see(item)
            self._on_tree_select(None)
            self.inspector_tabs.select(0)
            if index is not None:
                self._jump_to_block(index)

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

    def _restore_selection(self) -> None:
        key = self.selected_content_object.key if self.selected_content_object else None
        iid = f"block-{self.selected_block_index}" if self.selected_block_index is not None else self.root_iid
        # The old editor buffer must never be committed into a restored snapshot.
        self.selected_content_object = None
        self.selected_block_index = None
        self._load_global_fields()
        self._populate_tree()
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self._on_tree_select(None)
            if key and self.object_tree.exists(key):
                self._select_object(key, navigate=False)
        self._refresh_score_display()

    def default_export_basename(self) -> str:
        """Use the imported Word filename as the export dialog basename."""

        metadata = self.raw_exam.get("metadata", {})
        fallback = str(metadata.get("exam_name", "语文试卷"))
        return default_export_basename(self.current_exam_path, fallback)

    def open_export_dialog(self) -> None:
        if getattr(self, "_export_in_progress", False) or getattr(self, "_import_in_progress", False):
            messagebox.showinfo(APP_TITLE, "请等待当前导入或导出完成。", parent=self)
            return
        if not self._flush_edits(show_error=True):
            return
        self._cancel_preview()
        summary = calculate_score_summary(self.raw_exam)
        lines: list[str] = []
        if summary.missing_questions:
            lines.append("未填写分值：" + "、".join(summary.missing_questions))
        if summary.total != summary.target:
            lines.append(self._score_difference_text(summary))
        question_count = sum(1 for block in self.raw_exam.get("blocks", []) if block.get("type") == "question")
        if self.raw_exam.get("document_kind") != "answer" and question_count == 0:
            lines.append(f"当前识别 {question_count} 道题，与默认模板题量不同")
        diagnostics = self.raw_exam.get("diagnostics", [])
        if diagnostics:
            lines.append(f"存在 {len(diagnostics)} 条识别提示")
        if lines:
            dialog = ExportWarningDialog(self, lines)
            self.wait_window(dialog)
            if not dialog.result:
                self._schedule_canvas_preview()
                return
        directory = self.current_exam_path.parent if self.current_exam_path else Path.home() / "Documents"
        if self._last_export_path:
            directory = self._last_export_path.parent
        initial = suggested_export_path(self.current_exam_path, directory, self.default_export_basename())
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 Word 文档",
            defaultextension=".docx",
            filetypes=[("Word 文档（DOCX）", "*.docx")],
            initialfile=initial.name,
            initialdir=str(directory),
        )
        if not path:
            self._schedule_canvas_preview()
            return
        target = Path(path)
        basename = target.stem
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
        if override := os.environ.get("EXAM_TYPESETTER_STATE_DIR"):
            return Path(override) / "ui.json"
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
        if self._import_in_progress:
            return
        selection = self.tree.selection()
        if not selection:
            return
        iid = str(selection[0])
        index = int(iid.split("-", 1)[1]) if iid.startswith("block-") else None
        if index == self.selected_block_index and self.selected_content_object is not None:
            return
        if not self.apply_current_question(silent=False):
            previous = f"block-{self.selected_block_index}" if self.selected_block_index is not None else self.root_iid
            self.tree.selection_set(previous)
            return
        self.selected_content_object = None
        self.selected_block_index = index
        block = self.raw_exam["blocks"][index] if index is not None else None
        self.selected_block_type = str(block.get("type", "")) if block else None
        self.loading_fields = True
        try:
            if block is not None and block.get("type") == "question":
                question = block["question"]
                self.selection_title_var.set(f"第 {question['number']} 题")
                self.kind_var.set("客观题" if question.get("kind") == "objective" else "主观题")
                score = parse_score(question.get("score"))
                self.score_var.set("" if score is None else format_score(score))
                siblings = [widget for widget in self.content_group.body.pack_slaves() if widget is not self.identity_frame]
                self.identity_frame.pack(fill=tk.X, pady=(0, 6), before=siblings[0])
            else:
                self.selection_title_var.set(self._block_label(block)[:40] if block else "卷首信息")
                self.identity_frame.pack_forget()
            self._content_objects = content_objects_for_block(block, index) if block is not None else metadata_content_objects(self.raw_exam)
            self._populate_object_tree()
            if self._content_objects:
                self.stem_text.configure(state=tk.NORMAL)
                self._select_object(self._content_objects[0].key, navigate=False)
            else:
                self.stem_text.configure(state=tk.NORMAL)
                self.stem_text.delete("1.0", tk.END)
                self.stem_text.insert("1.0", "此原生对象将在导出时保留，请在 Word 中检查表格或图片。")
                self.stem_text.configure(state=tk.DISABLED)
        finally:
            self.loading_fields = False
        self.selection_dirty = False
        self._inspector_tab_changed()
        if index is not None:
            self._jump_to_block(index)

    def _load_right_text(self, _block: dict[str, Any]) -> None:
        """Compatibility hook for the retired mirrored-text editor."""

        return

    def _select_object(self, key: str, *, navigate: bool) -> None:
        if self._import_in_progress:
            return
        if self.selected_content_object is not None and self.selected_content_object.key == key:
            return
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
        menu = tk.Menu(self)
        files = tk.Menu(menu, tearoff=False)
        files.add_command(label="导入 Word…", accelerator="Ctrl+O", command=self.import_new_exam)
        files.add_command(label="导出 Word…", accelerator="Ctrl+E", command=self.open_export_dialog)
        files.add_separator()
        files.add_command(label="保存草稿", accelerator="Ctrl+S", command=self.save_project)
        files.add_command(label="草稿另存为…", accelerator="Ctrl+Shift+S", command=lambda: self.save_project(save_as=True))
        files.add_command(label="恢复自动草稿…", command=self.recover_draft)
        files.add_command(label="继续编辑草稿…", command=self.open_project)
        files.add_separator()
        files.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=files)
        view = tk.Menu(menu, tearoff=False)
        view.add_command(label="查找内容", accelerator="Ctrl+F", command=self.focus_search)
        view.add_command(label="刷新预览", accelerator="F5", command=self.request_preview)
        view.add_command(label="适合宽度", command=lambda: self.document_editor.fit_width())
        menu.add_cascade(label="视图", menu=view)
        self.configure(menu=menu)
        help_menu = self._help_menu = tk.Menu(menu, tearoff=False)
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

    def check_for_updates(self, *, automatic: bool = False) -> None:
        """Check GitHub for a newer release without blocking the UI."""

        if getattr(self, "_pending_update", None) is not None:
            if not automatic:
                self._offer_install()
            return
        if self._update_in_progress:
            return
        self._update_in_progress = True
        self._update_automatic = automatic
        if not automatic:
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
            self._update_in_progress = False
            if self._update_automatic:
                self._last_update_error = str(payload)
                self._help_menu.entryconfigure(0, label="重新检查更新（上次连接失败）")
                return
            self.busy_bar.stop()
            self.status_var.set("检查更新失败。")
            messagebox.showerror(APP_TITLE, str(payload), parent=self)
            return
        if kind == "checked":
            self._update_in_progress = False
            if not self._update_automatic:
                self.busy_bar.stop()
            info = payload
            if not info.newer:
                if self._update_automatic:
                    return
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
                "是否下载更新包？",
                parent=self,
            )
            if install:
                self._download_update(info)
            return
        if kind == "downloaded":
            self._update_in_progress = False
            if not self.busy:
                self.busy_bar.stop()
            self._pending_update = Path(payload)
            self._offer_install()

    def _offer_install(self) -> None:
        if self._import_in_progress or self._export_in_progress:
            self.status_var.set("更新包已就绪。任务完成后可从“帮助 → 检查更新”安装。")
            return
        if messagebox.askyesno("更新包已校验", "现在安装并重新打开工作台吗？稍后也可在帮助菜单安装。", parent=self):
            self._on_close(update_path=self._pending_update)

    def _download_update(self, info: object) -> None:
        """Download the selected portable release in a worker thread."""

        self._update_automatic = False
        self._update_in_progress = True
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

    def _on_close(self, *, update_path: Path | None = None) -> None:
        if self._closing:
            return
        if getattr(self, "_export_in_progress", False):
            self.status_var.set("正在完成 Word 导出，请完成后关闭窗口。")
            return
        if self._import_in_progress:
            self.cancel_import()
        if not self._confirm_replace():
            return
        if update_path is not None:
            try:
                schedule_portable_update(update_path)
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror(APP_TITLE, str(exc), parent=self)
                return
        self._closing = True
        self._clear_recovery()
        self._cancel_editor_timers()
        uninstall_file_drop(self)
        self.status_var.set("正在结束后台任务…")
        finished = threading.Event()

        def cleanup() -> None:
            try:
                self._recovery.close()
                self._preview_service.close()
                self._export_executor.shutdown(wait=True, cancel_futures=True)
                self.temp_context.cleanup()
            finally:
                finished.set()

        threading.Thread(target=cleanup, daemon=True).start()

        def wait_closed() -> None:
            if finished.is_set():
                self.destroy()
            else:
                self.after(50, wait_closed)

        wait_closed()

    def _cancel_editor_timers(self) -> None:
        self._settings_feedback = None
        self._preview_settings_feedback = None
        self._set_settings_message("修改后自动更新，也可点击应用并查看。")
        for name in ("_text_commit_after", "_global_commit_after", "_canvas_preview_after", "_search_after"):
            timer = getattr(self, name, None)
            if timer:
                self.after_cancel(timer)
            setattr(self, name, None)

    def _flush_edits(self, *, show_error: bool) -> bool:
        if self._invalid_option:
            if show_error:
                messagebox.showerror(APP_TITLE, self.format_error_var.get(), parent=self)
            return False
        if self.selected_content_object is not None and self.selected_content_object.block_index is not None:
            try:
                self._format_spec_from_controls()
            except ValueError:
                if show_error:
                    messagebox.showerror(APP_TITLE, "当前格式参数无效，请检查字号、缩进和行距后重试。", parent=self)
                return False
        if self._text_commit_after:
            self.after_cancel(self._text_commit_after)
            self._text_commit_after = None
        if not self.apply_current_question(silent=not show_error):
            return False
        if (self._global_commit_after or self._global_invalid) and not self._commit_global_settings(show_error=show_error):
            return False
        return True

    def _confirm_replace(self) -> bool:
        if not self._flush_edits(show_error=True):
            return False
        if draft_signature(self.raw_exam) == self._saved_signature:
            return True
        answer = messagebox.askyesnocancel("保存修改", "当前草稿有尚未保存的修改。\n\n是否先保存可继续编辑的草稿？\n选择“否”会放弃这些修改。", parent=self)
        if answer is None:
            return False
        return self.save_project() if answer else True

    def _refresh_history_buttons(self) -> None:
        if self._import_in_progress:
            return
        self.undo_button.configure(state=tk.NORMAL if self.undo_stack else tk.DISABLED)
        self.redo_button.configure(state=tk.NORMAL if self.redo_stack else tk.DISABLED)
        dirty = self._saved_signature and draft_signature(self.raw_exam) != self._saved_signature
        path = self._project_path or self.current_exam_path
        name = path.name if path else "内置示例"
        self.file_label_var.set(f"{name}  ·  {'有未保存修改' if dirty else '已就绪'}")

    def open_export_result(self, folder: bool) -> None:
        if self._last_export_path is None:
            return
        try:
            os.startfile(self._last_export_path.parent if folder else self._last_export_path)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法打开导出结果：{exc}", parent=self)


def main() -> int:
    CurrentDesktopApp().mainloop()
    return 0


__all__ = ["APP_TITLE", "APP_VERSION", "CurrentDesktopApp", "import_exam", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
