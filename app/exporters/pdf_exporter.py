"""通过 Word 或 LibreOffice 将 DOCX 转换为 PDF。"""

from __future__ import annotations

from pathlib import Path
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
            "$ErrorActionPreference='Stop';"
            "$word=New-Object -ComObject Word.Application;"
            "$word.Visible=$false;"
            "try {"
            f"$doc=$word.Documents.Open('{source_ps}');"
            f"$doc.ExportAsFixedFormat('{target_ps}',17);"
            "$doc.Close($false)"
            "} finally {$word.Quit()}"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if completed.returncode != 0 or not target.exists():
            message = completed.stderr.strip() or completed.stdout.strip() or "Word COM 未生成 PDF"
            raise PdfExportError(message)

    @staticmethod
    def _export_with_libreoffice(source: Path, target: Path) -> None:
        candidates = [
            shutil.which("soffice"),
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        executable = next((item for item in candidates if item and Path(item).exists()), None)
        if executable is None:
            raise PdfExportError("未找到 soffice")
        with tempfile.TemporaryDirectory(prefix="exam_pdf_") as temp_dir:
            completed = subprocess.run(
                [executable, "--headless", "--convert-to", "pdf", "--outdir", temp_dir, str(source)],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            converted = Path(temp_dir) / f"{source.stem}.pdf"
            if completed.returncode != 0 or not converted.exists():
                message = completed.stderr.strip() or completed.stdout.strip() or "LibreOffice 未生成 PDF"
                raise PdfExportError(message)
            shutil.copy2(converted, target)
