"""Production Windows launcher and release diagnostics for v0.4.5."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib
import json
import os
import sys
from pathlib import Path

from windows_launcher import _install_startup_fix


WM_DROPFILES = 0x0233


class DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


def _set_tk_data_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    tcl_data = root / "_tcl_data"
    tk_data = root / "_tk_data"
    if not (tcl_data / "init.tcl").is_file():
        raise RuntimeError(f"Tcl 运行数据不完整：{tcl_data}")
    if not (tk_data / "tk.tcl").is_file():
        raise RuntimeError(f"Tk 运行数据不完整：{tk_data}")
    os.chdir(root)
    os.environ["TCL_LIBRARY"] = str(tcl_data)
    os.environ["TK_LIBRARY"] = str(tk_data)


def _load_application() -> object:
    current = importlib.import_module("desktop_app_current_v01")
    _install_startup_fix(current)
    return importlib.import_module("desktop_app_v045")


def _question_numbers(data: dict) -> list[int]:
    return [
        block["question"]["number"]
        for block in data.get("blocks", [])
        if block.get("type") == "question"
    ]


def _run_import_test(module: object, source: Path, output: Path) -> int:
    data = module.import_exam(source)
    result = {
        "source": source.name,
        "blocks": len(data.get("blocks", [])),
        "questions": _question_numbers(data),
        "automatic_numbering": any(
            item.get("code") == "word-automatic-numbering"
            for item in data.get("diagnostics", [])
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def _run_font_test(output: Path) -> int:
    from app.font_resolver_v043 import load_preview_font, resolved_font_path

    values = {}
    for name in ("SimSun", "SimHei", "KaiTi", "FangSong"):
        for bold in (False, True):
            font = load_preview_font(name, 10.5, bold)
            key = f"{name}_{'bold' if bold else 'regular'}"
            values[key] = {
                "path": resolved_font_path(name, bold=bold),
                "type": type(font).__name__,
                "cjk_distinct_masks": len(
                    {bytes(font.getmask(character)) for character in "语文试卷排版"}
                ),
            }
    entries = list(values.values())
    values["all_cjk_fonts_valid"] = all(
        item["type"] == "FreeTypeFont" and item["cjk_distinct_masks"] >= 4
        for item in entries
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    return 0 if values["all_cjk_fonts_valid"] else 2


def _run_startup_test(module: object, output: Path) -> int:
    app = module.CurrentDesktopApp()

    def finish() -> None:
        payload = {
            "status": "GUI_STARTUP_OK",
            "version": module.APP_VERSION,
            "tree": bool(app.tree.get_children("")),
            "preview_service": type(app._preview_service).__module__,
            "preview": type(app.document_editor).__name__,
            "drop_enabled": bool(app.drop_enabled),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        app._on_close()

    app.after(1800, finish)
    app.mainloop()
    return 0


def _make_drop_handle(path: Path) -> int:
    data = (str(path.resolve()) + "\0\0").encode("utf-16-le")
    size = ctypes.sizeof(DROPFILES) + len(data)
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    handle = int(kernel32.GlobalAlloc(0x42, size))
    pointer = int(kernel32.GlobalLock(handle))
    header = DROPFILES(ctypes.sizeof(DROPFILES), wintypes.POINT(0, 0), False, True)
    ctypes.memmove(pointer, ctypes.byref(header), ctypes.sizeof(header))
    ctypes.memmove(pointer + ctypes.sizeof(header), data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def _run_drop_message_test(module: object, source: Path, output: Path) -> int:
    app = module.CurrentDesktopApp()
    attempts = 0

    def begin() -> None:
        state = getattr(app, "_exam_drop_state_v03", None)
        if not state:
            app.after(100, begin)
            return
        # Explorer targets the accepting top-level window.  The Tk child is
        # registered as a compatibility fallback, while diagnostics exercise
        # the same top-level path used by a real shell drop.
        hwnd = int(state["handles"][-1])
        handle = _make_drop_handle(source)
        user32 = ctypes.windll.user32
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.SendMessageW(hwnd, WM_DROPFILES, handle, 0)
        app.after(150, check)

    def check() -> None:
        nonlocal attempts
        attempts += 1
        current = Path(app.current_exam_path).resolve() if app.current_exam_path else None
        if current == source.resolve() or attempts >= 100:
            finish()
        else:
            app.after(150, check)

    def finish() -> None:
        payload = {
            "drop_enabled": bool(app.drop_enabled),
            "source": Path(app.current_exam_path).name if app.current_exam_path else "",
            "questions": _question_numbers(app.raw_exam),
            "status": app.status_var.get(),
            "windows_message_received": bool(app.current_exam_path),
            "drop_metrics": dict(
                getattr(app, "_exam_drop_state_v03", {}).get("metrics", {})
            ),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        app._on_close()

    app.after(400, begin)
    app.mainloop()
    return 0


def main() -> int:
    _set_tk_data_paths()
    module = _load_application()
    if "--font-test-output" in sys.argv:
        return _run_font_test(Path(sys.argv[sys.argv.index("--font-test-output") + 1]))
    if "--drop-message-test" in sys.argv:
        source = Path(sys.argv[sys.argv.index("--drop-message-test") + 1])
        output = Path(sys.argv[sys.argv.index("--test-output") + 1])
        return _run_drop_message_test(module, source, output)
    if "--import-test" in sys.argv:
        source = Path(sys.argv[sys.argv.index("--import-test") + 1])
        output = Path(sys.argv[sys.argv.index("--test-output") + 1])
        return _run_import_test(module, source, output)
    if "--startup-test-output" in sys.argv:
        return _run_startup_test(module, Path(sys.argv[sys.argv.index("--startup-test-output") + 1]))
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
