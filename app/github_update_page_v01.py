"""Embedded GitHub release page for the Windows desktop application."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.request import Request, urlopen
import webbrowser


GITHUB_REPOSITORY = "https://github.com/suoyikehahaha/-"
GITHUB_API = "https://api.github.com/repos/suoyikehahaha/-/releases/latest"
USER_AGENT = "ChineseExamTypesetter/0.4.5"


class GitHubUpdatePage(tk.Toplevel):
    """Show release information inside the application without requiring Office."""

    def __init__(self, parent: tk.Misc, current_version: str) -> None:
        super().__init__(parent)
        self.parent = parent
        self.current_version = current_version
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.title("GitHub 更新页面")
        self.geometry("760x560")
        self.minsize(620, 420)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self._refresh()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="GitHub 更新页面",
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text=f"当前版本：v{self.current_version}    仓库：{GITHUB_REPOSITORY}",
            foreground="#555555",
        ).pack(anchor=tk.W, pady=(6, 12))

        self.status_var = tk.StringVar(value="正在读取 GitHub 最新发布……")
        ttk.Label(frame, textvariable=self.status_var).pack(anchor=tk.W)

        self.details = ScrolledText(
            frame,
            wrap=tk.WORD,
            height=20,
            font=("Microsoft YaHei UI", 10),
            state=tk.DISABLED,
        )
        self.details.pack(fill=tk.BOTH, expand=True, pady=(8, 12))

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="刷新", command=self._refresh).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="打开 GitHub 仓库",
            command=lambda: webbrowser.open(GITHUB_REPOSITORY),
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side=tk.RIGHT)

    def _refresh(self) -> None:
        self.status_var.set("正在读取 GitHub 最新发布……")
        self._set_details("正在连接 GitHub，请稍候……")
        threading.Thread(target=self._fetch, daemon=True).start()
        self.after(100, self._poll)

    def _fetch(self) -> None:
        try:
            request = Request(
                GITHUB_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": USER_AGENT,
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self._messages.put(("ok", payload))
        except Exception as exc:  # noqa: BLE001
            self._messages.put(("error", str(exc)))

    def _poll(self) -> None:
        try:
            kind, payload = self._messages.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(100, self._poll)
            return
        if kind == "error":
            self.status_var.set("暂时无法连接 GitHub")
            self._set_details(
                "暂时无法读取最新发布信息。\n\n"
                f"错误：{payload}\n\n"
                "你仍可以点击“打开 GitHub 仓库”查看源码和发布页面。"
            )
            return

        data = payload if isinstance(payload, dict) else {}
        tag = str(data.get("tag_name", "暂无发布版本"))
        name = str(data.get("name", ""))
        published = str(data.get("published_at", ""))
        notes = str(data.get("body", "")).strip() or "暂无更新说明。"
        assets = data.get("assets", [])
        asset_lines = []
        if isinstance(assets, list):
            for asset in assets:
                if isinstance(asset, dict):
                    asset_lines.append(
                        f"{asset.get('name', '')}\n{asset.get('browser_download_url', '')}"
                    )
        text = (
            f"最新版本：{tag}\n"
            f"发布名称：{name or tag}\n"
            f"发布时间：{published or '未提供'}\n"
            f"当前版本：v{self.current_version}\n\n"
            "更新说明\n"
            "────────────\n"
            f"{notes}\n\n"
            "发布文件\n"
            "────────────\n"
            f"{chr(10).join(asset_lines) if asset_lines else '暂无附件，请打开 GitHub 仓库查看。'}"
        )
        self.status_var.set("GitHub 信息已更新")
        self._set_details(text)

    def _set_details(self, text: str) -> None:
        self.details.configure(state=tk.NORMAL)
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", text)
        self.details.configure(state=tk.DISABLED)


__all__ = ["GITHUB_API", "GITHUB_REPOSITORY", "GitHubUpdatePage"]
