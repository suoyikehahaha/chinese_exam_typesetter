"""Final v0.8.4 entry with a visible native paned workspace."""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import desktop_app as base
from desktop_app_v082_final import import_exam
from desktop_app_v082_release import build_documents_v82
from desktop_app_v084_production import ProductionDesktopApp as ProductionDesktopAppV084


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.4"
base.build_documents = build_documents_v82


class ProductionDesktopApp(ProductionDesktopAppV084):
    """Polish child widgets while preserving the TPanedwindow layout."""

    def _polish_widgets(self, widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            if isinstance(child, ttk.Panedwindow):
                for pane in child.winfo_children():
                    if isinstance(pane, ttk.Frame):
                        pane.configure(style="Panel.TFrame")
            elif isinstance(child, ttk.LabelFrame):
                child.configure(style="Card.TLabelframe")
            elif isinstance(child, tk.Text):
                child.configure(
                    background="#FFFFFF",
                    foreground="#1F1F1F",
                    insertbackground="#1F1F1F",
                    selectbackground="#0078D4",
                    selectforeground="#FFFFFF",
                    relief=tk.FLAT,
                    highlightthickness=1,
                    highlightbackground="#D7D7D7",
                    highlightcolor="#0067C0",
                    padx=10,
                    pady=9,
                    font=("SimSun", 10),
                )
            elif isinstance(child, tk.Canvas):
                child.configure(background="#E7E7E7", highlightthickness=0)
            self._polish_widgets(child)


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
