"""中栏：沉浸式 A4 页面连续预览舞台（高 DPI 懒加载、真实纸张投影与精准定位）。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Hashable, Mapping

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImageReader, QIntValidator, QKeyEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import fluent_icon
from .rich_document_editor import RichDocumentEditor
from .theme import PALETTE
from .widgets import SegmentedTabs


class _PageCard(QWidget):
    """单页 A4 纸张卡片，包含页码标签、柔和纸张投影与定位脉冲标记条。"""

    def __init__(self, page_index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page_index = page_index

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 10)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        caption_row = QHBoxLayout()
        caption_row.setContentsMargins(2, 0, 2, 0)
        self.caption_label = QLabel(f"第 {page_index + 1} 页", self)
        self.caption_label.setStyleSheet(
            f"color: {PALETTE['text_secondary']}; font-size: 11.5px; font-weight: 500; background: transparent;"
        )
        caption_row.addWidget(self.caption_label)
        caption_row.addStretch(1)
        layout.addLayout(caption_row)

        self.paper_frame = QFrame(self)
        self.paper_frame.setStyleSheet(
            "QFrame { background-color: #FFFFFF; border: 1px solid #CFD8E3; border-radius: 3px; }"
        )
        shadow = QGraphicsDropShadowEffect(self.paper_frame)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(15, 23, 42, 32))
        self.paper_frame.setGraphicsEffect(shadow)

        paper_layout = QVBoxLayout(self.paper_frame)
        paper_layout.setContentsMargins(0, 0, 0, 0)
        paper_layout.setSpacing(0)

        self.image_label = QLabel("正在载入页面…", self.paper_frame)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet(
            f"background-color: #FFFFFF; color: {PALETTE['text_muted']}; border: none; font-size: 12px;"
        )
        paper_layout.addWidget(self.image_label)
        layout.addWidget(self.paper_frame)

        # 定位标记条（位于纸张左边缘，默认隐藏，定位时显示 1.6 秒）
        self.marker = QFrame(self.paper_frame)
        self.marker.setStyleSheet(
            f"background-color: {PALETTE['accent']}; border: none; border-radius: 2px;"
        )
        self.marker.hide()

    def set_paper_size(self, width: int, height: int) -> None:
        self.paper_frame.setFixedSize(width + 2, height + 2)
        self.image_label.setFixedSize(width, height)

    def set_active_check(self, active: bool) -> None:
        if active:
            self.paper_frame.setStyleSheet(
                f"QFrame {{ background-color: #FFFFFF; border: 2px solid {PALETTE['accent']}; border-radius: 3px; }}"
            )
            self.caption_label.setText(f"第 {self.page_index + 1} 页  ·  正在核对")
            self.caption_label.setStyleSheet(
                f"color: {PALETTE['accent_text']}; font-size: 11.5px; font-weight: 700; background: transparent;"
            )
        else:
            self.paper_frame.setStyleSheet(
                "QFrame { background-color: #FFFFFF; border: 1px solid #CFD8E3; border-radius: 3px; }"
            )
            self.caption_label.setText(f"第 {self.page_index + 1} 页")
            self.caption_label.setStyleSheet(
                f"color: {PALETTE['text_secondary']}; font-size: 11.5px; font-weight: 500; background: transparent;"
            )

    def show_marker(self, local_y: int, height: int) -> None:
        y = max(4, min(self.paper_frame.height() - height - 4, local_y - height // 2))
        self.marker.setGeometry(3, y, 5, height)
        self.marker.raise_()
        self.marker.show()

    def hide_marker(self) -> None:
        self.marker.hide()


class PreviewPanel(QFrame):
    """中栏：融合 Word 风格富文本直编（默认）与精确 A4 分页预览/打印校对的双视图主舞台。"""

    printProofToggled = Signal()
    refreshRequested = Signal()
    viewModeChanged = Signal(str)  # "rich" | "pages"
    statusSummaryChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CenterPanel")

        self.zoom: float = 0.86
        self._fit_enabled: bool = True
        self._view_mode: str = "rich"
        self.preview_pages: list[Path] = []
        self.locators: dict[Hashable, tuple[int, float]] = {}
        self.block_pages: dict[Hashable, int] = {}
        self.actual_pages: int = 1
        self.raw_exam: dict[str, Any] = {}
        self.selected_key: Hashable | None = None
        self.changed_pages: set[int] = set()

        self._page_cards: list[_PageCard] = []
        self._page_sizes: list[tuple[int, int]] = []
        self._page_fingerprints: list[str] = []
        self._visible_pixmaps: dict[int, QPixmap] = {}
        self._active_marker_card: _PageCard | None = None
        self._proof_mode: bool = False

        # 防抖定时器：视口可见页懒加载、窗口缩放适配、定位条自动淡隐
        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.setInterval(20)
        self._visible_timer.timeout.connect(self._load_visible_pages)

        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(120)
        self._fit_timer.timeout.connect(self.fit_width)

        self._marker_timer = QTimer(self)
        self._marker_timer.setSingleShot(True)
        self._marker_timer.setInterval(1600)
        self._marker_timer.timeout.connect(self._clear_marker)

        self._build_ui()

    @property
    def page_frames(self) -> list[_PageCard]:
        """兼容外部诊断与测试对页面卡片集合的访问。"""

        return self._page_cards

    @property
    def preview_image_refs(self) -> list[QPixmap]:
        """当前驻留内存的可见页图像引用列表。"""

        return list(self._visible_pixmaps.values())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 单行精简视图与控制栏
        toolbar = QFrame(self)
        toolbar.setObjectName("PanelHeader")
        t_layout = QHBoxLayout(toolbar)
        t_layout.setContentsMargins(10, 6, 10, 6)
        t_layout.setSpacing(6)

        # 左侧双视图模式分段切换（排版编辑 | 分页预览）
        self.mode_tabs = SegmentedTabs(("排版编辑", "分页预览"), toolbar)
        self.mode_tabs.setFixedWidth(168)
        self.mode_tabs.currentChanged.connect(self._on_mode_tab_changed)
        t_layout.addWidget(self.mode_tabs)

        # 仅在“分页预览”模式显示的翻页与缩放控件容器
        self.pages_controls = QWidget(toolbar)
        pc_layout = QHBoxLayout(self.pages_controls)
        pc_layout.setContentsMargins(6, 0, 0, 0)
        pc_layout.setSpacing(5)

        self.prev_btn = QPushButton(self.pages_controls)
        self.prev_btn.setProperty("variant", "ghost")
        self.prev_btn.setIcon(fluent_icon("chevron_left", PALETTE["text"], 15))
        self.prev_btn.setToolTip("上一页 (PageUp)")
        self.prev_btn.setFixedSize(26, 26)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.clicked.connect(self.previous_page)
        pc_layout.addWidget(self.prev_btn)

        self.page_input = QLineEdit("1", self.pages_controls)
        self.page_input.setFixedSize(40, 26)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.setValidator(QIntValidator(1, 999, self))
        self.page_input.setToolTip("输入页码后按回车跳转")
        self.page_input.returnPressed.connect(self._go_to_entered_page)
        pc_layout.addWidget(self.page_input)

        self.total_pages_label = QLabel("/ 1 页", self.pages_controls)
        self.total_pages_label.setStyleSheet(
            f"QLabel {{ color: {PALETTE['text_secondary']}; font-size: 12px; padding: 0 2px; }}"
        )
        pc_layout.addWidget(self.total_pages_label)

        self.next_btn = QPushButton(self.pages_controls)
        self.next_btn.setProperty("variant", "ghost")
        self.next_btn.setIcon(fluent_icon("chevron_right", PALETTE["text"], 15))
        self.next_btn.setToolTip("下一页 (PageDown)")
        self.next_btn.setFixedSize(26, 26)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(self.next_page)
        pc_layout.addWidget(self.next_btn)

        pc_layout.addSpacing(8)

        zoom_out_btn = QPushButton(self.pages_controls)
        zoom_out_btn.setProperty("variant", "ghost")
        zoom_out_btn.setIcon(fluent_icon("zoom_out", PALETTE["text"], 15))
        zoom_out_btn.setToolTip("缩小 (Ctrl + 向下滚轮)")
        zoom_out_btn.setFixedSize(26, 26)
        zoom_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        zoom_out_btn.clicked.connect(lambda: self.change_zoom(-0.08))
        pc_layout.addWidget(zoom_out_btn)

        self.zoom_btn = QPushButton("86%", self.pages_controls)
        self.zoom_btn.setProperty("variant", "ghost")
        self.zoom_btn.setFixedHeight(26)
        self.zoom_btn.setMinimumWidth(48)
        self.zoom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_btn.setToolTip("选择预设缩放比例")
        self.zoom_btn.clicked.connect(self._show_zoom_menu)
        pc_layout.addWidget(self.zoom_btn)

        zoom_in_btn = QPushButton(self.pages_controls)
        zoom_in_btn.setProperty("variant", "ghost")
        zoom_in_btn.setIcon(fluent_icon("zoom_in", PALETTE["text"], 15))
        zoom_in_btn.setToolTip("放大 (Ctrl + 向上滚轮)")
        zoom_in_btn.setFixedSize(26, 26)
        zoom_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        zoom_in_btn.clicked.connect(lambda: self.change_zoom(0.08))
        pc_layout.addWidget(zoom_in_btn)

        fit_btn = QPushButton("适合宽度", self.pages_controls)
        fit_btn.setProperty("variant", "ghost")
        fit_btn.setIcon(fluent_icon("fit_width", PALETTE["text_secondary"], 14))
        fit_btn.setIconSize(QSize(14, 14))
        fit_btn.setFixedHeight(26)
        fit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fit_btn.clicked.connect(self.fit_width)
        pc_layout.addWidget(fit_btn)

        self.pages_controls.hide()
        t_layout.addWidget(self.pages_controls)

        t_layout.addStretch(1)

        # 轻量当前页数胶囊（仅在需要时展示在视图栏右侧，不额外占一行）
        self.page_status_label = QLabel("1 / 1 页", toolbar)
        self.page_status_label.setObjectName("MutedHint")
        t_layout.addWidget(self.page_status_label)

        # 刷新与打印校对簇
        refresh_btn = QPushButton(toolbar)
        refresh_btn.setProperty("variant", "ghost")
        refresh_btn.setIcon(fluent_icon("refresh", PALETTE["text_secondary"], 14))
        refresh_btn.setToolTip("刷新分页预览 (F5)")
        refresh_btn.setFixedSize(26, 26)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refreshRequested.emit)
        t_layout.addWidget(refresh_btn)

        self.proof_button = QPushButton("打印校对", toolbar)
        self.proof_button.setIcon(fluent_icon("printer", PALETTE["text"], 14))
        self.proof_button.setIconSize(QSize(14, 14))
        self.proof_button.setFixedHeight(27)
        self.proof_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proof_button.setToolTip("调用本机 Word 或 LibreOffice 查看真实打印分页")
        self.proof_button.clicked.connect(self.printProofToggled.emit)
        t_layout.addWidget(self.proof_button)

        root.addWidget(toolbar)

        # 按需浮现的轻量提示条（默认隐藏，仅在页边距核对、打印校对或报错时出现，释放纸面高度）
        self.feedback_bar = QFrame(self)
        self.feedback_bar.setStyleSheet(
            f"QFrame {{ background-color: #F4F8FD; border-bottom: 1px solid {PALETTE['border']}; }}"
        )
        fb_layout = QHBoxLayout(self.feedback_bar)
        fb_layout.setContentsMargins(14, 4, 10, 4)
        fb_layout.setSpacing(8)

        self.update_message_label = QLabel("", self.feedback_bar)
        self.update_message_label.setObjectName("FeedbackInfo")
        fb_layout.addWidget(self.update_message_label, 1)

        dismiss_btn = QPushButton(self.feedback_bar)
        dismiss_btn.setProperty("variant", "ghost")
        dismiss_btn.setIcon(fluent_icon("close", PALETTE["text_muted"], 12))
        dismiss_btn.setIconSize(QSize(12, 12))
        dismiss_btn.setFixedSize(20, 20)
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.clicked.connect(self.feedback_bar.hide)
        fb_layout.addWidget(dismiss_btn)

        self.feedback_bar.hide()
        root.addWidget(self.feedback_bar)

        # 双视图堆叠容器：0 = Word 风格富文本直编画布（默认），1 = 精确 A4 分页图像预览
        self.view_stack = QStackedWidget(self)
        self.view_stack.setStyleSheet("QStackedWidget { background: transparent; }")

        self.rich_editor = RichDocumentEditor(self.view_stack)
        self.rich_editor.zoomChanged.connect(lambda _z: self._update_status(self.current_page()))
        self.view_stack.addWidget(self.rich_editor)

        # A4 连续纸张滚动区域
        self.scroll_area = QScrollArea(self.view_stack)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setStyleSheet(f"QScrollArea {{ background-color: {PALETTE['stage']}; border: none; }}")
        self.scroll_area.viewport().installEventFilter(self)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        self.stage_widget = QWidget(self.scroll_area)
        self.stage_widget.setObjectName("PreviewStage")
        self.stage_widget.setStyleSheet(f"#PreviewStage {{ background-color: {PALETTE['stage']}; }}")
        self.pages_layout = QVBoxLayout(self.stage_widget)
        self.pages_layout.setContentsMargins(20, 14, 20, 28)
        self.pages_layout.setSpacing(6)
        self.pages_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self.empty_placeholder = QLabel("正在准备排版预览…", self.stage_widget)
        self.empty_placeholder.setStyleSheet(
            f"color: {PALETTE['text_muted']}; font-size: 13px; padding: 60px 0;"
        )
        self.pages_layout.addWidget(self.empty_placeholder, alignment=Qt.AlignmentFlag.AlignCenter)

        self.scroll_area.setWidget(self.stage_widget)
        self.view_stack.addWidget(self.scroll_area)
        root.addWidget(self.view_stack, 1)

    @property
    def view_mode(self) -> str:
        return self._view_mode

    def set_view_mode(self, mode: str) -> None:
        target = "pages" if mode == "pages" else "rich"
        idx = 1 if target == "pages" else 0
        if self.mode_tabs.currentIndex() != idx:
            self.mode_tabs.setCurrentIndex(idx)
        else:
            self._apply_view_mode(target)

    def _on_mode_tab_changed(self, index: int) -> None:
        self._apply_view_mode("pages" if index == 1 else "rich")

    def _apply_view_mode(self, mode: str) -> None:
        self._view_mode = mode
        is_pages = mode == "pages"
        self.view_stack.setCurrentIndex(1 if is_pages else 0)
        self.pages_controls.setVisible(is_pages)
        if is_pages:
            self.rich_editor.mini_bar.hide()
            self._visible_timer.start()
        else:
            self.rich_editor.mini_bar.hide()
        self._update_status(self.current_page())
        self.viewModeChanged.emit(mode)

    def set_proof_mode(self, active: bool) -> None:
        self._proof_mode = active
        if active:
            self.set_view_mode("pages")
            self.proof_button.setText("返回快速预览")
            self.proof_button.setProperty("variant", "accent-outline")
            self.proof_button.setIcon(fluent_icon("printer", PALETTE["accent_text"], 14))
        else:
            self.proof_button.setText("打印校对")
            self.proof_button.setProperty("variant", "")
            self.proof_button.setIcon(fluent_icon("printer", PALETTE["text"], 14))
        self.proof_button.style().unpolish(self.proof_button)
        self.proof_button.style().polish(self.proof_button)

    def set_update_message(self, message: str, *, tone: str = "info", show_banner: bool | None = None) -> None:
        self.update_message_label.setText(message)
        color = {
            "info": PALETTE["accent_text"],
            "success": PALETTE["success_text"],
            "warning": PALETTE["warning_text"],
            "error": PALETTE["danger_text"],
        }.get(tone, PALETTE["accent_text"])
        self.update_message_label.setStyleSheet(f"QLabel {{ font-size: 12px; color: {color}; background: transparent; }}")
        should_show = show_banner if show_banner is not None else (tone in {"success", "warning", "error"} and bool(message))
        self.feedback_bar.setVisible(should_show)

    def update_message(self) -> str:
        return self.update_message_label.text()

    def status_text(self) -> str:
        return self.page_status_label.text()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.scroll_area.viewport():
            if event.type() == QEvent.Type.Resize and self._fit_enabled and self._page_sizes:
                self._fit_timer.start()
            elif event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta != 0:
                        self.change_zoom(0.08 if delta > 0 else -0.08)
                        return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_PageUp:
            self.previous_page()
            event.accept()
            return
        if event.key() == Qt.Key.Key_PageDown:
            self.next_page()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_zoom_menu(self) -> None:
        menu = QMenu(self)
        for pct in (60, 75, 90, 100, 115, 130):
            action = QAction(f"{pct}%", menu)
            action.triggered.connect(lambda _checked=False, v=pct / 100.0: self.set_zoom(v))
            menu.addAction(action)
        menu.addSeparator()
        fit_action = QAction("适合页面宽度", menu)
        fit_action.triggered.connect(self.fit_width)
        menu.addAction(fit_action)
        menu.exec(self.zoom_btn.mapToGlobal(self.zoom_btn.rect().bottomLeft()))

    def _go_to_entered_page(self) -> None:
        if not self._page_cards:
            return
        try:
            number = int(self.page_input.text().strip())
        except ValueError:
            self._update_status(self.current_page())
            return
        target = max(0, min(len(self._page_cards) - 1, number - 1))
        self._scroll_page(target)
        self.page_input.clearFocus()

    def fit_width(self) -> None:
        if self._view_mode == "rich":
            self.rich_editor.fit_width()
            self._update_status(self.current_page())
            return
        self._fit_enabled = True
        if not self._page_sizes:
            return
        viewport_w = self.scroll_area.viewport().width()
        if viewport_w <= 60:
            return
        target_zoom = max(0.30, min(1.45, (viewport_w - 56) / max(1, self._page_sizes[0][0])))
        if abs(self.zoom - target_zoom) > 0.008:
            self.zoom = target_zoom
            self._resize_display()
        else:
            self._update_status(self.current_page())

    def set_zoom(self, value: float) -> None:
        if self._view_mode == "rich":
            self.rich_editor.set_zoom(value)
            self._update_status(self.current_page())
            return
        self._fit_enabled = False
        self.zoom = max(0.30, min(1.50, value))
        if self._page_cards:
            self._resize_display()

    def change_zoom(self, delta: float) -> None:
        if self._view_mode == "rich":
            self.rich_editor.change_zoom(delta)
            self._update_status(self.current_page())
            return
        self.set_zoom(self.zoom + delta)

    def previous_page(self) -> None:
        if self._page_cards:
            self._scroll_page(max(0, self.current_page() - 1))

    def next_page(self) -> None:
        if self._page_cards:
            self._scroll_page(min(len(self._page_cards) - 1, self.current_page() + 1))

    def current_page(self) -> int:
        if not self._page_cards:
            return 0
        vbar = self.scroll_area.verticalScrollBar()
        middle = vbar.value() + self.scroll_area.viewport().height() * 0.45
        for index, card in enumerate(self._page_cards):
            if card.y() + card.height() >= middle:
                return index
        return len(self._page_cards) - 1

    def _capture_page_position(self) -> tuple[int, float] | None:
        if not self._page_cards:
            return None
        index = self.current_page()
        card = self._page_cards[index]
        top = self.scroll_area.verticalScrollBar().value()
        relative = (top - card.y()) / max(1, card.height())
        return index, relative

    def _restore_page_position(self, position: tuple[int, float]) -> None:
        if not self._page_cards:
            return
        index = max(0, min(position[0], len(self._page_cards) - 1))
        card = self._page_cards[index]
        target_y = int(round(card.y() + position[1] * max(1, card.height())))
        self.scroll_area.verticalScrollBar().setValue(max(0, target_y))

    def _scroll_page(self, page_index: int) -> None:
        if not self._page_cards:
            return
        page_index = max(0, min(page_index, len(self._page_cards) - 1))
        card = self._page_cards[page_index]
        self.scroll_area.verticalScrollBar().setValue(max(0, card.y() - 6))
        self._update_status(page_index)
        self._visible_timer.start()

    def _resize_display(self) -> None:
        anchor = self._capture_page_position()
        self._visible_pixmaps.clear()
        for card, (width, height) in zip(self._page_cards, self._page_sizes):
            card.image_label.clear()
            card.set_paper_size(max(1, round(width * self.zoom)), max(1, round(height * self.zoom)))
        self.stage_widget.adjustSize()
        if anchor is not None:
            QTimer.singleShot(0, lambda pos=anchor: self._restore_page_position(pos))
        self._update_status(self.current_page())
        self._visible_timer.start()

    def _on_scrolled(self, _value: int) -> None:
        self._update_status(self.current_page())
        self._visible_timer.start()

    def _load_visible_pages(self) -> None:
        if not self._page_cards or not self.preview_pages:
            return
        vbar = self.scroll_area.verticalScrollBar()
        top = vbar.value()
        bottom = top + max(1, self.scroll_area.viewport().height())

        visible = [
            index
            for index, card in enumerate(self._page_cards)
            if card.y() <= bottom and (card.y() + card.height()) >= top
        ]
        if not visible:
            visible = [self.current_page()]
        keep = set(range(max(0, min(visible) - 1), min(len(self._page_cards), max(visible) + 2)))

        for index in list(self._visible_pixmaps):
            if index not in keep:
                self._page_cards[index].image_label.clear()
                del self._visible_pixmaps[index]

        dpr = max(1.0, float(self.devicePixelRatioF()))
        for index in sorted(keep):
            if index in self._visible_pixmaps:
                continue
            path = self.preview_pages[index]
            if not path.is_file():
                self._page_cards[index].image_label.setText("页面已过期，请刷新预览")
                continue
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                self._page_cards[index].image_label.setText("无法载入页面图像")
                continue
            orig_w, orig_h = self._page_sizes[index]
            logical_w = max(1, round(orig_w * self.zoom))
            logical_h = max(1, round(orig_h * self.zoom))
            scaled = pixmap.scaled(
                max(1, round(logical_w * dpr)),
                max(1, round(logical_h * dpr)),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._visible_pixmaps[index] = scaled
            self._page_cards[index].image_label.setPixmap(scaled)

    def set_preview_pages(
        self,
        pages: list[Path | str] | tuple[Path | str, ...],
        *,
        locators: Mapping[Hashable, tuple[int, float]] | None = None,
        actual_pages: int | None = None,
        raw_exam: dict[str, Any] | None = None,
        selected_key: Hashable | None = None,
        page_info: tuple[tuple[int, int, str], ...] = (),
    ) -> bool:
        """载入渲染结果，按图像指纹增量判断变化并保持当前阅读位置。"""

        previous_view = self._capture_page_position()
        paths = [Path(page) for page in pages]
        sizes: list[tuple[int, int]] = []
        fingerprints: list[str] = []

        try:
            for index, path in enumerate(paths):
                if page_info and len(page_info) == len(paths):
                    if not path.is_file():
                        raise FileNotFoundError(path)
                    width, height, digest = page_info[index]
                    sizes.append((int(width), int(height)))
                    fingerprints.append(str(digest))
                else:
                    payload = path.read_bytes()
                    fingerprints.append(sha256(payload).hexdigest())
                    reader = QImageReader(str(path))
                    size = reader.size()
                    if not size.isValid():
                        raise OSError(f"无法读取图像尺寸：{path.name}")
                    sizes.append((size.width(), size.height()))
        except OSError as exc:
            detail = exc.strerror or str(exc)
            name = path.name if "path" in locals() else ""
            self.set_update_message(
                f"预览图片“{name}”无法读取：{detail}。请点击“应用并查看”重新生成预览。",
                tone="error",
            )
            return False

        self.changed_pages = {
            index
            for index, value in enumerate(fingerprints)
            if index >= len(self._page_fingerprints) or value != self._page_fingerprints[index]
        }

        # 如果所有页指纹和页数均无变化，直接复用现有页面卡片
        if self._page_cards and not self.changed_pages and len(paths) == len(self._page_cards):
            self.preview_pages = paths
            self.locators = dict(locators or {})
            self.block_pages = {key: int(val[0]) for key, val in self.locators.items()}
            self.actual_pages = actual_pages or len(paths)
            if raw_exam is not None:
                self.raw_exam = raw_exam
            self._update_status(self.current_page())
            return True

        self._page_fingerprints = fingerprints
        self._clear_marker()
        self.preview_pages = paths
        self._page_sizes = sizes
        self.locators = dict(locators or {})
        self.actual_pages = actual_pages or len(paths)
        if raw_exam is not None:
            self.raw_exam = raw_exam

        if self._fit_enabled and self._page_sizes and self.scroll_area.viewport().width() > 60:
            self.zoom = max(
                0.30,
                min(1.45, (self.scroll_area.viewport().width() - 56) / max(1, self._page_sizes[0][0])),
            )

        # 清空旧卡片并按新页数重建
        self._visible_pixmaps.clear()
        for card in self._page_cards:
            self.pages_layout.removeWidget(card)
            card.deleteLater()
        self._page_cards.clear()
        self.block_pages.clear()
        self.empty_placeholder.setVisible(not paths)

        for index, (width, height) in enumerate(self._page_sizes):
            card = _PageCard(index, self.stage_widget)
            card.set_paper_size(max(1, round(width * self.zoom)), max(1, round(height * self.zoom)))
            self.pages_layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._page_cards.append(card)

        if self._page_cards:
            for key, location in self.locators.items():
                page_idx = max(0, min(int(location[0]), len(self._page_cards) - 1))
                self.block_pages[key] = page_idx

        self.stage_widget.adjustSize()
        index = min(previous_view[0], len(paths) - 1) if previous_view and paths else 0
        self._update_status(index)
        if previous_view is not None and paths:
            QTimer.singleShot(0, lambda pos=previous_view: self._restore_page_position(pos))
        self._load_visible_pages()
        return True

    def show_page_update(self, page_index: int, message: str) -> None:
        """定位到正在核对的目标页并以清蓝描边高亮。"""

        if not self._page_cards:
            self.set_update_message(message, tone="info")
            return
        self.set_view_mode("pages")
        page_index = max(0, min(page_index, len(self._page_cards) - 1))
        for index, card in enumerate(self._page_cards):
            card.set_active_check(index == page_index)
        self._scroll_page(page_index)
        tone = "success" if page_index in self.changed_pages else "info"
        self.set_update_message(message, tone=tone, show_banner=True)

    def scroll_to(self, key: Hashable, *, highlight: bool = True) -> None:
        """根据语义锚点同步滚动富文本纸面与分页预览到对应内容对象。"""

        self.selected_key = key
        if isinstance(key, (str, int)):
            self.rich_editor.scroll_to_key(key, highlight=highlight)

        locator = self.locators.get(key)
        if locator is None or not self._page_cards:
            return
        page_index = max(0, min(int(locator[0]), len(self._page_cards) - 1))
        vertical = max(0.0, min(1.0, float(locator[1])))
        card = self._page_cards[page_index]
        paper_h = max(1, card.paper_frame.height())
        local_y = int(paper_h * (0.075 + vertical * 0.84))
        target_y = card.y() + card.paper_frame.y() + local_y - int(self.scroll_area.viewport().height() * 0.42)
        self.scroll_area.verticalScrollBar().setValue(max(0, target_y))
        self._update_status(page_index)
        if self._view_mode == "pages":
            self._visible_timer.start()
            if highlight:
                self._clear_marker()
                marker_h = max(24, int(30 * self.zoom))
                card.show_marker(local_y, marker_h)
                self._active_marker_card = card
                self._marker_timer.start()

    def _clear_marker(self) -> None:
        self._marker_timer.stop()
        if self._active_marker_card is not None:
            try:
                self._active_marker_card.hide_marker()
            except RuntimeError:
                pass
            self._active_marker_card = None

    def _update_status(self, page_index: int) -> None:
        total = max(1, len(self._page_cards))
        current = max(1, min(total, page_index + 1))
        pct = round(self.zoom * 100)
        self.total_pages_label.setText(f"/ {total} 页")
        self.zoom_btn.setText(f"{pct}%")
        if self._view_mode == "rich":
            summary = f"约 {current} / {total} 页"
        else:
            summary = f"{current} / {total} 页 · {pct}%"
        self.page_status_label.setText(summary)
        self.page_status_label.setVisible(self._view_mode == "rich")
        self.statusSummaryChanged.emit(summary)
        if not self.page_input.hasFocus():
            self.page_input.setText(str(current))
        self.prev_btn.setEnabled(current > 1)
        self.next_btn.setEnabled(current < total)


__all__ = ["PreviewPanel"]
