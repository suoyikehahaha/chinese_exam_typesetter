"""静默执行 DOCX 到 PDF 的转换，供预览和导出流程使用。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from app.exporters.pdf_exporter import PdfExportError


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_WORD_TIMEOUT_SECONDS = 90
_PDF_WAIT_SECONDS = 8.0


class SilentPdfExporter:
    """优先调用 Word，失败后尝试 LibreOffice，整个过程不显示控制台。"""

    def export(self, docx_path: str | Path, pdf_path: str | Path) -> tuple[Path, str]:
        """将 DOCX 转为 PDF，并返回输出路径和实际转换引擎。"""

        source = Path(docx_path).resolve()
        target = Path(pdf_path).resolve()
        if not source.is_file():
            raise PdfExportError(f"找不到待转换的 Word 文件：{source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []

        for attempt in range(2):
            try:
                self._export_with_word(source, target)
                if self._is_valid_pdf(target):
                    return target, "microsoft-word"
                errors.append(f"Microsoft Word 第 {attempt + 1} 次未生成有效 PDF")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Microsoft Word：{exc}")

        try:
            self._export_with_libreoffice(source, target)
            if self._is_valid_pdf(target):
                return target, "libreoffice"
            errors.append("LibreOffice 未生成有效 PDF")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"LibreOffice：{exc}")

        raise PdfExportError("内部预览生成失败；" + "；".join(errors))

    @classmethod
    def _export_with_word(cls, source: Path, target: Path) -> None:
        """运行独立 PowerShell COM 进程，并让清理异常不覆盖导出结果。"""

        powershell = cls._find_powershell()
        if powershell is None:
            raise PdfExportError("未找到 Windows PowerShell，无法调用 Microsoft Word")

        target.unlink(missing_ok=True)
        script = cls._build_word_script(source, target)
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
            timeout=_WORD_TIMEOUT_SECONDS,
            check=False,
            creationflags=_NO_WINDOW,
        )

        if cls._wait_for_pdf(target):
            return

        detail = completed.stderr.strip() or completed.stdout.strip()
        if completed.returncode:
            raise PdfExportError(detail or f"Microsoft Word 返回退出码 {completed.returncode}")
        raise PdfExportError(detail or "Microsoft Word 未生成有效 PDF")

    @staticmethod
    def _build_word_script(source: Path, target: Path) -> str:
        """构造安全的 Word COM PowerShell 脚本，路径中的单引号会被转义。"""

        source_ps = _powershell_quote(source)
        target_ps = _powershell_quote(target)
        return (
            "$ErrorActionPreference='Stop';"
            "$word=$null;"
            "$doc=$null;"
            "$failed=$false;"
            "try {"
            "$word=New-Object -ComObject Word.Application;"
            "$word.Visible=$false;"
            "$word.DisplayAlerts=0;"
            f"$doc=$word.Documents.Open({source_ps},$false,$true);"
            f"$doc.ExportAsFixedFormat({target_ps},17);"
            "} catch {"
            "$failed=$true;"
            "Write-Error $_;"
            "} finally {"
            "if ($doc -ne $null) {"
            "try {$doc.Close($false)} catch {}"
            "try {[Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)} catch {}"
            "}"
            "if ($word -ne $null) {"
            "try {$word.Quit($false)} catch {}"
            "try {[Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)} catch {}"
            "}"
            "[GC]::Collect();"
            "[GC]::WaitForPendingFinalizers();"
            "}"
            "if ($failed) {exit 1} else {exit 0}"
        )

    @staticmethod
    def _export_with_libreoffice(source: Path, target: Path) -> None:
        """使用独立用户配置调用 LibreOffice，避免占用用户正在使用的实例。"""

        executable = _find_libreoffice()
        if executable is None:
            raise PdfExportError(
                "未找到 LibreOffice。请安装 LibreOffice，或确认 Microsoft Word 可以正常启动。"
            )

        target.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="exam_preview_") as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "output"
            profile_dir = root / "profile"
            output_dir.mkdir()
            profile_dir.mkdir()
            profile_uri = profile_dir.resolve().as_uri()
            completed = subprocess.run(
                [
                    str(executable),
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--norestore",
                    "--nolockcheck",
                    f"-env:UserInstallation={profile_uri}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=_WORD_TIMEOUT_SECONDS,
                check=False,
                creationflags=_NO_WINDOW,
            )
            converted = output_dir / f"{source.stem}.pdf"
            if completed.returncode != 0 or not SilentPdfExporter._is_valid_pdf(converted):
                message = completed.stderr.strip() or completed.stdout.strip()
                raise PdfExportError(message or "LibreOffice 未生成有效 PDF")
            shutil.copy2(converted, target)

    @staticmethod
    def _find_powershell() -> Path | None:
        candidates = [
            shutil.which("powershell.exe"),
            shutil.which("powershell"),
        ]
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidates.append(str(Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"))
        return _first_existing_file(candidates)

    @staticmethod
    def _is_valid_pdf(path: Path) -> bool:
        try:
            if path.stat().st_size < 32:
                return False
            with path.open("rb") as handle:
                return handle.read(5) == b"%PDF-"
        except OSError:
            return False

    @classmethod
    def _wait_for_pdf(cls, path: Path) -> bool:
        deadline = time.monotonic() + _PDF_WAIT_SECONDS
        while time.monotonic() < deadline:
            if cls._is_valid_pdf(path):
                return True
            time.sleep(0.2)
        return cls._is_valid_pdf(path)


def _powershell_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _first_existing_file(candidates: list[str | None]) -> Path | None:
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file():
                return path
    return None


def _find_libreoffice() -> Path | None:
    candidates: list[str | None] = [
        shutil.which("soffice.exe"),
        shutil.which("soffice"),
    ]
    for variable in ("ProgramW6432", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            candidates.append(str(Path(base) / "LibreOffice" / "program" / "soffice.exe"))
            candidates.append(str(Path(base) / "Programs" / "LibreOffice" / "program" / "soffice.exe"))
    return _first_existing_file(candidates)


__all__ = ["SilentPdfExporter"]
