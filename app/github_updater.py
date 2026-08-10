"""Fixed-repository GitHub Releases updater for the portable Windows EXE."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen


GITHUB_REPOSITORY = "https://github.com/suoyikehahaha/chinese_exam_typesetter"
GITHUB_API = "https://api.github.com/repos/suoyikehahaha/chinese_exam_typesetter/releases/latest"
USER_AGENT = "SuyiWeiyan-Exam-Typesetter-Updater"


@dataclass(slots=True)
class UpdateInfo:
    version: str
    name: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    release_url: str
    newer: bool


def check_latest_release(current_version: str) -> UpdateInfo:
    """Read the latest public release from the official repository."""

    request = Request(
        GITHUB_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assets = [
        asset
        for asset in payload.get("assets", [])
        if str(asset.get("name", "")).lower().endswith(".exe")
    ]
    preferred = [
        asset
        for asset in assets
        if "chineseexamtypesetter" in str(asset.get("name", "")).lower()
    ]
    if not preferred:
        raise RuntimeError("最新 Release 中没有找到 Windows EXE，请稍后重试。")
    asset = preferred[0]
    asset_url = str(asset.get("browser_download_url", ""))
    _validate_download_url(asset_url)
    version = str(payload.get("tag_name", "")).strip().lstrip("vV")
    return UpdateInfo(
        version=version,
        name=str(payload.get("name", "") or payload.get("tag_name", "")),
        notes=str(payload.get("body", "")),
        asset_name=str(asset.get("name", "")),
        asset_url=asset_url,
        asset_size=int(asset.get("size", 0)),
        release_url=str(payload.get("html_url", "")),
        newer=_version_tuple(version) > _version_tuple(current_version),
    )


def download_release_asset(info: UpdateInfo) -> Path:
    """Download the release EXE into a private temporary directory."""

    _validate_download_url(info.asset_url)
    folder = Path(tempfile.mkdtemp(prefix="suyi_exam_update_"))
    target = folder / info.asset_name
    request = Request(
        info.asset_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    with urlopen(request, timeout=120) as response, target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if info.asset_size and target.stat().st_size != info.asset_size:
        raise RuntimeError("更新文件大小校验失败，请重新下载。")
    return target


def schedule_portable_update(downloaded_exe: Path) -> None:
    """Replace the running portable EXE after it exits, then restart it."""

    if not getattr(sys, "frozen", False):
        raise RuntimeError("源码运行模式不能覆盖安装，请在打包后的软件中执行更新。")
    downloaded_exe = downloaded_exe.resolve()
    if downloaded_exe.suffix.lower() != ".exe" or not downloaded_exe.is_file():
        raise RuntimeError("下载的更新文件不是有效的 Windows EXE。")
    executable = Path(sys.executable).resolve()
    script = _portable_update_script(downloaded_exe, executable, os.getpid())
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded,
        ],
        creationflags=creation_flags,
        close_fds=True,
    )


def _portable_update_script(downloaded_exe: Path, executable: Path, pid: int) -> str:
    """Build the hidden replacement script separately for deterministic testing."""

    backup = executable.with_suffix(executable.suffix + ".previous")
    return f"""
$ErrorActionPreference = 'Stop'
$processId = {pid}
$download = {_ps_quote(str(downloaded_exe))}
$target = {_ps_quote(str(executable))}
$backup = {_ps_quote(str(backup))}
Wait-Process -Id $processId -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $backup) {{
    Remove-Item -LiteralPath $backup -Force
}}
Move-Item -LiteralPath $target -Destination $backup -Force
try {{
    Copy-Item -LiteralPath $download -Destination $target -Force
    Start-Process -FilePath $target
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue
}}
catch {{
    if (Test-Path -LiteralPath $backup) {{
        Move-Item -LiteralPath $backup -Destination $target -Force
    }}
    throw
}}
"""


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", value)]
    return tuple((parts + [0, 0, 0])[:3])


def _validate_download_url(value: str) -> None:
    parsed = urlparse(value)
    allowed = (
        parsed.scheme == "https"
        and (
            parsed.hostname == "github.com"
            or (parsed.hostname or "").endswith(".githubusercontent.com")
        )
    )
    if not allowed:
        raise RuntimeError("GitHub 返回了无效的更新下载地址。")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


__all__ = [
    "GITHUB_API",
    "GITHUB_REPOSITORY",
    "UpdateInfo",
    "check_latest_release",
    "download_release_asset",
    "schedule_portable_update",
]
