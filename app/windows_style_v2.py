"""Stable Windows-inspired ttk style without global font parsing errors."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


def apply_windows_style_v2(window: tk.Tk) -> None:
    """Configure Windows-like colors and fonts using named Tk fonts."""

    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    for font_name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    ):
        try:
            tkfont.nametofont(font_name).configure(family="Segoe UI", size=10)
        except tk.TclError:
            continue

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


__all__ = ["apply_windows_style_v2"]
