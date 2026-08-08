"""Production Windows launcher for the final v0.4.1 release."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

from windows_launcher import _install_startup_fix


def _set_tk_data_paths() -> None:
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
    current = importlib.import_module("desktop_app_current_v01")
    _install_startup_fix(current)
    return importlib.import_module("desktop_app_v041_final")


def _run_import_test(module: object, source: Path, output: Path) -> int:
    data = module.v041.v04.stable.release.current_v02.import_exam(source)
    result = {
        "source": source.name,
        "blocks": len(data.get("blocks", [])),
        "questions": [
            block["question"]["number"]
            for block in data.get("blocks", [])
            if block.get("type") == "question"
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def _run_startup_test(module: object, output: Path) -> int:
    app = module.CurrentDesktopApp()

    def finish() -> None:
        roots = app.tree.get_children("")
        payload = {
            "status": "GUI_STARTUP_OK",
            "version": module.APP_VERSION,
            "tree": bool(roots),
            "preview_service": type(app._preview_service).__module__,
            "preview": type(app.document_editor).__name__,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        app._on_close()

    app.after(1800, finish)
    app.mainloop()
    return 0


def main() -> int:
    _set_tk_data_paths()
    module = _load_application()
    if "--import-test" in sys.argv:
        source = Path(sys.argv[sys.argv.index("--import-test") + 1])
        output = Path(sys.argv[sys.argv.index("--test-output") + 1])
        return _run_import_test(module, source, output)
    if "--startup-test-output" in sys.argv:
        output = Path(sys.argv[sys.argv.index("--startup-test-output") + 1])
        return _run_startup_test(module, output)
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
