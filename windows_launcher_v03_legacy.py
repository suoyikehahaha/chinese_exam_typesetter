"""Windows launcher for the version 0.3 editable A4 workbench."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from windows_launcher import _install_startup_fix, _set_tk_data_paths


def main() -> int:
    """Launch v0.3 after applying the frozen-runtime Tk initialization fix."""

    _set_tk_data_paths()
    current_v02 = importlib.import_module("desktop_app_current_v01")
    _install_startup_fix(current_v02)
    module = importlib.import_module("desktop_app_v03")
    if "--import-test" in sys.argv:
        index = sys.argv.index("--import-test")
        data = module.current_v02.import_exam(Path(sys.argv[index + 1]))
        if sys.stdout is not None:
            print(
                {
                    "questions": [
                        block["question"]["number"]
                        for block in data.get("blocks", [])
                        if block.get("type") == "question"
                    ]
                }
            )
        return 0
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
