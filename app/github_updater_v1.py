"""GitHub Releases updater for the portable Windows distribution."""

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


APP_FOLDER = "SuyiWeiyanExamTypesetter"
SETTINGS_FILE = "settings.json"
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


def settings_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    return base / APP_FOLDER / SETTINGS_FILE


def load_repository_url() -> str:
    path = settings_path()
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("github_repository", "")).strip()


def save_repository_url(value: str) -> str:
    normalized = normalize_repository_url(value)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"github_repository": normalized},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return normalized


def normalize_repository_url(value: str) -> str:
    text = value.strip().rstrip("/")
    text = re.sub(r"\.git$", "", text, flags=re.IGNORECASE)
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("请输入完整的 GitHub 仓库地址，例如 https://github.com/用户名/仓库名")
    return f"https://github.com/{match.group(1)}/{match.group(2)}"


def check_latest_release(repository_url: str, current_version: str) -> UpdateInfo:
    normalized = normalize_repository_url(repository_url)
    owner, repository = normalized.removeprefix("https://github.com/").split("/", 1)
    api_url = f"https://api.github.com/repos/{owner}/{repository}/releases/latest"
    request = Request(
        api_url,
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
        if str(asset.get("name", "")).lower().endswith(".zip")
    ]
    preferred = [
        asset
        for asset in assets
        if any(
            cue in str(asset.get("name", "")).lower()
            for cue in ("windows", "win", "便携", "排版工作台")
        )
    ]
    if not preferred:
        raise RuntimeError("最新 Release 中没有 Windows 便携版 ZIP，请先上传发布文件")
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
    _validate_download_url(info.asset_url)
    folder = Path(tempfile.mkdtemp(prefix="suyi_exam_update_"))
    target = folder / info.asset_name
    request = Request(
        info.asset_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    with urlopen(request, timeout=60) as response, target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if info.asset_size and target.stat().st_size != info.asset_size:
        raise RuntimeError("更新包大小校验失败，请重新下载")
    return target


def schedule_portable_update(zip_path: Path) -> None:
    """Run a hidden updater after the current portable process exits."""

    if not getattr(sys, "frozen", False):
        raise RuntimeError("源码运行模式不能覆盖安装，请在打包后的软件中执行更新")
    executable = Path(sys.executable).resolve()
    install_dir = executable.parent
    pid = os.getpid()
    extract_dir = zip_path.parent / "expanded"
    script = f"""
$ErrorActionPreference = 'Stop'
$processId = {pid}
$archive = {_ps_quote(str(zip_path))}
$extract = {_ps_quote(str(extract_dir))}
$install = {_ps_quote(str(install_dir))}
$exeName = {_ps_quote(executable.name)}
Wait-Process -Id $processId -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $extract) {{
    Remove-Item -LiteralPath $extract -Recurse -Force
}}
Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
$candidate = Get-ChildItem -LiteralPath $extract -Recurse -Filter $exeName |
    Where-Object {{ Test-Path -LiteralPath (Join-Path $_.Directory.FullName '_internal') }} |
    Select-Object -First 1
if ($null -eq $candidate) {{
    throw '更新包中没有找到完整的 Windows 便携版'
}}
$payload = $candidate.Directory.FullName
Get-ChildItem -LiteralPath $payload -Force | ForEach-Object {{
    Copy-Item -LiteralPath $_.FullName -Destination $install -Recurse -Force
}}
Start-Process -FilePath (Join-Path $install $exeName)
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
"""
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
        raise RuntimeError("GitHub 返回了无效的更新下载地址")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


__all__ = [
    "UpdateInfo",
    "check_latest_release",
    "download_release_asset",
    "load_repository_url",
    "normalize_repository_url",
    "save_repository_url",
    "schedule_portable_update",
    "settings_path",
]
