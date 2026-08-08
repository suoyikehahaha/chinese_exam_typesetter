"""Reliable native Windows file dropping for a Tk top-level window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Callable


WM_DROPFILES = 0x0233
GWL_WNDPROC = -4
GA_ROOT = 2


def install_file_drop(widget: object, callback: Callable[[tuple[Path, ...]], None]) -> bool:
    if os.name != "nt" or not hasattr(widget, "winfo_id"):
        return False
    try:
        widget.update_idletasks()
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        client = int(widget.winfo_id())
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = int(user32.GetAncestor(client, GA_ROOT) or client)
        long_ptr = ctypes.c_ssize_t
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        call_proc = user32.CallWindowProcW
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = long_ptr
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
        set_long.restype = long_ptr
        call_proc.argtypes = [long_ptr, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        call_proc.restype = long_ptr
        original = int(get_long(hwnd, GWL_WNDPROC))
        if not original:
            return False
        callback_type = ctypes.WINFUNCTYPE(
            long_ptr,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        def wnd_proc(window: int, message: int, wparam: int, lparam: int) -> int:
            if message == WM_DROPFILES:
                paths = _read_drop_paths(wparam)
                shell32.DragFinish(wparam)
                if paths:
                    widget.after(0, lambda values=paths: callback(values))
                return 0
            return int(call_proc(original, window, message, wparam, lparam))

        proc = callback_type(wnd_proc)
        proc_address = int(ctypes.cast(proc, ctypes.c_void_p).value or 0)
        if not proc_address:
            return False
        set_long(hwnd, GWL_WNDPROC, proc_address)
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragAcceptFiles(hwnd, True)
        setattr(widget, "_exam_drop_state_v02", (hwnd, original, proc, shell32, user32))
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def uninstall_file_drop(widget: object) -> None:
    state = getattr(widget, "_exam_drop_state_v02", None)
    if not state:
        return
    hwnd, original, _proc, shell32, user32 = state
    try:
        shell32.DragAcceptFiles(hwnd, False)
        user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, original)
    except (AttributeError, OSError):
        pass
    try:
        delattr(widget, "_exam_drop_state_v02")
    except AttributeError:
        pass


def _read_drop_paths(handle: int) -> tuple[Path, ...]:
    shell32 = ctypes.windll.shell32
    shell32.DragQueryFileW.argtypes = [wintypes.HDROP, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    count = int(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0))
    values: list[Path] = []
    for index in range(count):
        length = int(shell32.DragQueryFileW(handle, index, None, 0))
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(handle, index, buffer, length + 1)
        values.append(Path(buffer.value))
    return tuple(values)


__all__ = ["install_file_drop", "uninstall_file_drop"]
