"""Windows 11 inspired presentation layer for workbench v0.8.2."""

from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import desktop_app as base
from desktop_app_v082_final import (
    AdaptiveDesktopApp as AdaptiveDesktopAppBase,
    build_documents_v82,
    import_exam,
)


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.2"
base.build_documents = build_documents_v82


class WindowsDesktopApp(AdaptiveDesktopAppBase):
    """Keep the workflow familiar while applying a calm Windows visual system."""

    def __init__(self) -> None:
        super().__init__()
        self.geometry("1500x900")
        self.minsize(1180, 720)
        self.configure(background="#F3F3F3")
        self._polish_widgets(self)
        self._enable_windows_rounding()

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.option_add("*Font", "Segoe UI 10")
        self.option_add("*Menu.Font", "Segoe UI 10")

        background = "#F3F3F3"
        card = "#FFFFFF"
        text = "#1F1F1F"
        muted = "#5D5D5D"
        border = "#D7D7D7"
        accent = "#0067C0"

        style.configure(
            ".",
            font=("Segoe UI", 10),
            background=background,
            foreground=text,
        )
        style.configure("TFrame", background=background)
        style.configure("Toolbar.TFrame", background="#F7F7F7")
        style.configure(
            "Panel.TFrame",
            background=card,
            borderwidth=1,
            relief="solid",
            bordercolor=border,
        )
        style.configure("Card.TFrame", background=card)
        style.configure("TLabel", background=background, foreground=text)
        style.configure("Toolbar.TLabel", background="#F7F7F7", foreground=text)
        style.configure(
            "Title.TLabel",
            background="#F7F7F7",
            foreground=text,
            font=("Segoe UI", 15, "bold"),
        )
        style.configure(
            "DialogTitle.TLabel",
            background=background,
            foreground=text,
            font=("Segoe UI", 14, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background="#F7F7F7",
            foreground=muted,
        )
        style.configure(
            "TButton",
            font=("Segoe UI", 10),
            padding=(12, 7),
            background="#FBFBFB",
            foreground=text,
            bordercolor=border,
            lightcolor="#FBFBFB",
            darkcolor="#FBFBFB",
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("pressed", "#E5E5E5"), ("active", "#F0F0F0")],
            bordercolor=[("focus", "#8A8A8A"), ("active", "#C7C7C7")],
        )
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(14, 7),
            background=accent,
            foreground="#FFFFFF",
            bordercolor=accent,
            lightcolor=accent,
            darkcolor=accent,
        )
        style.map(
            "Primary.TButton",
            background=[("pressed", "#004F91"), ("active", "#1975C5")],
            foreground=[("disabled", "#DADADA"), ("!disabled", "#FFFFFF")],
        )
        style.configure(
            "Treeview",
            font=("Segoe UI", 10),
            rowheight=31,
            background=card,
            fieldbackground=card,
            foreground=text,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.map(
            "Treeview",
            background=[("selected", "#CCE8FF")],
            foreground=[("selected", "#0F0F0F")],
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            padding=(8, 7),
            background="#FAFAFA",
            foreground=text,
            bordercolor=border,
        )
        style.configure(
            "TNotebook",
            background=background,
            borderwidth=0,
            tabmargins=(0, 5, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            font=("Segoe UI", 10),
            padding=(16, 9),
            background="#EAEAEA",
            foreground=muted,
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", card), ("active", "#F6F6F6")],
            foreground=[("selected", text)],
        )
        style.configure(
            "TEntry",
            padding=(8, 6),
            fieldbackground=card,
            foreground=text,
            bordercolor=border,
        )
        style.configure(
            "TCombobox",
            padding=(8, 6),
            fieldbackground=card,
            foreground=text,
            bordercolor=border,
            arrowcolor=muted,
        )
        style.configure(
            "TCheckbutton",
            background=background,
            foreground=text,
            padding=(2, 3),
        )
        style.configure(
            "Card.TLabelframe",
            background=card,
            bordercolor=border,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=card,
            foreground=text,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TProgressbar",
            background=accent,
            troughcolor="#E5E5E5",
            bordercolor="#E5E5E5",
            lightcolor=accent,
            darkcolor=accent,
        )

    def _polish_widgets(self, widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            if isinstance(child, ttk.Panedwindow):
                child.configure(style="TFrame")
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

    def _enable_windows_rounding(self) -> None:
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            preference = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                33,
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except Exception:
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
    WindowsDesktopApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
