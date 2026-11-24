"""Fixed-repository GitHub Releases updater for the portable Windows EXE."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hashlib
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
    digest: str = ""
    checksum_url: str = ""


@contextmanager
def _open_release_request(request: Request, timeout: int):
    """Use the Windows HTTPS client when the packaged TLS runtime cannot start."""
    try:
        response = urlopen(request, timeout=timeout)
    except FileNotFoundError as exc:
        system_root = os.environ.get("SystemRoot")
        curl = Path(system_root, "System32", "curl.exe") if system_root else None
        if os.name != "nt" or curl is None or not curl.is_file():
            raise RuntimeError(f"无法建立更新连接，请从发布页下载：{GITHUB_REPOSITORY}/releases/latest") from exc
        args = [str(curl), "--disable", "--fail", "--silent", "--show-error", "--location",
                "--proto", "=https", "--proto-redir", "=https", "--max-time", str(timeout)]
        for key, value in request.header_items():
            args.extend(["--header", f"{key}: {value}"])
        with tempfile.TemporaryFile() as body:
            result = subprocess.run([*args, "--", request.full_url], stdout=body, stderr=subprocess.PIPE,
                                    timeout=timeout + 5, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                detail = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"连接 GitHub 失败，请稍后重试。{detail}") from exc
            body.seek(0)
            yield body
    else:
        with response as stream:
            yield stream


def fetch_release_metadata() -> dict:
    request = Request(
        GITHUB_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with _open_release_request(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def check_latest_release(current_version: str) -> UpdateInfo:
    """Read the latest public release from the official repository."""
    payload = fetch_release_metadata()
    version = str(payload.get("tag_name", "")).removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("发布版本号无法识别，请查看 GitHub 发布页面。")
    assets = payload.get("assets", ())
    expected_name = f"ChineseExamTypesetter_{version}.exe"
    asset = next((a for a in assets if a.get("name") == expected_name), None)
    if asset is None:
        raise RuntimeError(f"发布页面缺少 {expected_name}，请稍后重试。")
    asset_url = str(asset.get("browser_download_url", ""))
    _validate_download_url(asset_url)
    checksum = next((a for a in assets if a.get("name") == "SHA256SUMS.txt"), {})
    return UpdateInfo(
        version, str(payload.get("name") or version), str(payload.get("body") or ""),
        expected_name, asset_url, int(asset.get("size", 0)), str(payload.get("html_url", "")),
        _version_tuple(version) > _version_tuple(current_version),
        str(asset.get("digest") or ""), str(checksum.get("browser_download_url") or ""),
    )


def _expected_hash(info):
    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", info.digest):
        return info.digest.split(":", 1)[1].lower()
    if info.checksum_url:
        _validate_download_url(info.checksum_url)
        request = Request(info.checksum_url, headers={"User-Agent": USER_AGENT})
        with _open_release_request(request, timeout=25) as response:
            manifest = response.read(65537)
        if len(manifest) <= 65536:
            for line in manifest.decode("utf-8-sig").splitlines():
                match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line.strip())
                if match and match[2] == info.asset_name:
                    return match[1].lower()
    raise RuntimeError("发布页缺少这个安装包的 SHA-256 校验值，请稍后重试。")


def download_release_asset(info: UpdateInfo) -> Path:
    _validate_download_url(info.asset_url)
    if not re.fullmatch(r"ChineseExamTypesetter_\d+\.\d+\.\d+\.exe", info.asset_name):
        raise ValueError("更新包的文件名无效。")
    expected = _expected_hash(info)
    folder = Path(tempfile.mkdtemp(prefix="suyi_exam_update_"))
    target = folder / info.asset_name
    request = Request(info.asset_url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"})
    digest = hashlib.sha256()
    try:
        with _open_release_request(request, timeout=120) as response, target.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if info.asset_size and target.stat().st_size != info.asset_size:
            raise ValueError("更新文件大小校验失败，请重新下载。")
        if digest.hexdigest() != expected:
            raise ValueError("更新文件 SHA-256 校验失败，请重新下载。")
        return target
    except Exception:
        target.unlink(missing_ok=True)
        folder.rmdir()
        raise


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
    expected = downloaded_exe.stem.rsplit("_", 1)[-1]
    return f"""
$ErrorActionPreference = 'Stop'
$processId = {pid}
$download = {_ps_quote(str(downloaded_exe))}
$target = {_ps_quote(str(executable))}
$backup = {_ps_quote(str(backup))}
$expected = {_ps_quote(expected)}
$probe = Join-Path ([IO.Path]::GetTempPath()) ('exam-health-' + [guid]::NewGuid() + '.json')
Wait-Process -Id $processId -ErrorAction SilentlyContinue
Copy-Item -LiteralPath $target -Destination $backup -Force
try {{
    Copy-Item -LiteralPath $download -Destination $target -Force
    $check = Start-Process -FilePath $target -ArgumentList ('--startup-test-output "' + $probe + '"') -WindowStyle Hidden -PassThru
    if (-not $check.WaitForExit(60000)) {{
        Stop-Process -Id $check.Id -Force -ErrorAction SilentlyContinue
        $check.WaitForExit()
        throw 'Startup timed out'
    }}
    $health = Get-Content -LiteralPath $probe -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($check.ExitCode -ne 0 -or $health.status -ne 'GUI_STARTUP_OK' -or $health.version -ne $expected) {{
        throw 'New application failed startup verification'
    }}
    Start-Process -FilePath $target
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
}}
catch {{
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Start-Process -FilePath $target
    throw
}}
finally {{
    Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue
}}
"""


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", value)]
    return tuple((parts + [0, 0, 0])[:3])


def _validate_download_url(value: str) -> None:
    parsed = urlparse(value)
    allowed = (parsed.scheme == "https" and parsed.netloc == "github.com"
               and parsed.path.startswith(urlparse(GITHUB_REPOSITORY).path + "/releases/download/")
               and not parsed.query and not parsed.fragment and ".." not in parsed.path)
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
