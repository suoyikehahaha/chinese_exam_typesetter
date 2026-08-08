"""Production v0.8.4 with a stable single-window startup."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tkinter as tk

import desktop_app as base
from app.windows_style_v2 import apply_windows_style_v2
from desktop_app_v082_final import import_exam
from desktop_app_v082_release import build_documents_v82
from desktop_app_v083_production import ProductionDesktopApp as ProductionDesktopAppV083


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.4"
base.build_documents = build_documents_v82


def resource_path(relative: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative


class ProductionDesktopApp(ProductionDesktopAppV083):
    """Use one Tk root and a safely configured Windows visual theme."""

    def _setup_styles(self) -> None:
        apply_windows_style_v2(self)

    def __init__(self) -> None:
        super().__init__()
        self._set_application_icon()

    def _set_application_icon(self) -> None:
        icon = resource_path("assets/app-icon-256-v1.png")
        if not icon.exists():
            return
        try:
            self._app_icon = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self._app_icon)
        except tk.TclError:
            return


def run_cli() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", metavar="OUTPUT")
    parser.add_argument("--import-test", metavar="FILE")
    args, _ = parser.parse_known_args()
    if args.self_test:
        return base.self_test(Path(args.self_test))
    if args.import_test:
        data = import_exam(Path(args.import_test))
        print(
            [
                block["question"]["number"]
                for block in data["blocks"]
                if block.get("type") == "question"
            ]
        )
        return 0
    ProductionDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
