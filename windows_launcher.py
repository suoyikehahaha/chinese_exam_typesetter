"""Windows launcher for the current Word-only desktop workbench."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _set_tk_data_paths() -> None:
    """Use relative Tcl/Tk paths for the bundled Python runtime."""

    if not getattr(sys, "frozen", False):
        return
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    os.chdir(root)
    os.environ["TCL_LIBRARY"] = "_tcl_data"
    os.environ["TK_LIBRARY"] = "_tk_data"


def _install_startup_fix(module: object) -> None:
    """Create the target-page variable after Tk has created the root window."""

    import tkinter as tk

    cls = module.CurrentDesktopApp
    base_init = cls.__mro__[1].__init__
    original_build_ui = cls._build_ui

    def fixed_build_ui(self: object) -> None:
        if getattr(self, "target_pages_var", None) is None:
            self.target_pages_var = tk.StringVar(master=self, value="8")
        original_build_ui(self)

    def fixed_init(self: object) -> None:
        self.target_pages_var = None
        base_init(self)

    cls._build_ui = fixed_build_ui
    cls.__init__ = fixed_init


def main() -> int:
    """Launch the workbench or run the lightweight import self-test."""

    _set_tk_data_paths()
    module = importlib.import_module("desktop_app_current_v01")
    _install_startup_fix(module)
    if "--import-test" in sys.argv:
        index = sys.argv.index("--import-test")
        data = module.import_exam(Path(sys.argv[index + 1]))
        if sys.stdout is not None:
            print({"questions": [
                block["question"]["number"]
                for block in data.get("blocks", [])
                if block.get("type") == "question"
            ]})
        return 0
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
