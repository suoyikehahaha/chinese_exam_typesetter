"""Optional print proof of the exact DOCX produced for the current edit revision."""
from concurrent.futures import CancelledError
from pathlib import Path
from tempfile import TemporaryDirectory

from .current_pipeline import build_documents
from .exporters.pdf_exporter import PdfExporter, PdfExportError


def rasterize_pdf(pdf_path, output_dir, cancel_event=None):
    import pypdfium2 as pdfium

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    with pdfium.PdfDocument(str(pdf_path)) as document:
        for index in range(len(document)):
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("打印校对已取消")
            page = document[index]
            try:
                bitmap = page.render(scale=1.65)
                try:
                    target = output_dir / f"page-{index + 1}.png"
                    with bitmap.to_pil() as image:
                        image.save(target)
                    pages.append(target)
                finally:
                    bitmap.close()
            finally:
                page.close()
    return pages


def render_print_proof(raw, layout, template, directory, cancel_event):
    # Conversion inputs and print intermediates belong to this single request.
    with TemporaryDirectory(dir=directory, prefix="print-source-") as folder:
        docx, _, _ = build_documents(raw, layout, folder, "proof", template_path=template)
        if cancel_event.is_set():
            raise CancelledError("打印校对已取消")
        try:
            pdf, engine = PdfExporter().export(docx, Path(folder) / "proof.pdf")
        except PdfExportError as exc:
            raise RuntimeError(f"打印校对需要本机 Word 或 LibreOffice。可继续使用快速预览。\n{exc}") from exc
        pages = rasterize_pdf(pdf, Path(directory) / "print-pages", cancel_event)
        if not pages:
            raise ValueError("办公软件未生成可校对的页面，请重新尝试。")
        return tuple(pages), {"microsoft-word": "Microsoft Word", "libreoffice": "LibreOffice"}[engine]
