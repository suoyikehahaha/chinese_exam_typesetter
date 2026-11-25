"""微软 Fluent 风格业务弹窗（导出前提醒、GitHub 更新说明、关于与许可）。"""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Iterable
import webbrowser

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..github_updater import GITHUB_REPOSITORY, fetch_release_metadata
from ..version import APP_TITLE, APP_VERSION
from .icons import fluent_icon, fluent_pixmap
from .theme import PALETTE, apply_windows_chrome
from .widgets import StatusPill


class ExportWarningDialog(QDialog):
    """导出 DOCX 前的非阻断检查确认弹窗。"""

    def __init__(self, parent: QWidget | None, lines: Iterable[str]) -> None:
        super().__init__(parent)
        self.result_confirmed = False
        self.setWindowTitle("导出前提醒")
        self.setModal(True)
        self.setFixedWidth(480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(10)
        icon_lbl = QLabel(self)
        icon_lbl.setPixmap(fluent_pixmap("warning", PALETTE["warning_text"], 22, stroke=1.8))
        head.addWidget(icon_lbl)
        title = QLabel("当前试卷还有需要核对的项目", self)
        title.setStyleSheet(f"font-size: 14.5px; font-weight: 700; color: {PALETTE['warning_text']};")
        head.addWidget(title, 1)
        root.addLayout(head)

        box = QFrame(self)
        box.setStyleSheet(
            f"QFrame {{ background-color: {PALETTE['warning_bg']}; "
            f"border: 1px solid {PALETTE['warning_border']}; border-radius: 8px; }}"
        )
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(14, 12, 14, 12)
        box_layout.setSpacing(6)
        for line in lines:
            row = QLabel(f"•  {line}", box)
            row.setWordWrap(True)
            row.setStyleSheet(f"color: {PALETTE['text']}; font-size: 12.5px; background: transparent; border: none;")
            box_layout.addWidget(row)
        root.addWidget(box)

        tip = QLabel("这些提醒不会阻止导出，你也可以返回大纲核对后再导出正式 Word 文件。", self)
        tip.setObjectName("MutedHint")
        tip.setWordWrap(True)
        root.addWidget(tip)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        back_btn = QPushButton("返回检查", self)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.reject)
        buttons.addWidget(back_btn)

        go_btn = QPushButton("继续导出", self)
        go_btn.setProperty("variant", "primary")
        go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        go_btn.clicked.connect(self._confirm)
        buttons.addWidget(go_btn)
        root.addLayout(buttons)

        apply_windows_chrome(self)

    def _confirm(self) -> None:
        self.result_confirmed = True
        self.accept()


class _ReleaseFetcherSignals(QObject):
    loaded = Signal(dict)
    failed = Signal(str)


