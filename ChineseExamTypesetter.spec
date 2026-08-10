# -*- mode: python ; coding: utf-8 -*-
"""Production one-file Windows build."""

from pathlib import Path
import os
import sys

from PyInstaller.building.datastruct import Tree
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
]
binaries = [
    (str(dll_root / "_tkinter.pyd"), "."),
    (str(dll_root / "tcl86t.dll"), "."),
    (str(dll_root / "tk86t.dll"), "."),
]
hiddenimports = [
    "desktop_app",
    "desktop_workbench_base", "app.current_importer", "app.flexible_importers", "app.windows_drop",
    "app.github_update_page",
    "app.inspector_model", "app.read_only_preview", "app.current_pipeline",
    "app.page_layout", "app.chinese_line_break", "app.font_resolver",
    "app.internal_preview_core", "app.internal_preview_native", "app.internal_preview", "app.preview_service",
    *collect_submodules("tkinter"),
]

a = Analysis(
    [str(project_root / "windows_launcher.py")],
    pathex=[str(project_root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False, optimize=0,
)
# Add Tcl/Tk as explicit DATA TOC entries after Analysis has normalized the
# ordinary source/data pairs.  Passing Tree entries through the `datas=`
# argument is rejected by recent PyInstaller versions because that argument
# accepts two-tuples; `a.datas` is the correct three-tuple TOC boundary.
a.datas.extend(Tree(str(tcl_data), prefix="_tcl_data"))
a.datas.extend(Tree(str(tk_data), prefix="_tk_data"))
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="ChineseExamTypesetter_0.1.0", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, contents_directory=".",
    icon=[str(project_root / "assets" / "app-icon.ico")],
)
