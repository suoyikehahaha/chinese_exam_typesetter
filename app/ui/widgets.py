"""微软 Fluent 风格通用自定义基础组件。"""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .icons import fluent_icon, fluent_pixmap
from .theme import PALETTE


class FluentComboBox(QComboBox):
    """带清晰自绘下拉箭头且屏蔽非聚焦滚轮误改的下拉框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(30)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = PALETTE["text_muted"] if not self.isEnabled() else PALETTE["text_secondary"]
        pen = QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx = self.width() - 13.0
        cy = self.height() / 2.0
        painter.drawLine(QPointF(cx - 3.5, cy - 1.5), QPointF(cx, cy + 2.0))
        painter.drawLine(QPointF(cx, cy + 2.0), QPointF(cx + 3.5, cy - 1.5))
        painter.end()


class FluentDoubleSpinBox(QDoubleSpinBox):
    """带清晰上下微调箭头、支持失焦滚轮穿透与校验态高亮的数值框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(30)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = PALETTE["text_muted"] if not self.isEnabled() else PALETTE["text_secondary"]
        pen = QPen(QColor(color), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx = self.width() - 10.5
        mid = self.height() / 2.0
        # 上箭头
        painter.drawLine(QPointF(cx - 2.8, mid - 2.4), QPointF(cx, mid - 5.2))
        painter.drawLine(QPointF(cx, mid - 5.2), QPointF(cx + 2.8, mid - 2.4))
        # 下箭头
        painter.drawLine(QPointF(cx - 2.8, mid + 2.4), QPointF(cx, mid + 5.2))
        painter.drawLine(QPointF(cx, mid + 5.2), QPointF(cx + 2.8, mid + 2.4))
        painter.end()


class FluentCheckBox(QCheckBox):
    """带清晰白勾符号的 Fluent 风格复选框。"""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.isChecked():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#FFFFFF"), 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        # 复选框指示器位于左侧居中，宽高 16x16
        top = (self.height() - 16.0) / 2.0
        painter.drawLine(QPointF(4.2, top + 8.4), QPointF(7.2, top + 11.4))
        painter.drawLine(QPointF(7.2, top + 11.4), QPointF(12.2, top + 5.0))
        painter.end()


class SegmentedTabs(QFrame):
    """Windows 11 分段切换控件（用于属性检查器的“内容 / 格式 / 整卷”）。"""

    currentChanged = Signal(int)

    def __init__(self, labels: Iterable[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedContainer")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(3, 3, 3, 3)
        self._layout.setSpacing(3)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        self._current_index = 0

        for idx, text in enumerate(labels):
            button = QPushButton(text, self)
            button.setProperty("variant", "segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._group.addButton(button, idx)
            self._layout.addWidget(button)
            self._buttons.append(button)

        if self._buttons:
            self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self._on_clicked)

    def _on_clicked(self, index: int) -> None:
        if index != self._current_index:
            self._current_index = index
            self.currentChanged.emit(index)

    def currentIndex(self) -> int:
        return self._current_index

    def setCurrentIndex(self, index: int) -> None:
        if not (0 <= index < len(self._buttons)):
            return
        if not self._buttons[index].isEnabled():
            return
        changed = index != self._current_index
        self._current_index = index
        self._buttons[index].setChecked(True)
        if changed:
            self.currentChanged.emit(index)

    def setTabEnabled(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setEnabled(enabled)

    def isTabEnabled(self, index: int) -> bool:
        return 0 <= index < len(self._buttons) and self._buttons[index].isEnabled()

    def tabText(self, index: int) -> str:
        return self._buttons[index].text() if 0 <= index < len(self._buttons) else ""

    def count(self) -> int:
        return len(self._buttons)


class CollapsibleCard(QFrame):
    """可折叠的 Fluent 属性分组卡片，支持右上角“恢复模板”幽灵按钮。"""

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = True,
        reset_callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorCard")
        self.title = title
        self.expanded = expanded

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QFrame(self)
        self.header.setObjectName("InspectorCardHeader")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.installEventFilter(self)

        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(10, 7, 8, 7)
        header_layout.setSpacing(6)

        self.arrow_label = QLabel(self.header)
        self.arrow_label.setFixedSize(16, 16)
        header_layout.addWidget(self.arrow_label)

        self.title_label = QLabel(title, self.header)
        self.title_label.setObjectName("InspectorCardTitle")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)

        self.reset_button: QPushButton | None = None
        if reset_callback is not None:
            btn = QPushButton("恢复模板", self.header)
            btn.setProperty("variant", "ghost")
            btn.setIcon(fluent_icon("reset", PALETTE["text_secondary"], 13))
            btn.setIconSize(QSize(13, 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("padding: 2px 7px; font-size: 11.5px;")
            btn.clicked.connect(reset_callback)
            header_layout.addWidget(btn)
            self.reset_button = btn

        root.addWidget(self.header)

        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 10, 12, 12)
        self.body_layout.setSpacing(8)
        root.addWidget(self.body)

        self.set_expanded(expanded)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.header and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self.toggle()
                return True
        return super().eventFilter(watched, event)

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = bool(expanded)
        self.body.setVisible(self.expanded)
        icon_name = "chevron_down" if self.expanded else "chevron_right"
        self.arrow_label.setPixmap(fluent_pixmap(icon_name, PALETTE["text_secondary"], 14))
        self.header.setProperty("collapsed", "false" if self.expanded else "true")
        self.header.style().unpolish(self.header)
        self.header.style().polish(self.header)


class StatusPill(QLabel):
    """圆角语义状态徽章。"""

    _STYLES = {
        "neutral": (PALETTE["surface_alt"], PALETTE["border"], PALETTE["text_secondary"]),
        "info": (PALETTE["accent_soft"], PALETTE["accent_soft_border"], PALETTE["accent_text"]),
        "success": (PALETTE["success_bg"], PALETTE["success_border"], PALETTE["success_text"]),
        "warning": (PALETTE["warning_bg"], PALETTE["warning_border"], PALETTE["warning_text"]),
        "danger": (PALETTE["danger_bg"], PALETTE["danger_border"], PALETTE["danger_text"]),
    }

    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        bg, border, fg = self._STYLES.get(tone, self._STYLES["neutral"])
        self.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {border}; color: {fg}; "
            "border-radius: 10px; padding: 2px 9px; font-size: 11.5px; font-weight: 600;"
        )


class InlineResultBanner(QFrame):
    """导出完成后显示的绿色内嵌轻横幅，提供一键打开文件与打开文件夹。"""

    openFileRequested = Signal()
    openFolderRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {PALETTE['success_bg']}; "
            f"border-bottom: 1px solid {PALETTE['success_border']}; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 12, 7)
        layout.setSpacing(8)

        icon_label = QLabel(self)
        icon_label.setPixmap(fluent_pixmap("check", PALETTE["success_text"], 15, stroke=2.0))
        layout.addWidget(icon_label)

        self.message_label = QLabel("", self)
        self.message_label.setStyleSheet(
            f"color: {PALETTE['success_text']}; font-size: 12.5px; font-weight: 600; background: transparent; border: none;"
        )
        layout.addWidget(self.message_label, 1)

        open_file_btn = QPushButton("打开文件", self)
        open_file_btn.setIcon(fluent_icon("open_file", PALETTE["success_text"], 14))
        open_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_file_btn.setStyleSheet(
            f"background: #FFFFFF; color: {PALETTE['success_text']}; "
            f"border: 1px solid {PALETTE['success_border']}; border-radius: 5px; padding: 4px 10px; font-weight: 600;"
        )
        open_file_btn.clicked.connect(self.openFileRequested.emit)
        layout.addWidget(open_file_btn)

        open_folder_btn = QPushButton("打开文件夹", self)
        open_folder_btn.setIcon(fluent_icon("folder", PALETTE["success_text"], 14))
        open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder_btn.setStyleSheet(
            f"background: transparent; color: {PALETTE['success_text']}; "
            f"border: 1px solid {PALETTE['success_border']}; border-radius: 5px; padding: 4px 10px;"
        )
        open_folder_btn.clicked.connect(self.openFolderRequested.emit)
        layout.addWidget(open_folder_btn)

        close_btn = QPushButton(self)
        close_btn.setProperty("variant", "ghost")
        close_btn.setIcon(fluent_icon("close", PALETTE["success_text"], 13))
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        layout.addWidget(close_btn)

        self.hide()

    def show_message(self, text: str) -> None:
        self.message_label.setText(text)
        self.show()


class DropOverlay(QWidget):
    """拖拽 Word 文件到主窗口上方时的半透明清蓝提示遮罩。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(232, 243, 252, 220))

        margin = 24
        box = QRectF(margin, margin, max(40, self.width() - margin * 2), max(40, self.height() - margin * 2))
        pen = QPen(QColor(PALETTE["accent"]), 2.2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 255, 255, 190))
        painter.drawRoundedRect(box, 16, 16)

        font = self.font()
        font.setPointSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(PALETTE["accent_text"]))
        painter.drawText(box.adjusted(0, -18, 0, 0), Qt.AlignmentFlag.AlignCenter, "松开鼠标即可导入 Word 试题")

        sub_font = self.font()
        sub_font.setPointSize(10)
        sub_font.setBold(False)
        painter.setFont(sub_font)
        painter.setPen(QColor(PALETTE["text_secondary"]))
        painter.drawText(box.adjusted(0, 26, 0, 0), Qt.AlignmentFlag.AlignCenter, "支持 .docx 与 .doc 格式，自动识别整卷、限时训练或参考答案")
        painter.end()


__all__ = [
    "CollapsibleCard",
    "DropOverlay",
    "FluentCheckBox",
    "FluentComboBox",
    "FluentDoubleSpinBox",
    "InlineResultBanner",
    "SegmentedTabs",
    "StatusPill",
]
