"""Small native Windows file-drop adapter for a Tk top-level window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Callable, Iterable


WM_DROPFILES = 0x0233
GWL_WNDPROC = -4


def install_file_drop(widget: object, callback: Callable[[tuple[Path, ...]], None]) -> bool:
    """Accept files dropped on a Tk window without an extra GUI dependency."""

    if os.name != "nt" or not hasattr(widget, "winfo_id"):
        return False
    try:
        hwnd = int(widget.winfo_id())
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        pointer_type = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        call_proc = user32.CallWindowProcW
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = pointer_type
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, pointer_type]
        set_long.restype = pointer_type
        call_proc.argtypes = [pointer_type, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        call_proc.restype = pointer_type
        original = get_long(hwnd, GWL_WNDPROC)
        callback_type = ctypes.WINFUNCTYPE(pointer_type, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        def wnd_proc(window: int, message: int, wparam: int, lparam: int) -> int:
            if message == WM_DROPFILES:
                paths = _read_drop_paths(wparam)
                if paths:
                    widget.after(0, lambda values=paths: callback(values))
                shell32.DragFinish(wparam)
                return 0
            return call_proc(original, window, message, wparam, lparam)

        proc = callback_type(wnd_proc)
        set_long(hwnd, GWL_WNDPROC, ctypes.cast(proc, pointer_type).value)
        shell32.DragAcceptFiles(hwnd, True)
        setattr(widget, "_exam_drop_state", (hwnd, original, proc, shell32, user32))
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def uninstall_file_drop(widget: object) -> None:
    """Restore the original Tk window procedure."""

    state = getattr(widget, "_exam_drop_state", None)
    if not state:
        return
    hwnd, original, _proc, shell32, user32 = state
    try:
        shell32.DragAcceptFiles(hwnd, False)
        user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, original)
    except (AttributeError, OSError):
        pass
    try:
        delattr(widget, "_exam_drop_state")
    except AttributeError:
        pass


def _read_drop_paths(handle: int) -> tuple[Path, ...]:
    shell32 = ctypes.windll.shell32
    shell32.DragQueryFileW.argtypes = [wintypes.HDROP, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
    values: list[Path] = []
    for index in range(count):
        length = shell32.DragQueryFileW(handle, index, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(handle, index, buffer, length + 1)
        values.append(Path(buffer.value))
    return tuple(values)


__all__ = ["install_file_drop", "uninstall_file_drop"]