class GitHubUpdateDialog(QDialog):
    """内置 GitHub 最新发布说明弹窗，纯异步加载不阻塞界面。"""

    def __init__(self, parent: QWidget | None, current_version: str) -> None:
        super().__init__(parent)
        self.current_version = current_version
        self._signals = _ReleaseFetcherSignals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._signals.failed.connect(self._on_failed)

        self.setWindowTitle("GitHub 更新页面")
        self.resize(700, 520)
        self.setMinimumSize(560, 400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("GitHub 最新发布", self)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        top.addWidget(title)
        top.addWidget(StatusPill(f"当前 v{current_version}", "info", self))
        top.addStretch(1)
        root.addLayout(top)

        repo_lbl = QLabel(f"官方仓库：{GITHUB_REPOSITORY}", self)
        repo_lbl.setObjectName("MutedHint")
        repo_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(repo_lbl)

        self.status_label = QLabel("正在读取 GitHub 最新发布…", self)
        self.status_label.setObjectName("FeedbackInfo")
        root.addWidget(self.status_label)

        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {PALETTE['surface']}; border: 1px solid {PALETTE['border']}; "
            "border-radius: 8px; padding: 10px; font-size: 12.5px; }}"
        )
        root.addWidget(self.details, 1)

        buttons = QHBoxLayout()
        refresh_btn = QPushButton("刷新", self)
        refresh_btn.setIcon(fluent_icon("refresh", PALETTE["text"], 14))
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh)
        buttons.addWidget(refresh_btn)

        open_btn = QPushButton("打开 GitHub 仓库", self)
        open_btn.setProperty("variant", "accent-outline")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: webbrowser.open(GITHUB_REPOSITORY))
        buttons.addWidget(open_btn)

        buttons.addStretch(1)
        close_btn = QPushButton("关闭", self)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        apply_windows_chrome(self)
        self._refresh()

    def _refresh(self) -> None:
        self.status_label.setText("正在读取 GitHub 最新发布…")
        self.details.setPlainText("正在连接 GitHub，请稍候…")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self) -> None:
        try:
            payload = fetch_release_metadata()
            self._signals.loaded.emit(payload if isinstance(payload, dict) else {})
        except Exception as exc:  # noqa: BLE001
            self._signals.failed.emit(str(exc))

    def _on_loaded(self, data: dict) -> None:
        tag = str(data.get("tag_name", "暂无发布版本"))
        name = str(data.get("name", ""))
        published = str(data.get("published_at", ""))
        notes = str(data.get("body", "")).strip() or "暂无更新说明。"
        assets = data.get("assets", [])
        asset_lines = []
        if isinstance(assets, list):
            for asset in assets:
                if isinstance(asset, dict):
                    asset_lines.append(
                        f"• {asset.get('name', '')}\n  {asset.get('browser_download_url', '')}"
                    )
        text = (
            f"最新版本：{tag}\n"
            f"发布名称：{name or tag}\n"
            f"发布时间：{published or '未提供'}\n"
            f"当前版本：v{self.current_version}\n\n"
            "更新说明\n"
            "────────────────────────\n"
            f"{notes}\n\n"
            "发布文件\n"
            "────────────────────────\n"
            f"{chr(10).join(asset_lines) if asset_lines else '暂无附件，请打开 GitHub 仓库查看。'}"
        )
        self.status_label.setText("GitHub 发布信息已更新")
        self.details.setPlainText(text)

    def _on_failed(self, error: str) -> None:
        self.status_label.setText("暂时无法连接 GitHub")
        self.details.setPlainText(
            "暂时无法读取最新发布信息。\n\n"
            f"错误详情：{error}\n\n"
            "你仍可以点击“打开 GitHub 仓库”查看源码和发布页面。"
        )


class AboutDialog(QDialog):
    """简洁现代的“关于与使用许可”弹窗。"""

    def __init__(self, parent: QWidget | None, icon_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于与使用许可")
        self.setModal(True)
        self.setFixedWidth(440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(14)
        if icon_path and icon_path.is_file():
            logo = QLabel(self)
            logo.setPixmap(
                QPixmap(str(icon_path)).scaled(
                    56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
            head.addWidget(logo)

        info = QVBoxLayout()
        info.setSpacing(4)
        title = QLabel(APP_TITLE, self)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        info.addWidget(title)
        info.addWidget(StatusPill(f"版本 v{APP_VERSION}  ·  本地排版处理", "info", self))
        head.addLayout(info, 1)
        root.addLayout(head)

        card = QFrame(self)
        card.setObjectName("InspectorCard")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(6)
        desc = QLabel(
            "本软件为本人（公众号：蓑衣微言）面向高中语文试题排版研发。\n\n"
            "未经本人书面许可，不得销售、出租、收费分发、嵌入商业服务或用于其他营利活动。",
            card,
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 12.5px;")
        c_layout.addWidget(desc)
        root.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QPushButton("知道了", self)
        ok_btn.setProperty("variant", "primary")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        apply_windows_chrome(self)


__all__ = ["AboutDialog", "ExportWarningDialog", "GitHubUpdateDialog"]
