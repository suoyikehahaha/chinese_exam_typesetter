"""Validated Windows launcher for the v0.3 Tcl/Tk bundled build."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

from windows_launcher import _install_startup_fix


def _set_tk_data_paths() -> None:
    """Use absolute Tcl/Tk data paths inside the PyInstaller extraction root."""

    if not getattr(sys, "frozen", False):
        return
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    tcl_data = root / "_tcl_data"
    tk_data = root / "_tk_data"
    if not (tcl_data / "init.tcl").is_file():
        raise RuntimeError(f"Tcl 运行数据不完整：{tcl_data}")
    if not (tk_data / "tk.tcl").is_file():
        raise RuntimeError(f"Tk 运行数据不完整：{tk_data}")
    os.chdir(root)
    os.environ["TCL_LIBRARY"] = str(tcl_data)
    os.environ["TK_LIBRARY"] = str(tk_data)


def _load_application() -> object:
    current_v02 = importlib.import_module("desktop_app_current_v01")
    _install_startup_fix(current_v02)
    return importlib.import_module("desktop_app_v03")


def _run_import_test(module: object, source: Path, output: Path | None) -> int:
    data = module.current_v02.import_exam(source)
    result = {
        "source": source.name,
        "blocks": len(data.get("blocks", [])),
        "questions": [
            block["question"]["number"]
            for block in data.get("blocks", [])
            if block.get("type") == "question"
        ],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    elif sys.stdout is not None:
        print(result)
    return 0


def _run_startup_test(module: object, output: Path) -> int:
    app = module.CurrentDesktopApp()

    def finish() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("GUI_STARTUP_OK", encoding="utf-8")
        app._on_close()

    app.after(1800, finish)
    app.mainloop()
    return 0


def main() -> int:
    """Launch the app or execute a packaged-runtime self-check."""

    _set_tk_data_paths()
    module = _load_application()
    if "--import-test" in sys.argv:
        index = sys.argv.index("--import-test")
        output = None
        if "--test-output" in sys.argv:
            output = Path(sys.argv[sys.argv.index("--test-output") + 1])
        return _run_import_test(module, Path(sys.argv[index + 1]), output)
    if "--startup-test-output" in sys.argv:
        index = sys.argv.index("--startup-test-output")
        return _run_startup_test(module, Path(sys.argv[index + 1]))
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
