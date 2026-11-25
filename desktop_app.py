"""高中语文试卷智能排版工作台正式桌面入口（PySide6 Fluent 版）。"""

from __future__ import annotations

from app.current_importer import import_exam
from app.export_naming import typeset_name
from app.layout_presets import DEFAULT_PRESET, PRESET_LABELS
from app.version import APP_TITLE, APP_VERSION


def _load_ui_module():
    """延迟导入 PySide6 主窗口模块，确保纯文本/命令行工具导入本文件时不触发 GUI 初始化。"""

    from app.ui import main_window

    return main_window


def __getattr__(name: str):
    if name in {"CurrentDesktopApp", "create_application", "main", "resource_path"}:
        return getattr(_load_ui_module(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "APP_TITLE",
    "APP_VERSION",
    "CurrentDesktopApp",
    "DEFAULT_PRESET",
    "PRESET_LABELS",
    "create_application",
    "import_exam",
    "main",
    "typeset_name",
]


if __name__ == "__main__":
    raise SystemExit(_load_ui_module().main())
