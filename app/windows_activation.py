"""Windows-like active and inactive window palettes."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any


ACTIVE = {
    "root": "#F3F3F3",
    "toolbar": "#F7F7F7",
    "status": "#E6F2FF",
    "status_text": "#004578",
    "attribution_text": "#3B5266",
    "selection": "#CCE8FF",
}
INACTIVE = {
    "root": "#F7F7F7",
    "toolbar": "#F1F1F1",
    "status": "#EEEEEE",
    "status_text": "#666666",
    "attribution_text": "#777777",
    "selection": "#E4E4E4",
}


def install_activation_palette(window: tk.Tk) -> None:
    """Bind native activation events and apply matching ttk colors."""

    window.bind("<Activate>", lambda _event: apply_activation_palette(window, True), add="+")
    window.bind(
        "<Deactivate>",
        lambda _event: apply_activation_palette(window, False),
        add="+",
    )
    window.after_idle(lambda: apply_activation_palette(window, True))


def apply_activation_palette(window: tk.Tk, active: bool) -> None:
    """Apply a restrained Windows 11 active or inactive palette."""

    palette = ACTIVE if active else INACTIVE
    window.configure(background=palette["root"])
    style = ttk.Style(window)
    style.configure("TFrame", background=palette["root"])
    style.configure("Toolbar.TFrame", background=palette["toolbar"])
    style.configure("Toolbar.TLabel", background=palette["toolbar"])
    style.configure("Title.TLabel", background=palette["toolbar"])
    style.configure(
        "Prominent.Status.TFrame",
        background=palette["status"],
    )
    style.configure(
        "Prominent.Status.TLabel",
        background=palette["status"],
        foreground=palette["status_text"],
    )
    style.configure(
        "Prominent.Attribution.TLabel",
        background=palette["status"],
        foreground=palette["attribution_text"],
    )
    style.map(
        "Treeview",
        background=[("selected", palette["selection"])],
        foreground=[("selected", "#0F0F0F" if active else "#555555")],
    )


def remove_duplicate_update_button(window: Any) -> int:
    """Hide toolbar update buttons while preserving the Help menu item."""

    removed = 0
    for widget in _walk_widgets(window):
        if not isinstance(widget, ttk.Button):
            continue
        if str(widget.cget("text")).strip() != "检查更新":
            continue
        manager = widget.winfo_manager()
        if manager == "pack":
            widget.pack_forget()
        elif manager == "grid":
            widget.grid_remove()
        elif manager == "place":
            widget.place_forget()
        removed += 1
    return removed


def _walk_widgets(widget: Any) -> list[Any]:
    result: list[Any] = []
    for child in widget.winfo_children():
        result.append(child)
        result.extend(_walk_widgets(child))
    return result


__all__ = [
    "ACTIVE",
    "INACTIVE",
    "apply_activation_palette",
    "install_activation_palette",
    "remove_duplicate_update_button",
]
