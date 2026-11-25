# -*- mode: python ; coding: utf-8 -*-
"""Production one-file Windows build (PySide6 Fluent edition)."""

from pathlib import Path
import runpy


project_root = Path(SPECPATH).resolve()
app_version = runpy.run_path(str(project_root / "app" / "version.py"))["APP_VERSION"]

required = (
    project_root / "templates",
    project_root / "samples",
    project_root / "assets",
)
missing = [str(path) for path in required if not path.exists()]
if missing:
    raise SystemExit("Build resources are missing:\n" + "\n".join(missing))

datas = [
    (str(project_root / "templates"), "templates"),
    (str(project_root / "samples"), "samples"),
    (str(project_root / "assets"), "assets"),
]

hiddenimports = [
    "desktop_app",
    "app.current_importer",
    "app.flexible_importers",
    "app.inspector_model",
    "app.current_pipeline",
    "app.page_layout",
    "app.chinese_line_break",
    "app.font_resolver",
    "app.internal_preview_core",
    "app.internal_preview_native",
    "app.internal_preview",
    "app.preview_service",
    "app.ui",
    "app.ui.theme",
    "app.ui.icons",
    "app.ui.widgets",
    "app.ui.outline_panel",
    "app.ui.rich_document_editor",
    "app.ui.preview_panel",
    "app.ui.inspector_panel",
    "app.ui.dialogs",
    "app.ui.editing_actions",
    "app.ui.session_actions",
    "app.ui.main_window",
]

excludes = [
    "tkinter",
    "_tkinter",
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSql",
    "PySide6.QtNetwork",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtPdf",
    "PySide6.QtSvg",
    "PySide6.QtTest",
]

a = Analysis(
    [str(project_root / "windows_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"ChineseExamTypesetter_{app_version}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
    icon=[str(project_root / "assets" / "app-icon.ico")],
)
