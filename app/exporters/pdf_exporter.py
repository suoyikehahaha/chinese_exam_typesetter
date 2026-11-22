"""通过 Word 或 LibreOffice 将 DOCX 转换为 PDF。"""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import tempfile


class PdfExportError(RuntimeError):
    """PDF 导出失败。"""


class PdfExporter:
    """PDF 双通道导出器。"""

    def export(self, docx_path: str | Path, pdf_path: str | Path) -> tuple[Path, str]:
        """优先调用 Word，失败后调用 LibreOffice。"""

        source = Path(docx_path).resolve()
        target = Path(pdf_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        try:
            self._export_with_word(source, target)
            return target, "microsoft-word"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Microsoft Word：{exc}")
        try:
            self._export_with_libreoffice(source, target)
            return target, "libreoffice"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"LibreOffice：{exc}")
        raise PdfExportError("PDF 导出失败；" + "；".join(errors))

    @staticmethod
    def _export_with_word(source: Path, target: Path) -> None:
        source_ps = str(source).replace("'", "''")
        target_ps = str(target).replace("'", "''")
        script = (
            "$ErrorActionPreference='Stop';$word=$null;$doc=$null;$exported=$false;"
            "try {$word=New-Object -ComObject Word.Application;"
            "$word.Visible=$false;$word.DisplayAlerts=0;$word.AutomationSecurity=3;"
            f"$source='{source_ps}';$target='{target_ps}';"
            "$doc=$word.Documents.Open($source,$false,$true,$false);"
            "$doc.ExportAsFixedFormat($target,17);$exported=$true"
            "} catch {[Console]::Error.WriteLine($_.Exception.Message)} finally {"
            "try {if($null -ne $doc){$doc.Close()}} catch {[Console]::Error.WriteLine($_.Exception.Message)};"
            "try {if($null -ne $word){$word.Quit()}} catch {[Console]::Error.WriteLine($_.Exception.Message)}"
            "};if(-not $exported){exit 1};exit 0"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not target.is_file() or not target.stat().st_size:
            message = completed.stderr.strip() or completed.stdout.strip() or "Word COM 未生成 PDF"
            raise PdfExportError(message)

    @staticmethod
    def _export_with_libreoffice(source: Path, target: Path) -> None:
        candidates = [
            os.environ.get("SOFFICE_PATH"),
            shutil.which("soffice"),
            str(Path(os.environ.get("ProgramW6432", "")) / "LibreOffice/program/soffice.exe"),
            str(Path(os.environ.get("ProgramFiles", "")) / "LibreOffice/program/soffice.exe"),
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        executable = next((item for item in candidates if item and Path(item).exists()), None)
        if executable is None:
            raise PdfExportError("未找到 soffice")
        with tempfile.TemporaryDirectory(prefix="exam_pdf_") as temp_dir:
            profile = Path(temp_dir) / "profile"
            completed = subprocess.run(
                [
                    executable,
                    f"-env:UserInstallation={profile.resolve().as_uri()}",
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    temp_dir,
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            converted = Path(temp_dir) / f"{source.stem}.pdf"
            if completed.returncode != 0 or not converted.exists():
                message = completed.stderr.strip() or completed.stdout.strip() or "LibreOffice 未生成 PDF"
                raise PdfExportError(message)
            shutil.copy2(converted, target)
