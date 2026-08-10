"""Optional Word/WPS bridge used only for legacy ``.doc`` files.

The core application reads and writes DOCX directly.  This module is kept
small and lazy: it probes the local machine only when a legacy binary Word
document needs conversion.  All child processes are hidden on Windows.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CONVERSION_TIMEOUT = 120


@dataclass(frozen=True, slots=True)
class OfficeEngine:
    """A locally detected office engine."""

    name: str
    executable: Path | None
    progids: tuple[str, ...]


def detect_office_engines() -> tuple[OfficeEngine, ...]:
    """Return usable Word/WPS candidates in the preferred order."""

    word = _first_existing(_word_candidates())
    wps = _first_existing(_wps_candidates())
    engines: list[OfficeEngine] = []
    if word:
        engines.append(OfficeEngine("microsoft-word", word, ("Word.Application",)))
    if wps:
        engines.append(
            OfficeEngine("wps", wps, ("Kwps.Application", "WPS.Application"))
        )
    return tuple(engines)


def engine_summary() -> str:
    """Return a short user-facing summary without starting an office app."""

    engines = detect_office_engines()
    names = {"microsoft-word": "Word", "wps": "WPS"}
    if not engines:
        return "未检测到 Word 或 WPS，当前支持 DOCX 工作模式"
    return "可用转换引擎：" + "、".join(names[item.name] for item in engines)


def convert_doc_to_docx(
    source: str | Path,
    target: str | Path,
    *,
    engines: tuple[OfficeEngine, ...] | None = None,
) -> OfficeEngine:
    """Convert a legacy ``.doc`` to DOCX with Word or WPS.

    The source file is opened read-only and is never overwritten.  A clear
    error is raised when neither compatible office application is installed.
    """

    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    if source_path.suffix.lower() != ".doc":
        raise ValueError("旧版转换入口只接受 .doc 文件。")
    if not source_path.is_file():
        raise FileNotFoundError(f"找不到 Word 文件：{source_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = engines if engines is not None else detect_office_engines()
    if not candidates:
        raise RuntimeError(
            "导入 .doc 需要 Microsoft Word 或 WPS Office。"
            "当前电脑未检测到可用办公软件，请安装其中一种后重试；DOCX 文件无需办公软件。"
        )
    errors: list[str] = []
    for engine in candidates:
        for progid in engine.progids:
            target_path.unlink(missing_ok=True)
            try:
                _convert_with_com(source_path, target_path, progid)
                if _is_docx(target_path):
                    return engine
                errors.append(f"{engine.name}/{progid} 未生成有效 DOCX")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{engine.name}/{progid}：{exc}")
    detail = "；".join(errors)
    raise RuntimeError(
        "无法把 .doc 转换为 .docx。请确认 Word 或 WPS 可以正常打开该文件。"
        + (f" 详细信息：{detail}" if detail else "")
    )


def _convert_with_com(source: Path, target: Path, progid: str) -> None:
    """Run a hidden PowerShell COM conversion for one ProgID."""

    powershell = _find_powershell()
    if powershell is None:
        raise RuntimeError("未找到 Windows PowerShell，无法调用本机办公软件转换。")
    source_ps = _powershell_quote(source)
    target_ps = _powershell_quote(target)
    progid_ps = _powershell_quote_text(progid)
    script = (
        "$ErrorActionPreference='Stop';"
        "$app=$null;$doc=$null;$failed=$false;"
        "try {"
        f"$app=New-Object -ComObject {progid_ps};"
        "$app.Visible=$false;"
        "try {$app.DisplayAlerts=0} catch {};"
        f"$doc=$app.Documents.Open({source_ps},$false,$true);"
        f"$doc.SaveAs({target_ps},12);"
        "} catch { $failed=$true; Write-Error $_ }"
        "finally {"
        "if ($doc -ne $null) {"
        "try {$doc.Close($false)} catch {};"
        "try {[Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)} catch {};"
        "}"
        "if ($app -ne $null) {"
        "try {$app.Quit($false)} catch {};"
        "try {[Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)} catch {};"
        "}"
        "[GC]::Collect();[GC]::WaitForPendingFinalizers();"
        "}"
        "if ($failed) {exit 1} else {exit 0}"
    )
    completed = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-STA",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=_CONVERSION_TIMEOUT,
        check=False,
        creationflags=_NO_WINDOW,
    )
    if not _is_docx(target):
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"转换进程返回退出码 {completed.returncode}")


def _word_candidates() -> list[str | None]:
    values: list[str | None] = [shutil.which("winword.exe"), shutil.which("winword")]
    for variable in ("ProgramW6432", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(variable)
        if base:
            root = Path(base)
            values.extend(
                [
                    str(root / "Microsoft Office" / "root" / "Office16" / "WINWORD.EXE"),
                    str(root / "Microsoft Office" / "Office16" / "WINWORD.EXE"),
                    str(root / "Microsoft Office" / "root" / "Office15" / "WINWORD.EXE"),
                ]
            )
    return values


def _wps_candidates() -> list[str | None]:
    values: list[str | None] = [shutil.which("wps.exe"), shutil.which("wps")]
    for variable in ("ProgramW6432", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            root = Path(base)
            values.extend(
                [
                    str(root / "WPS Office" / "ksolaunch.exe"),
                    str(root / "WPS Office" / "11.1.0.0" / "office6" / "wps.exe"),
                    str(root / "Kingsoft" / "WPS Office" / "office6" / "wps.exe"),
                ]
            )
    return values


def _find_powershell() -> Path | None:
    values = [shutil.which("powershell.exe"), shutil.which("powershell")]
    windir = os.environ.get("WINDIR", r"C:\Windows")
    values.append(str(Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"))
    return _first_existing(values)


def _first_existing(values: list[str | None]) -> Path | None:
    for value in values:
        if value:
            path = Path(value)
            if path.is_file():
                return path
    return None


def _powershell_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _powershell_quote_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_docx(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 200 and path.read_bytes()[:2] == b"PK"
    except OSError:
        return False


__all__ = ["OfficeEngine", "convert_doc_to_docx", "detect_office_engines", "engine_summary"]
