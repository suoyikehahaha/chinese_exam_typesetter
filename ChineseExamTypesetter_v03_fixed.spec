# -*- mode: python ; coding: utf-8 -*-
"""Stable one-file Windows build with explicit Tcl/Tk resources."""

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
    project_root / "templates",
    project_root / "samples",
    project_root / "assets",
    dll_root / "_tkinter.pyd",
    dll_root / "tcl86t.dll",
    dll_root / "tk86t.dll",
    tcl_data / "init.tcl",
    tk_data / "tk.tcl",
)
missing = [str(path) for path in required if not path.exists()]
if missing:
    raise SystemExit("Tcl/Tk build resources are missing:\n" + "\n".join(missing))

# Let PyInstaller's Tcl/Tk hook inspect the same runtime used by the build.
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
    "desktop_app_v03",
    "desktop_app_current_v01",
    "app.editable_a4_canvas_v03",
    "app.score_summary_v03",
    *collect_submodules("tkinter"),
]

a = Analysis(
    [str(project_root / "windows_launcher_v03_fixed.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="ChineseExamTypesetter_0.3_fixed",
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
    icon=[str(project_root / "assets" / "app-icon-v1.ico")],
)
