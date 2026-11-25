"""基于 PySide6 的微软 Fluent 清蓝三栏语文试题排版工作台主窗口。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import ctypes
from ctypes import wintypes
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any
import webbrowser

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..config import load_layout
from ..current_importer import import_exam
from ..current_pipeline import ExportDestinationError, build_documents
from ..drafts import DraftRecovery, read_draft, save_draft
from ..export_naming import default_export_basename, typeset_name
from ..github_updater import (
    GITHUB_REPOSITORY,
    check_latest_release,
    download_release_asset,
    schedule_portable_update,
)
from ..inspector_model import (
    ContentObject,
    build_object_locators,
    content_objects_for_block,
    default_format_for_exam,
    inline_formats_for,
    metadata_content_objects,
    objects_in_scope,
    paragraph_format_for,
    set_content_object_text,
    set_inline_format,
    set_paragraph_format,
)
from ..layout_presets import DEFAULT_PRESET, PRESET_LABELS, preset_page
from ..page_target import use_natural_pagination
from ..preview_service import PreviewResult, PreviewService
from ..review_items import review_items
from ..score_summary import ScoreSummary, calculate_score_summary, format_score, parse_score
from ..version import APP_TITLE, APP_VERSION
from ..workbench_state import draft_signature, suggested_export_path, validate_format
from .editing_actions import EditingActionsMixin
from .session_actions import SessionActionsMixin
from .icons import fluent_icon
from .inspector_panel import InspectorPanel
from .outline_panel import OutlinePanel, block_display_label
from .preview_panel import PreviewPanel
from .theme import PALETTE, apply_fluent_theme, apply_windows_chrome
from .widgets import DropOverlay, InlineResultBanner


WM_DROPFILES = 0x0233


def resource_path(relative: str) -> Path:
    """定位开发目录或 PyInstaller 冻结目录中的内置资源。"""

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative


def _read_hdrop_paths(hdrop: int) -> tuple[Path, ...]:
    """从 Windows WM_DROPFILES 的 HDROP 句柄中提取文件路径。"""

    shell32 = ctypes.windll.shell32
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    count = int(shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0))
    paths: list[Path] = []
    for index in range(count):
        length = int(shell32.DragQueryFileW(hdrop, index, None, 0))
        buf = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(hdrop, index, buf, length + 1)
        if buf.value:
            paths.append(Path(buf.value))
    return tuple(paths)


class _WorkerSignals(QObject):
    """跨线程安全回传信号，替代旧版定时轮询队列。"""

    previewReady = Signal(int, object)
    exportReady = Signal(int, object)
    importReady = Signal(int, object, object)
    importFailed = Signal(int, object, str)
    recoveryError = Signal(int, str)
    updateChecked = Signal(object)
    updateDownloaded = Signal(object)
    updateError = Signal(str)


class CurrentDesktopApp(EditingActionsMixin, SessionActionsMixin, QMainWindow):
    """微软 Fluent 清蓝三栏语文试题排版工作台。"""

    def __init__(self, *, check_updates: bool = True) -> None:
        super().__init__()
        self._closing = False
        self._signals = _WorkerSignals(self)
        self._connect_worker_signals()

        # 运行目录与内置资源
        self.temp_context = tempfile.TemporaryDirectory(prefix="exam_typesetter_")
        self.temp_dir = Path(self.temp_context.name)
        self.layout_path = resource_path("templates/layout.yaml")
        self.template_path: Path | None = None
        self.current_exam_path: Path | None = None
        self._project_path: Path | None = None
        self._last_export_path: Path | None = None

        # 文档数据与修订状态机
        self.raw_exam: dict[str, Any] = json.loads(
            resource_path("samples/exam.json").read_text(encoding="utf-8")
        )
        self._session_generation = 0
        self._revision = 0
        self._preview_ticket = 0
        self._preview_generation = 0
        self._export_generation = 0
        self._import_generation = 0
        self._requested_revision: tuple[int, int] | None = None
        self._displayed_revision: tuple[int, int] | None = None
        self._proof_requested = False
        self._import_in_progress = False
        self._export_in_progress = False
        self._import_future: Future[Any] | None = None
        self._busy = False

        # 编辑状态、撤销重做栈与校验状态
        self.selected_block_index: int | None = None
        self.selected_content_object: ContentObject | None = None
        self._content_objects: list[ContentObject] = []
        self.undo_stack: list[dict[str, Any]] = []
        self.redo_stack: list[dict[str, Any]] = []
        self._saved_signature = ""
        self._export_signature = ""
        self._global_invalid = False
        self._invalid_option = False
        self._settings_feedback: dict[str, Any] | None = None
        self._preview_settings_feedback: dict[str, Any] | None = None
        self._preview_warnings: tuple[str, ...] = ()
        self.preview_pages: list[Path] = []
        self._startup_preview_deferred = True
        self._startup_preview_pending = False

        # 后台服务与自动恢复
        self._preview_service = PreviewService(build_documents)
        self._export_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exam-export")
        self._recovery = DraftRecovery(self._ui_settings_path().parent / "recovery")
        self._recovery_claim: Any = None
        self._recovered_path: Path | None = None

        # 更新检查状态
        self._update_in_progress = False
        self._update_automatic = False
        self._pending_update: Path | None = None
        self._last_update_error: str | None = None

        # 拖放兼容状态
        self.drop_enabled = True
        self._drop_metrics = {"native_messages": 0, "qt_drops": 0}
        self._inspector_visible = True
        self._saved_inspector_width = 366

        # 防抖定时器
        self._text_commit_timer = self._make_single_timer(220, self._commit_text_after_delay)
        self._global_commit_timer = self._make_single_timer(280, lambda: self._commit_global_settings(show_error=False))
        self._preview_debounce_timer = self._make_single_timer(95, self.request_preview)
        self._autosave_timer = self._make_single_timer(1200, self._autosave)

        # 构建界面与绑定交互
        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.setAcceptDrops(True)
        self._apply_icon()
        self._build_menu()
        self._build_ui()
        self._bind_signals()
        self._install_shortcuts()
        self._restore_window_geometry()
        apply_windows_chrome(self)

        # 载入内置示例并启动首次预览
        self._load_global_fields()
        self.outline_panel.populate(self.raw_exam, keep_selection=False)
        self.preview_panel.rich_editor.load_document(self.raw_exam, self.layout_path, preserve_scroll=False)
        self._saved_signature = draft_signature(self.raw_exam)
        self.outline_panel.select_block(None, emit=True)
        self._refresh_history_buttons()
        self._set_status("内置示例已就绪。可直接在纸面选字设定格式，或拖入 Word 文档开始排版。")

        self._startup_preview_pending = True
        QTimer.singleShot(260, self._release_startup_preview)
        if check_updates:
            QTimer.singleShot(450, lambda: self.recover_draft(automatic=True))
            QTimer.singleShot(6000, lambda: self.check_for_updates(automatic=True))

    # ==================== 界面构建 ====================

    def _make_single_timer(self, interval_ms: int, callback) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(interval_ms)
        timer.timeout.connect(callback)
        return timer

    @staticmethod
    def _resource_path(relative: str) -> Path:
        return resource_path(relative)

    def _apply_icon(self) -> None:
        icon_png = resource_path("assets/app-icon.png")
        if icon_png.is_file():
            self.setWindowIcon(QIcon(str(icon_png)))

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("文件(&F)")
        self._add_action(file_menu, "导入 Word…", self.import_new_exam, "Ctrl+O")
        self._add_action(file_menu, "导出 Word…", self.open_export_dialog, "Ctrl+E")
        file_menu.addSeparator()
        self._add_action(file_menu, "保存草稿", self.save_project, "Ctrl+S")
        self._add_action(file_menu, "草稿另存为…", lambda: self.save_project(save_as=True), "Ctrl+Shift+S")
        self._add_action(file_menu, "继续编辑草稿…", self.open_project)
        self._add_action(file_menu, "恢复自动草稿…", self.recover_draft)
        file_menu.addSeparator()
        self._add_action(file_menu, "退出", self.close)

        edit_menu = bar.addMenu("编辑(&E)")
        self._undo_action_item = self._add_action(edit_menu, "撤销", self.undo_action, "Ctrl+Z")
        self._redo_action_item = self._add_action(edit_menu, "重做", self.redo_action, "Ctrl+Y")
        edit_menu.addSeparator()
        self._add_action(edit_menu, "查找内容", self.focus_search, "Ctrl+F")

        view_menu = bar.addMenu("视图(&V)")
        self._add_action(view_menu, "排版编辑视图（Word 式直编）", lambda: self.preview_panel.set_view_mode("rich"), "Ctrl+1")
        self._add_action(view_menu, "分页预览视图（A4 分页校对）", lambda: self.preview_panel.set_view_mode("pages"), "Ctrl+2")
        view_menu.addSeparator()
        self._add_action(view_menu, "刷新预览", self.request_preview, "F5")
        self._add_action(view_menu, "适合宽度", lambda: self.preview_panel.fit_width(), "Ctrl+0")
        self._add_action(view_menu, "放大预览", lambda: self.preview_panel.change_zoom(0.08), "Ctrl+=")
        self._add_action(view_menu, "缩小预览", lambda: self.preview_panel.change_zoom(-0.08), "Ctrl+-")
        view_menu.addSeparator()
        self._toggle_inspector_action = self._add_action(
            view_menu, "显示 / 隐藏属性面板", self.toggle_inspector, "Ctrl+B"
        )
        self._add_action(view_menu, "整卷设置", self.show_page_settings)

        help_menu = bar.addMenu("帮助(&H)")
        self._check_update_action = self._add_action(help_menu, "检查更新", self.check_for_updates)
        help_menu.addSeparator()
        self._add_action(help_menu, "GitHub 更新页面", self.open_github_update_page)
        self._add_action(help_menu, "打开 GitHub 仓库", lambda: webbrowser.open(GITHUB_REPOSITORY))
        help_menu.addSeparator()
        self._add_action(help_menu, "关于与使用许可", self.show_about)

    def _add_action(self, menu, label: str, slot, shortcut: str | None = None) -> QAction:
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. 顶部单行集成工具栏（清爽 Fluent 浅白底 + 1px 底边框）
        header = QFrame(central)
        header.setObjectName("TopHeaderBar")
        header.setFixedHeight(54)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 8, 16, 8)
        h_layout.setSpacing(10)

        icon_png = resource_path("assets/app-icon.png")
        if icon_png.is_file():
            logo = QLabel(header)
            logo.setPixmap(
                QPixmap(str(icon_png)).scaled(
                    26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
            h_layout.addWidget(logo)

        brand_title = QLabel("语文试题排版工作台", header)
        brand_title.setObjectName("BrandTitle")
        h_layout.addWidget(brand_title)

        version_badge = QLabel(f"v{APP_VERSION}", header)
        version_badge.setObjectName("BrandVersion")
        h_layout.addWidget(version_badge)

        # 分隔竖线
        sep1 = QFrame(header)
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFixedHeight(20)
        sep1.setStyleSheet(f"color: {PALETTE['border']}; background-color: {PALETTE['border']}; max-width: 1px;")
        h_layout.addWidget(sep1)

        # 当前文件与修改状态微胶囊
        self.file_badge = QLabel("内置示例 · 已就绪", header)
        self.file_badge.setObjectName("FileStatusBadge")
        self.file_badge.setMaximumWidth(360)
        h_layout.addWidget(self.file_badge)

        h_layout.addStretch(1)

        # 撤销 / 重做 快捷图标按钮
        self.undo_button = QPushButton(header)
        self.undo_button.setProperty("variant", "ghost")
        self.undo_button.setIcon(fluent_icon("undo", PALETTE["text"], 16))
        self.undo_button.setIconSize(QSize(16, 16))
        self.undo_button.setToolTip("撤销 (Ctrl+Z)")
        self.undo_button.setFixedSize(32, 32)
        self.undo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_button.clicked.connect(self.undo_action)
        h_layout.addWidget(self.undo_button)

        self.redo_button = QPushButton(header)
        self.redo_button.setProperty("variant", "ghost")
        self.redo_button.setIcon(fluent_icon("redo", PALETTE["text"], 16))
        self.redo_button.setIconSize(QSize(16, 16))
        self.redo_button.setToolTip("重做 (Ctrl+Y)")
        self.redo_button.setFixedSize(32, 32)
        self.redo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.redo_button.clicked.connect(self.redo_action)
        h_layout.addWidget(self.redo_button)

        sep2 = QFrame(header)
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFixedHeight(20)
        sep2.setStyleSheet(f"color: {PALETTE['border']}; background-color: {PALETTE['border']}; max-width: 1px;")
        h_layout.addWidget(sep2)

        # 取消导入按钮（仅导入中显示）
        self.cancel_import_button = QPushButton("取消导入", header)
        self.cancel_import_button.setProperty("variant", "danger-soft")
        self.cancel_import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_import_button.clicked.connect(self.cancel_import)
        self.cancel_import_button.hide()
        h_layout.addWidget(self.cancel_import_button)

        # 导入 Word 按钮
        self.import_button = QPushButton("导入 Word", header)
        self.import_button.setProperty("variant", "accent-outline")
        self.import_button.setIcon(fluent_icon("import_doc", PALETTE["accent_text"], 15))
        self.import_button.setIconSize(QSize(15, 15))
        self.import_button.setToolTip("导入 .docx 或 .doc 试题，也可直接拖入窗口 (Ctrl+O)")
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.clicked.connect(self.import_new_exam)
        h_layout.addWidget(self.import_button)

        # 保存草稿按钮
        self.save_button = QPushButton("保存草稿", header)
        self.save_button.setProperty("variant", "ghost")
        self.save_button.setIcon(fluent_icon("save", PALETTE["text_secondary"], 15))
        self.save_button.setIconSize(QSize(15, 15))
        self.save_button.setToolTip("保存结构化草稿以便稍后继续编辑 (Ctrl+S)")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.clicked.connect(self.save_project)
        h_layout.addWidget(self.save_button)

        # 整卷设置快捷入口
        self.page_settings_btn = QPushButton("整卷设置", header)
        self.page_settings_btn.setProperty("variant", "ghost")
        self.page_settings_btn.setIcon(fluent_icon("settings", PALETTE["text_secondary"], 15))
        self.page_settings_btn.setIconSize(QSize(15, 15))
        self.page_settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.page_settings_btn.clicked.connect(self.show_page_settings)
        h_layout.addWidget(self.page_settings_btn)

        # 折叠/展开右侧检查器按钮
        self.inspector_toggle_btn = QPushButton(header)
        self.inspector_toggle_btn.setProperty("variant", "ghost")
        self.inspector_toggle_btn.setCheckable(True)
        self.inspector_toggle_btn.setChecked(True)
        self.inspector_toggle_btn.setIcon(fluent_icon("panel_right", PALETTE["text_secondary"], 16))
        self.inspector_toggle_btn.setIconSize(QSize(16, 16))
        self.inspector_toggle_btn.setToolTip("展开 / 收起右侧属性检查器 (Ctrl+B)")
        self.inspector_toggle_btn.setFixedSize(32, 32)
        self.inspector_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inspector_toggle_btn.clicked.connect(self.toggle_inspector)
        h_layout.addWidget(self.inspector_toggle_btn)

        # 导出 Word 主按钮
        self.export_button = QPushButton("导出 Word", header)
        self.export_button.setProperty("variant", "primary")
        self.export_button.setIcon(fluent_icon("export_doc", "#FFFFFF", 15))
        self.export_button.setIconSize(QSize(15, 15))
        self.export_button.setToolTip("导出正式排版后的 DOCX 文档 (Ctrl+E)")
        self.export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_button.clicked.connect(self.open_export_dialog)
        h_layout.addWidget(self.export_button)

        root.addWidget(header)

        # 导出结果内嵌轻横幅
        self.result_banner = InlineResultBanner(central)
        self.result_banner.openFileRequested.connect(lambda: self.open_export_result(False))
        self.result_banner.openFolderRequested.connect(lambda: self.open_export_result(True))
        root.addWidget(self.result_banner)

        # 2. 主体三栏可拖拽工作台（左：大纲 | 中：沉浸 A4 预览 | 右：属性检查器）
        workspace = QWidget(central)
        ws_layout = QHBoxLayout(workspace)
        ws_layout.setContentsMargins(10, 10, 10, 10)
        ws_layout.setSpacing(0)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, workspace)
        self.main_splitter.setChildrenCollapsible(False)

        self.outline_panel = OutlinePanel(self.main_splitter)
        self.outline_panel.setMinimumWidth(220)

        self.preview_panel = PreviewPanel(self.main_splitter)
        self.preview_panel.setMinimumWidth(440)

        self.inspector_panel = InspectorPanel(self.main_splitter)
        self.inspector_panel.setMinimumWidth(316)

        self.main_splitter.addWidget(self.outline_panel)
        self.main_splitter.addWidget(self.preview_panel)
        self.main_splitter.addWidget(self.inspector_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([282, 792, 366])
        self.main_splitter.splitterMoved.connect(self._save_ui_settings)

        ws_layout.addWidget(self.main_splitter)
        root.addWidget(workspace, 1)

        # 3. 底部极细状态栏
        status_bar = QFrame(central)
        status_bar.setObjectName("BottomStatusBar")
        status_bar.setFixedHeight(28)
        s_layout = QHBoxLayout(status_bar)
        s_layout.setContentsMargins(16, 0, 16, 0)
        s_layout.setSpacing(12)

        self.status_label = QLabel("已就绪", status_bar)
        self.status_label.setObjectName("BottomStatusText")
        s_layout.addWidget(self.status_label, 1)

        self.busy_bar = QProgressBar(status_bar)
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedWidth(96)
        self.busy_bar.hide()
        s_layout.addWidget(self.busy_bar)

        attribution = QLabel("本地处理 · 不上传文档内容  |  公众号：蓑衣微言", status_bar)
        attribution.setObjectName("BottomAttribution")
        s_layout.addWidget(attribution)

        root.addWidget(status_bar)

        # 拖拽提示遮罩（覆盖在 central 上）
        self.drop_overlay = DropOverlay(central)

    def _bind_signals(self) -> None:
        self.outline_panel.nodeSelected.connect(self._on_outline_select)
        self.outline_panel.issueSelected.connect(self._go_to_issue)

        self.preview_panel.printProofToggled.connect(self.toggle_print_proof)
        self.preview_panel.refreshRequested.connect(self.request_preview)

        rich = self.preview_panel.rich_editor
        rich.cursorObjectChanged.connect(self._on_rich_cursor_object_changed)
        rich.objectTextEdited.connect(self._on_rich_object_text_edited)
        rich.inlineStyleRequested.connect(self._on_rich_inline_style)
        rich.clearInlineRequested.connect(self._on_rich_clear_inline)
        rich.paragraphStyleRequested.connect(self._on_rich_paragraph_style)
        rich.batchApplyRequested.connect(self._apply_same_type)
        rich.undoRequested.connect(self.undo_action)
        rich.redoRequested.connect(self.redo_action)

        ins = self.inspector_panel
        ins.tabChanged.connect(self._on_inspector_tab_changed)
        ins.collapseRequested.connect(self.toggle_inspector)
        ins.objectSelected.connect(lambda key: self._select_object(key, navigate=True))
        ins.textEdited.connect(self._schedule_text_commit)
        ins.selectionChangedHint.connect(self._update_selection_hint)
        ins.identityChanged.connect(self._on_identity_changed)
        ins.scoreCommitted.connect(self._commit_score_entry)
        ins.fontChanged.connect(self._on_font_changed)
        ins.inlineDecorationRequested.connect(self._on_inspector_inline_decoration)
        ins.clearInlineRequested.connect(self._on_inspector_clear_inline)
        ins.paragraphChanged.connect(self._on_paragraph_changed)
        ins.optionsChanged.connect(self._on_options_changed)
        ins.batchApplyRequested.connect(self._apply_same_type)
        ins.resetGroupRequested.connect(self._restore_group)
        ins.globalFieldEdited.connect(self._schedule_global_commit)
        ins.scopeChanged.connect(self._on_scope_changed)
        ins.applyGlobalRequested.connect(self.apply_global_settings)

    def _connect_worker_signals(self) -> None:
        self._signals.previewReady.connect(self._on_preview_ready)
        self._signals.exportReady.connect(self._on_export_ready)
        self._signals.importReady.connect(self._on_import_ready)
        self._signals.importFailed.connect(self._on_import_failed)
        self._signals.recoveryError.connect(self._on_recovery_error)
        self._signals.updateChecked.connect(self._on_update_checked)
        self._signals.updateDownloaded.connect(self._on_update_downloaded)
        self._signals.updateError.connect(self._on_update_error)

    def _install_shortcuts(self) -> None:
        # 额外支持 Ctrl+Shift+Z 重做与 Esc 清除搜索
        redo_alt = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        redo_alt.activated.connect(self.redo_action)
        esc_search = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.outline_panel.search_input)
        esc_search.activated.connect(self.outline_panel.clear_search)

    # ==================== 窗口状态、拖放与便捷属性 ====================

    @property
    def document_editor(self) -> PreviewPanel:
        """兼容历史外部诊断与测试访问预览组件。"""

        return self.preview_panel

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "drop_overlay") and self.centralWidget() is not None:
            self.drop_overlay.setGeometry(self.centralWidget().rect())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls():
            paths = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
            if any(p.suffix.lower() in {".docx", ".doc"} for p in paths):
                event.acceptProposedAction()
                self.drop_overlay.setGeometry(self.centralWidget().rect())
                self.drop_overlay.raise_()
                self.drop_overlay.show()
                return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self.drop_overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.drop_overlay.hide()
        urls = event.mimeData().urls()
        paths = tuple(Path(url.toLocalFile()) for url in urls if url.isLocalFile())
        if paths:
            self._drop_metrics["qt_drops"] += 1
            event.acceptProposedAction()
            self._handle_dropped_files(paths)

    def nativeEvent(self, event_type, message):  # noqa: N802
        """兼容 Windows 原生 WM_DROPFILES 消息（如自动化拖放诊断）。"""

        if os.name == "nt" and event_type == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_DROPFILES:
                    self._drop_metrics["native_messages"] += 1
                    paths = _read_hdrop_paths(int(msg.wParam))
                    ctypes.windll.shell32.DragFinish(wintypes.HANDLE(int(msg.wParam)))
                    if paths:
                        QTimer.singleShot(0, lambda p=paths: self._handle_dropped_files(p))
                    return True, 0
            except Exception:
                pass
        return super().nativeEvent(event_type, message)

    def toggle_inspector(self) -> None:
        """一键收起或展开右侧属性检查器，让中小屏预览更沉浸。"""

        if self._inspector_visible:
            sizes = self.main_splitter.sizes()
            if len(sizes) == 3 and sizes[2] > 0:
                self._saved_inspector_width = sizes[2]
            self.inspector_panel.hide()
            self._inspector_visible = False
            self.inspector_toggle_btn.setChecked(False)
        else:
            self.inspector_panel.show()
            self._inspector_visible = True
            self.inspector_toggle_btn.setChecked(True)
            sizes = self.main_splitter.sizes()
            if len(sizes) == 3:
                total = sum(sizes)
                right = min(max(310, self._saved_inspector_width), max(310, total // 3))
                left = sizes[0] or 276
                self.main_splitter.setSizes([left, max(440, total - left - right), right])

    def show_page_settings(self) -> None:
        self._flush_edits(show_error=False)
        if not self._inspector_visible:
            self.toggle_inspector()
        self.inspector_panel.inspector_tabs.setCurrentIndex(2)

    def focus_search(self) -> None:
        self.outline_panel.focus_search()

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def status_text(self) -> str:
        return self.status_label.text()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.busy_bar.setVisible(busy or self._import_in_progress or self._export_in_progress or self._update_in_progress)

    def _set_settings_message(self, message: str, *, tone: str = "info") -> None:
        self.inspector_panel.set_page_settings_status(message, tone=tone)
        self.preview_panel.set_update_message(message, tone=tone)


    # ==================== 刷盘、状态刷新、持久化与关闭 ====================

    def _flush_edits(self, *, show_error: bool) -> bool:
        self.preview_panel.rich_editor.flush_pending_text()
        if self._invalid_option:
            if show_error:
                self._show_error(self.inspector_panel.format_error_label.text())
            return False
        if self.selected_content_object is not None and self.selected_content_object.block_index is not None:
            try:
                self._format_spec_from_controls()
            except ValueError:
                if show_error:
                    self._show_error("当前格式参数无效，请检查字号、缩进和行距后重试。")
                return False
        self._text_commit_timer.stop()
        if not self.apply_current_question(silent=not show_error):
            return False
        if (self._global_commit_timer.isActive() or self._global_invalid) and not self._commit_global_settings(
            show_error=show_error
        ):
            return False
        return True

    def _confirm_replace(self) -> bool:
        if not self._flush_edits(show_error=True):
            return False
        if draft_signature(self.raw_exam) == self._saved_signature:
            return True
        choice = self._ask_yes_no_cancel(
            "保存修改",
            "当前草稿有尚未保存的修改。\n\n是否先保存可继续编辑的草稿？\n选择“否”将放弃这些修改。",
        )
        if choice is None:
            return False
        return self.save_project() if choice else True

    def _refresh_history_buttons(self) -> None:
        if self._import_in_progress:
            return
        has_undo = bool(self.undo_stack)
        has_redo = bool(self.redo_stack)
        self.undo_button.setEnabled(has_undo)
        self.redo_button.setEnabled(has_redo)
        self._undo_action_item.setEnabled(has_undo)
        self._redo_action_item.setEnabled(has_redo)

        dirty = bool(self._saved_signature and draft_signature(self.raw_exam) != self._saved_signature)
        path = self._project_path or self.current_exam_path
        name = path.name if path else "内置示例"
        self.file_badge.setText(f"{name}  ·  {'有未保存修改' if dirty else '已就绪'}")
        self.file_badge.setToolTip(str(path) if path else "内置高考真题示例文档")
        self.file_badge.setProperty("dirty", "true" if dirty else "false")
        self.file_badge.style().unpolish(self.file_badge)
        self.file_badge.style().polish(self.file_badge)

    def _cancel_editor_timers(self) -> None:
        self._settings_feedback = None
        self._preview_settings_feedback = None
        self._set_settings_message("修改后自动更新，也可点击应用并查看。", tone="info")
        for timer in (
            self._text_commit_timer,
            self._global_commit_timer,
            self._preview_debounce_timer,
            self._autosave_timer,
        ):
            timer.stop()

    @staticmethod
    def _ui_settings_path() -> Path:
        if override := os.environ.get("EXAM_TYPESETTER_STATE_DIR"):
            return Path(override) / "ui.json"
        root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return root / "SuoyiExamTypesetter" / "ui.json"

    def _read_ui_settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self._ui_settings_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_ui_settings(self) -> None:
        settings = self._read_ui_settings()
        sizes = self.main_splitter.sizes()
        if len(sizes) == 3 and sum(sizes) > 0:
            settings["splitter_sizes"] = sizes
        settings["window_size"] = [self.width(), self.height()]
        path = self._ui_settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    def _restore_window_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        max_w = (avail.width() - 48) if avail else 1480
        max_h = (avail.height() - 60) if avail else 900
        default_w = min(1460, max(1080, max_w))
        default_h = min(900, max(680, max_h))

        settings = self._read_ui_settings()
        win_size = settings.get("window_size")
        if isinstance(win_size, list) and len(win_size) == 2:
            try:
                default_w = max(1060, min(max_w, int(win_size[0])))
                default_h = max(660, min(max_h, int(win_size[1])))
            except (TypeError, ValueError):
                pass
        self.setMinimumSize(1040, 640)
        self.resize(default_w, default_h)

        sizes = settings.get("splitter_sizes")
        if isinstance(sizes, list) and len(sizes) == 3 and all(isinstance(v, int) and v >= 200 for v in sizes):
            self.main_splitter.setSizes(sizes)

    def _close_with_update(self, update_path: Path | None) -> None:
        if self._closing:
            return
        if self._export_in_progress:
            self._set_status("正在完成 Word 导出，请完成后关闭窗口。")
            return
        if self._import_in_progress:
            self.cancel_import()
        if not self._confirm_replace():
            return
        if update_path is not None:
            try:
                schedule_portable_update(update_path)
            except (OSError, ValueError, RuntimeError) as exc:
                self._show_error(str(exc))
                return
        self._shutdown_services()
        self.close()

    def _shutdown_services(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._save_ui_settings()
        self._clear_recovery()
        self._cancel_editor_timers()
        self._recovery.close()
        self._preview_service.close()
        self._export_executor.shutdown(wait=True, cancel_futures=True)
        self.temp_context.cleanup()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        if self._export_in_progress:
            self._set_status("正在完成 Word 导出，请完成后关闭窗口。")
            event.ignore()
            return
        if self._import_in_progress:
            self.cancel_import()
        if not self._confirm_replace():
            event.ignore()
            return
        self._shutdown_services()
        event.accept()

    # ==================== 统一对话框辅助（便于测试隔离） ====================

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, APP_TITLE, message)

    def _ask_yes_no(self, title: str, message: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, message, QMessageBox.StandardButton.NoButton, self)
        yes_btn = box.addButton("确定", QMessageBox.ButtonRole.YesRole)
        box.addButton("取消", QMessageBox.ButtonRole.NoRole)
        box.setDefaultButton(yes_btn)
        box.exec()
        return box.clickedButton() is yes_btn

    def _ask_yes_no_cancel(self, title: str, message: str) -> bool | None:
        box = QMessageBox(QMessageBox.Icon.Question, title, message, QMessageBox.StandardButton.NoButton, self)
        yes_btn = box.addButton("保存", QMessageBox.ButtonRole.YesRole)
        no_btn = box.addButton("不保存", QMessageBox.ButtonRole.NoRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is yes_btn:
            return True
        if clicked is no_btn:
            return False
        return None

    def _ask_retry_cancel(self, title: str, message: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, title, message, QMessageBox.StandardButton.NoButton, self)
        retry_btn = box.addButton("重试", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(retry_btn)
        box.exec()
        return box.clickedButton() is retry_btn


def create_application(argv: list[str] | None = None) -> QApplication:
    """获取或创建配置好高 DPI 与 Fluent 主题的 QApplication。"""

    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    apply_fluent_theme(app)
    return app


def main() -> int:
    app = create_application()
    window = CurrentDesktopApp()
    window.show()
    return int(app.exec())


__all__ = [
    "APP_TITLE",
    "APP_VERSION",
    "CurrentDesktopApp",
    "DEFAULT_PRESET",
    "PRESET_LABELS",
    "create_application",
    "import_exam",
    "main",
    "resource_path",
    "typeset_name",
]
