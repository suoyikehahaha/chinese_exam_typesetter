"""Production v0.8.3 with Windows styling, license notice and GitHub updates."""

from __future__ import annotations

import argparse
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

import desktop_app as base
from app.github_updater_v1 import (
    check_latest_release,
    download_release_asset,
    load_repository_url,
    save_repository_url,
    schedule_portable_update,
)
from desktop_app_v082_release import build_documents_v82
from desktop_app_v082_windows import WindowsDesktopApp
from desktop_app_v082_final import import_exam


base.APP_TITLE = "高中语文试卷智能排版工作台（公众号：蓑衣微言）"
base.VERSION = "0.8.3"
base.build_documents = build_documents_v82


class UpdateSettingsDialog(tk.Toplevel):
    """Collect the GitHub repository that owns future releases."""

    def __init__(self, parent: "ProductionDesktopApp", on_saved: object = None) -> None:
        super().__init__(parent)
        self.parent = parent
        self.on_saved = on_saved
        self.title("更新设置")
        self.geometry("660x250")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.repository_var = tk.StringVar(value=load_repository_url())
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="GitHub 更新设置", style="DialogTitle.TLabel").pack(
            anchor=tk.W
        )
        ttk.Label(
            frame,
            text="粘贴源码仓库地址。以后在该仓库发布新版 Windows 便携版 ZIP，软件即可检查并安装更新。",
            wraplength=600,
        ).pack(anchor=tk.W, pady=(12, 10))
        ttk.Entry(frame, textvariable=self.repository_var).pack(fill=tk.X)
        ttk.Label(
            frame,
            text="示例：https://github.com/用户名/仓库名",
            foreground="#666666",
        ).pack(anchor=tk.W, pady=(7, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor=tk.E, pady=(22, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="保存",
            style="Primary.TButton",
            command=self._save,
        ).pack(side=tk.LEFT, padx=(8, 0))

    def _save(self) -> None:
        try:
            value = save_repository_url(self.repository_var.get())
        except Exception as exc:  # noqa: BLE001
            base.messagebox.showerror(base.APP_TITLE, str(exc), parent=self)
            return
        self.destroy()
        self.parent.status_var.set(f"更新仓库已保存：{value}")
        if callable(self.on_saved):
            self.on_saved()


class ProductionDesktopApp(WindowsDesktopApp):
    """Add noncommercial attribution and self-update controls."""

    def __init__(self) -> None:
        self._update_messages: queue.Queue[tuple[str, object]] = queue.Queue()
        super().__init__()

    def _build_menu(self) -> None:
        super()._build_menu()
        menu = self.nametowidget(self["menu"])
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="检查更新", command=self.check_for_updates)
        help_menu.add_command(label="更新设置", command=self.open_update_settings)
        help_menu.add_separator()
        help_menu.add_command(label="关于与使用许可", command=self.show_about)
        menu.add_cascade(label="帮助", menu=help_menu)

    def _build_ui(self) -> None:
        super()._build_ui()
        frames = [
            child
            for child in self.winfo_children()
            if isinstance(child, ttk.Frame)
            and str(child.cget("style")) == "Toolbar.TFrame"
        ]
        if frames:
            toolbar = frames[0]
            ttk.Button(
                toolbar,
                text="检查更新",
                command=self.check_for_updates,
            ).pack(side=tk.LEFT, padx=4)
        if len(frames) > 1:
            status = frames[-1]
            ttk.Label(
                status,
                text="本人制作｜公众号：蓑衣微言｜拒绝商用",
                style="Status.TLabel",
            ).pack(side=tk.RIGHT, padx=(14, 12))

    def show_about(self) -> None:
        base.messagebox.showinfo(
            "关于与使用许可",
            "高中语文试卷智能排版工作台\n\n"
            "本软件为本人（公众号：蓑衣微言）为高中语文试题排版而做，拒绝商用。\n\n"
            "允许个人教师、学校内部教学和非营利教研使用。"
            "未经本人书面许可，不得销售、出租、收费分发、嵌入商业服务或用于其他营利活动。\n\n"
            f"当前版本：{base.VERSION}",
            parent=self,
        )

    def open_update_settings(self, on_saved: object = None) -> None:
        UpdateSettingsDialog(self, on_saved)

    def check_for_updates(self) -> None:
        repository = load_repository_url()
        if not repository:
            self.open_update_settings(self.check_for_updates)
            return
        self.status_var.set("正在连接 GitHub 检查更新……")
        self.busy_bar.start(10)

        def worker() -> None:
            try:
                info = check_latest_release(repository, base.VERSION)
                self._update_messages.put(("checked", info))
            except Exception as exc:  # noqa: BLE001
                self._update_messages.put(("update_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_update_messages)

    def _poll_update_messages(self) -> None:
        try:
            kind, payload = self._update_messages.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_update_messages)
            return
        if kind == "update_error":
            self.busy_bar.stop()
            self.status_var.set("检查更新失败。")
            base.messagebox.showerror(base.APP_TITLE, str(payload), parent=self)
            return
        if kind == "checked":
            self.busy_bar.stop()
            info = payload
            if not info.newer:
                self.status_var.set("当前已是最新版。")
                base.messagebox.showinfo(
                    base.APP_TITLE,
                    f"当前版本 {base.VERSION} 已是最新版。",
                    parent=self,
                )
                return
            install = base.messagebox.askyesno(
                "发现新版本",
                f"发现版本 {info.version}\n\n{info.name}\n\n"
                f"更新包：{info.asset_name}\n"
                f"大小：{info.asset_size / 1024 / 1024:.1f} MB\n\n"
                "是否下载并安装？",
                parent=self,
            )
            if install:
                self._download_update(info)
            return
        if kind == "downloaded":
            self.busy_bar.stop()
            self.status_var.set("更新包已下载，准备安装。")
            try:
                schedule_portable_update(Path(payload))
            except Exception as exc:  # noqa: BLE001
                base.messagebox.showerror(base.APP_TITLE, str(exc), parent=self)
                return
            base.messagebox.showinfo(
                "准备更新",
                "软件将关闭并在后台完成更新，更新后会自动重新打开。",
                parent=self,
            )
            self.after(200, self.destroy)

    def _download_update(self, info: object) -> None:
        self.status_var.set("正在下载 GitHub 更新包……")
        self.busy_bar.start(10)

        def worker() -> None:
            try:
                path = download_release_asset(info)
                self._update_messages.put(("downloaded", path))
            except Exception as exc:  # noqa: BLE001
                self._update_messages.put(("update_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_update_messages)


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
