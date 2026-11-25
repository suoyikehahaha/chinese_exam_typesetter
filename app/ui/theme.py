"""微软 Fluent 清蓝设计系统令牌、全局 QSS 样式表与 Windows 11 窗口视觉适配。"""

from __future__ import annotations

import ctypes
import os
from typing import Any

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget


# 微软 Fluent 亮色清蓝设计令牌（浅云灰画布 + 纯白卡片 + Fluent 清蓝主色）
PALETTE: dict[str, str] = {
    "canvas": "#F3F5F9",
    "stage": "#E8EDF4",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "surface_hover": "#F1F5F9",
    "surface_pressed": "#E5EDF6",
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "accent": "#0067C0",
    "accent_hover": "#1076CC",
    "accent_pressed": "#00529B",
    "accent_soft": "#E8F3FC",
    "accent_soft_border": "#B8D8F8",
    "accent_text": "#004B8D",
    "text": "#1A222E",
    "text_secondary": "#526071",
    "text_muted": "#7B8898",
    "success_bg": "#EBF7EE",
    "success_border": "#B7DFC1",
    "success_text": "#0F7B0F",
    "warning_bg": "#FFF6E5",
    "warning_border": "#F5D598",
    "warning_text": "#9A5B00",
    "danger_bg": "#FDECEC",
    "danger_border": "#F4BDBD",
    "danger_text": "#C42B1C",
}

ACTIVE_PALETTE = {
    "root": PALETTE["canvas"],
    "toolbar": PALETTE["surface"],
    "status": PALETTE["accent_soft"],
    "status_text": PALETTE["accent_text"],
    "selection": PALETTE["accent_soft"],
}

INACTIVE_PALETTE = {
    "root": "#F6F7F9",
    "toolbar": "#FAFAFC",
    "status": "#EEF1F5",
    "status_text": "#64748B",
    "selection": "#E6ECF3",
}


