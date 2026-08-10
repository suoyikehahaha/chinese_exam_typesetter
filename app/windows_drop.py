"""Native Windows file dropping through the safe window-subclass API."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections import deque
import os
from pathlib import Path
from threading import Lock
from typing import Callable


WM_DROPFILES = 0x0233
WM_COPYGLOBALDATA = 0x0049
GA_ROOT = 2
MSGFLT_ALLOW = 1


def install_file_drop(widget: object, callback: Callable[[tuple[Path, ...]], None]) -> bool:
    if os.name != "nt" or not hasattr(widget, "winfo_id"):
        return False
    try:
        widget.update_idletasks()
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        comctl32 = ctypes.windll.comctl32
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        shell32.DragFinish.restype = None
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragAcceptFiles.restype = None
        client = int(widget.winfo_id())
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        top = int(user32.GetAncestor(client, GA_ROOT) or client)
        handles = tuple(dict.fromkeys((client, top)))
        result_type = ctypes.c_ssize_t
        uint_ptr = ctypes.c_size_t
        dword_ptr = ctypes.c_size_t
        callback_type = ctypes.WINFUNCTYPE(
            result_type,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            uint_ptr,
            dword_ptr,
        )
        comctl32.SetWindowSubclass.argtypes = [
            wintypes.HWND,
            callback_type,
            uint_ptr,
            dword_ptr,
        ]
        comctl32.SetWindowSubclass.restype = wintypes.BOOL
        comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, uint_ptr]
        comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
        comctl32.DefSubclassProc.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        comctl32.DefSubclassProc.restype = result_type

        pending: deque[tuple[Path, ...]] = deque()
        pending_lock = Lock()
        metrics = {"native_messages": 0, "queued_batches": 0}
        poll_token: str | None = None

        def poll_pending() -> None:
            nonlocal poll_token
            batches: list[tuple[Path, ...]] = []
            with pending_lock:
                while pending:
                    batches.append(pending.popleft())
            for paths in batches:
                callback(paths)
            if getattr(widget, "_exam_drop_state", None):
                poll_token = widget.after(80, poll_pending)
                widget._exam_drop_state["poll_token"] = poll_token

        def subclass_proc(
            window: int,
            message: int,
            wparam: int,
            lparam: int,
            _subclass_id: int,
            _reference_data: int,
        ) -> int:
            if message == WM_DROPFILES:
                metrics["native_messages"] += 1
                paths = _read_drop_paths(wparam)
                shell32.DragFinish(wparam)
                if paths:
                    # Keep Tcl calls out of the native Windows callback.  Entering
                    # Tk from WM_DROPFILES can deadlock SendMessage and can make a
                    # real Explorer drop appear to do nothing.
                    with pending_lock:
                        pending.append(paths)
                        metrics["queued_batches"] += 1
                return 0
            return int(comctl32.DefSubclassProc(window, message, wparam, lparam))

        proc = callback_type(subclass_proc)
        subclass_id = max(1, id(widget) & 0xFFFFFFFF)
        registered: list[int] = []
        for hwnd in handles:
            if bool(comctl32.SetWindowSubclass(hwnd, proc, subclass_id, 0)):
                shell32.DragAcceptFiles(hwnd, True)
                _allow_drop_messages(user32, hwnd)
                registered.append(hwnd)
        if not registered:
            return False
        setattr(widget, "_exam_drop_state", {
            "handles": tuple(registered),
            "subclass_id": subclass_id,
            "proc": proc,
            "shell32": shell32,
            "comctl32": comctl32,
            "pending": pending,
            "pending_lock": pending_lock,
            "metrics": metrics,
            "poll_token": None,
        })
        poll_token = widget.after(80, poll_pending)
        widget._exam_drop_state["poll_token"] = poll_token
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _allow_drop_messages(user32: object, hwnd: int) -> None:
    try:
        change_filter = user32.ChangeWindowMessageFilterEx
        change_filter.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
        change_filter.restype = wintypes.BOOL
        change_filter(hwnd, WM_DROPFILES, MSGFLT_ALLOW, None)
        change_filter(hwnd, WM_COPYGLOBALDATA, MSGFLT_ALLOW, None)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def uninstall_file_drop(widget: object) -> None:
    state = getattr(widget, "_exam_drop_state", None)
    if not state:
        return
    handles = state["handles"]
    subclass_id = state["subclass_id"]
    proc = state["proc"]
    shell32 = state["shell32"]
    comctl32 = state["comctl32"]
    poll_token = state.get("poll_token")
    if poll_token:
        try:
            widget.after_cancel(poll_token)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    for hwnd in handles:
        try:
            shell32.DragAcceptFiles(hwnd, False)
            comctl32.RemoveWindowSubclass(hwnd, proc, subclass_id)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    try:
        delattr(widget, "_exam_drop_state")
    except AttributeError:
        pass


def _read_drop_paths(handle: int) -> tuple[Path, ...]:
    shell32 = ctypes.windll.shell32
    # ctypes.wintypes does not expose HDROP on current Python releases.
    # HDROP is an opaque HANDLE, so HANDLE preserves its pointer width.
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    count = int(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0))
    values: list[Path] = []
    for index in range(count):
        length = int(shell32.DragQueryFileW(handle, index, None, 0))
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(handle, index, buffer, length + 1)
        values.append(Path(buffer.value))
    return tuple(values)


__all__ = ["WM_DROPFILES", "install_file_drop", "uninstall_file_drop"]
