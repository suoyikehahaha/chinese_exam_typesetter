# -*- mode: python ; coding: utf-8 -*-
"""Production one-file Windows v0.4.4 build."""

from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).resolve()
python_root = Path(sys.base_prefix).resolve()
dll_root = python_root / "DLLs"
tcl_root = python_root / "tcl"
tcl_data = tcl_root / "tcl8.6"
tk_data = tcl_root / "tk8.6"

required = (
    project_root / "templates", project_root / "samples", project_root / "assets",
    dll_root / "_tkinter.pyd", dll_root / "tcl86t.dll", dll_root / "tk86t.dll",
    tcl_data / "init.tcl", tk_data / "tk.tcl",
)
missing = [str(path) for path in required if not path.exists()]
if missing:
    raise SystemExit("Tcl/Tk build resources are missing:\n" + "\n".join(missing))

os.environ["TCL_LIBRARY"] = str(tcl_data)
os.environ["TK_LIBRARY"] = str(tk_data)

datas = [
    (str(project_root / "templates"), "templates"),
    (str(project_root / "samples"), "samples"),
    (str(project_root / "assets"), "assets"),
    (str(tcl_data), "_tcl_data"),
    (str(tk_data), "_tk_data"),
]
binaries = [
    (str(dll_root / "_tkinter.pyd"), "."),
    (str(dll_root / "tcl86t.dll"), "."),
    (str(dll_root / "tk86t.dll"), "."),
]
hiddenimports = [
    "desktop_app_v044", "desktop_app_v041", "desktop_app_v04_final",
    "desktop_app_v04_stable", "desktop_app_v04_release", "desktop_app_v04",
    "desktop_app_v03", "desktop_app_current_v01",
    "app.current_importer_v14", "app.flexible_importers_v13", "app.windows_drop_v02",
    "app.inspector_model_v04", "app.read_only_preview_v04", "app.current_pipeline_v04",
    "app.page_layout_v04", "app.chinese_line_break_v042", "app.font_resolver_v043",
    "app.internal_preview_v041", "app.internal_preview_v043", "app.preview_service_v043",
    *collect_submodules("tkinter"),
]

a = Analysis(
    [str(project_root / "windows_launcher_v044_final.py")],
    pathex=[str(project_root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="ChineseExamTypesetter_0.4.4", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None,
    icon=[str(project_root / "assets" / "app-icon-v1.ico")],
)