def build_stylesheet() -> str:
    """生成全局统一的 Fluent 清蓝样式表。"""

    p = PALETTE
    return f"""
    * {{
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        outline: none;
    }}

    QMainWindow, QDialog {{
        background-color: {p["canvas"]};
        color: {p["text"]};
    }}

    QWidget {{
        color: {p["text"]};
        font-size: 13px;
    }}

    /* 菜单栏：轻量通透 */
    QMenuBar {{
        background-color: {p["surface"]};
        border-bottom: 1px solid {p["border"]};
        padding: 2px 10px;
        spacing: 2px;
        font-size: 12.5px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 5px 10px;
        border-radius: 5px;
        color: {p["text_secondary"]};
    }}
    QMenuBar::item:selected {{
        background-color: {p["surface_hover"]};
        color: {p["text"]};
    }}
    QMenu {{
        background-color: {p["surface"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 8px;
        padding: 5px;
    }}
    QMenu::item {{
        padding: 6px 26px 6px 12px;
        border-radius: 5px;
        color: {p["text"]};
    }}
    QMenu::item:selected {{
        background-color: {p["accent_soft"]};
        color: {p["accent_text"]};
    }}
    QMenu::item:disabled {{
        color: {p["text_muted"]};
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {p["border"]};
        margin: 4px 8px;
    }}

    /* 顶部集成工具栏 */
    #TopHeaderBar {{
        background-color: {p["surface"]};
        border-bottom: 1px solid {p["border"]};
    }}
    #BrandTitle {{
        font-size: 15px;
        font-weight: 700;
        color: {p["text"]};
    }}
    #BrandVersion {{
        font-size: 11px;
        font-weight: 600;
        color: {p["accent_text"]};
        background-color: {p["accent_soft"]};
        border-radius: 9px;
        padding: 2px 8px;
    }}
    #FileStatusBadge {{
        font-size: 12px;
        color: {p["text_secondary"]};
        background-color: {p["surface_alt"]};
        border: 1px solid {p["border"]};
        border-radius: 6px;
        padding: 4px 10px;
    }}
    #FileStatusBadge[dirty="true"] {{
        color: {p["accent_text"]};
        background-color: {p["accent_soft"]};
        border: 1px solid {p["accent_soft_border"]};
    }}

    /* 主三栏卡片与分割条 */
    QSplitter::handle {{
        background-color: transparent;
        width: 6px;
        height: 6px;
    }}
    QSplitter::handle:hover {{
        background-color: {p["accent_soft_border"]};
        border-radius: 3px;
    }}

    #LeftPanel, #CenterPanel, #RightPanel {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 10px;
    }}
    #CenterPanel {{
        background-color: {p["stage"]};
    }}

    #PanelHeader {{
        background-color: {p["surface"]};
        border-bottom: 1px solid {p["border"]};
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
    }}
    #PanelTitle {{
        font-size: 13.5px;
        font-weight: 700;
        color: {p["text"]};
    }}
    #SectionSubtitle {{
        font-size: 11.5px;
        font-weight: 600;
        color: {p["text_secondary"]};
    }}
    #MutedHint {{
        font-size: 11.5px;
        color: {p["text_muted"]};
    }}
    #FeedbackInfo {{
        font-size: 12px;
        color: {p["accent_text"]};
    }}
    #ErrorHint {{
        font-size: 11.5px;
        color: {p["danger_text"]};
    }}

    /* 按钮层级：主按钮 / 次主描边 / 常规按钮 / 幽灵图标按钮 / 切换按钮 */
    QPushButton, QToolButton {{
        background-color: {p["surface"]};
        color: {p["text"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 6px;
        padding: 5px 12px;
        font-size: 12.5px;
        font-weight: 500;
    }}
    QPushButton:hover, QToolButton:hover {{
        background-color: {p["surface_hover"]};
        border-color: #B6C4D6;
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background-color: {p["surface_pressed"]};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        background-color: {p["surface_alt"]};
        color: {p["text_muted"]};
        border-color: {p["border"]};
    }}

    QPushButton[variant="primary"] {{
        background-color: {p["accent"]};
        color: #FFFFFF;
        border: 1px solid {p["accent"]};
        font-weight: 600;
        padding: 6px 15px;
    }}
    QPushButton[variant="primary"]:hover {{
        background-color: {p["accent_hover"]};
        border-color: {p["accent_hover"]};
    }}
    QPushButton[variant="primary"]:pressed {{
        background-color: {p["accent_pressed"]};
        border-color: {p["accent_pressed"]};
    }}
    QPushButton[variant="primary"]:disabled {{
        background-color: #9CC0E3;
        border-color: #9CC0E3;
        color: #F4F8FC;
    }}

    QPushButton[variant="accent-outline"] {{
        background-color: {p["accent_soft"]};
        color: {p["accent_text"]};
        border: 1px solid {p["accent_soft_border"]};
        font-weight: 600;
        padding: 6px 13px;
    }}
    QPushButton[variant="accent-outline"]:hover {{
        background-color: #DCECFB;
        border-color: #8FC0EF;
    }}
    QPushButton[variant="accent-outline"]:pressed {{
        background-color: #CCE4F9;
    }}

    QPushButton[variant="danger-soft"] {{
        background-color: {p["danger_bg"]};
        color: {p["danger_text"]};
        border: 1px solid {p["danger_border"]};
        font-weight: 600;
    }}
    QPushButton[variant="danger-soft"]:hover {{
        background-color: #FBD9D9;
    }}

    QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{
        background-color: transparent;
        border: 1px solid transparent;
        color: {p["text_secondary"]};
        padding: 5px 8px;
    }}
    QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{
        background-color: {p["surface_hover"]};
        color: {p["text"]};
        border-color: transparent;
    }}
    QPushButton[variant="ghost"]:pressed, QToolButton[variant="ghost"]:pressed {{
        background-color: {p["surface_pressed"]};
    }}
    QPushButton[variant="ghost"]:checked, QToolButton[variant="ghost"]:checked {{
        background-color: {p["accent_soft"]};
        color: {p["accent_text"]};
        border: 1px solid {p["accent_soft_border"]};
    }}

    QPushButton[variant="chip"] {{
        background-color: {p["surface_alt"]};
        color: {p["text_secondary"]};
        border: 1px solid {p["border"]};
        border-radius: 5px;
        padding: 3px 9px;
        font-size: 11.5px;
    }}
    QPushButton[variant="chip"]:hover {{
        background-color: {p["accent_soft"]};
        color: {p["accent_text"]};
        border-color: {p["accent_soft_border"]};
    }}
    QPushButton[variant="chip"]:checked {{
        background-color: {p["accent_soft"]};
        color: {p["accent_text"]};
        border-color: {p["accent"]};
        font-weight: 600;
    }}

    /* 分段切换器 */
    #SegmentedContainer {{
        background-color: {p["canvas"]};
        border: 1px solid {p["border"]};
        border-radius: 8px;
        padding: 3px;
    }}
    QPushButton[variant="segment"] {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        color: {p["text_secondary"]};
        font-size: 12.5px;
        font-weight: 500;
        padding: 5px 10px;
    }}
    QPushButton[variant="segment"]:hover {{
        color: {p["text"]};
        background-color: rgba(255, 255, 255, 0.55);
    }}
    QPushButton[variant="segment"]:checked {{
        background-color: {p["surface"]};
        color: {p["accent_text"]};
        border: 1px solid {p["border"]};
        font-weight: 700;
    }}
    QPushButton[variant="segment"]:disabled {{
        color: #B4C0CE;
        background-color: transparent;
        border-color: transparent;
    }}

    /* 折叠分组卡片 */
    #InspectorCard {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 8px;
    }}
    #InspectorCardHeader {{
        background-color: {p["surface_alt"]};
        border-top-left-radius: 7px;
        border-top-right-radius: 7px;
        border-bottom: 1px solid {p["border"]};
    }}
    #InspectorCardHeader[collapsed="true"] {{
        border-radius: 7px;
        border-bottom: none;
    }}
    #InspectorCardTitle {{
        font-size: 12.5px;
        font-weight: 700;
        color: {p["text"]};
    }}

    /* 输入控件 */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background-color: {p["surface"]};
        color: {p["text"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 6px;
        padding: 4px 8px;
        selection-background-color: {p["accent"]};
        selection-color: #FFFFFF;
    }}
    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
    QPlainTextEdit:hover, QTextEdit:hover {{
        border-color: #9FB4CC;
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1.5px solid {p["accent"]};
        background-color: {p["surface"]};
    }}
    QLineEdit[invalid="true"], QDoubleSpinBox[invalid="true"] {{
        border: 1.5px solid {p["danger_text"]};
        background-color: {p["danger_bg"]};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
    QPlainTextEdit:disabled, QTextEdit:disabled {{
        background-color: {p["surface_alt"]};
        color: {p["text_muted"]};
        border-color: {p["border"]};
    }}

    QComboBox {{
        padding: 3px 22px 3px 8px;
    }}
    QComboBox QLineEdit {{
        border: none;
        padding: 0px 2px;
        margin: 0px;
        background: transparent;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 22px;
        border: none;
        background: transparent;
    }}
    QComboBox::down-arrow {{
        image: none;
        width: 0px;
        height: 0px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {p["surface"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 7px;
        padding: 4px;
        selection-background-color: {p["accent_soft"]};
        selection-color: {p["accent_text"]};
        outline: none;
    }}

    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 18px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: {p["surface_hover"]};
        border-radius: 3px;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: none;
        width: 0px;
        height: 0px;
    }}

    /* 树形大纲与内容对象列表 */
    QTreeWidget, QListWidget {{
        background-color: {p["surface"]};
        border: none;
        outline: none;
        alternate-background-color: {p["surface_alt"]};
    }}
    QTreeWidget::branch:hover {{
        background-color: {p["surface_hover"]};
    }}
    QTreeWidget::branch:selected {{
        background-color: {p["accent_soft"]};
    }}
    QTreeWidget::item {{
        height: 30px;
        padding: 2px 4px;
        border-radius: 5px;
    }}
    QTreeWidget::item:hover {{
        background-color: {p["surface_hover"]};
    }}
    QTreeWidget::item:selected {{
        background-color: {p["accent_soft"]};
        color: {p["accent_text"]};
    }}
    QHeaderView::section {{
        background-color: {p["surface_alt"]};
        color: {p["text_secondary"]};
        border: none;
        border-bottom: 1px solid {p["border"]};
        padding: 5px 8px;
        font-size: 11.5px;
        font-weight: 600;
    }}

    QListWidget#ObjectList {{
        border: 1px solid {p["border"]};
        border-radius: 7px;
        padding: 2px;
    }}
    QListWidget#ObjectList::item {{
        padding: 5px 8px;
        border-radius: 5px;
        margin: 1px 0px;
    }}
    QListWidget#ObjectList::item:hover {{
        background-color: {p["surface_hover"]};
    }}
    QListWidget#ObjectList::item:selected {{
        background-color: {p["accent_soft"]};
        color: {p["accent_text"]};
        font-weight: 600;
    }}

    /* 复选框 */
    QCheckBox {{
        spacing: 7px;
        color: {p["text"]};
        font-size: 12.5px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1.5px solid {p["border_strong"]};
        background-color: {p["surface"]};
    }}
    QCheckBox::indicator:hover {{
        border-color: {p["accent"]};
    }}
    QCheckBox::indicator:checked {{
        background-color: {p["accent"]};
        border-color: {p["accent"]};
    }}

    /* 现代细长滚动条 */
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 9px;
        margin: 2px 1px;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(100, 116, 139, 0.30);
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: rgba(71, 85, 105, 0.55);
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 9px;
        margin: 1px 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: rgba(100, 116, 139, 0.30);
        border-radius: 4px;
        min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: rgba(71, 85, 105, 0.55);
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
        width: 0px;
    }}

    /* 进度条与底部状态栏 */
    QProgressBar {{
        background-color: {p["border"]};
        border: none;
        border-radius: 2px;
        max-height: 4px;
        min-height: 4px;
    }}
    QProgressBar::chunk {{
        background-color: {p["accent"]};
        border-radius: 2px;
    }}

    #BottomStatusBar {{
        background-color: {p["surface"]};
        border-top: 1px solid {p["border"]};
    }}
    #BottomStatusText {{
        font-size: 11.5px;
        color: {p["text_secondary"]};
    }}
    #BottomAttribution {{
        font-size: 11.5px;
        color: {p["text_muted"]};
    }}

    /* 提示气泡 */
    QToolTip {{
        background-color: #1E293B;
        color: #FFFFFF;
        border: none;
        border-radius: 6px;
        padding: 5px 9px;
        font-size: 11.5px;
    }}
    """


def apply_fluent_theme(app: QApplication) -> None:
    """在 QApplication 上统一应用微软 Fluent 清蓝主题与默认字体。"""

    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    app.setStyleSheet(build_stylesheet())


def apply_windows_chrome(window: QWidget) -> None:
    """在 Windows 11 上启用原生窗口圆角与浅色标题栏融色。"""

    if os.name != "nt":
        return
    try:
        hwnd = int(window.winId())
        dwmapi = ctypes.windll.dwmapi
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
        corner = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        # DWMWA_CAPTION_COLOR = 35，BGR 格式 #FFFFFF -> 0x00FFFFFF
        caption = ctypes.c_int(0x00FFFFFF)
        dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        # DWMWA_BORDER_COLOR = 34，BGR 格式 #E2E8F0 -> 0x00F0E8E2
        border = ctypes.c_int(0x00F0E8E2)
        dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
    except Exception:
        return


__all__ = [
    "ACTIVE_PALETTE",
    "INACTIVE_PALETTE",
    "PALETTE",
    "apply_fluent_theme",
    "apply_windows_chrome",
    "build_stylesheet",
]
