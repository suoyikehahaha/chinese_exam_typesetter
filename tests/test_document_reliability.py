from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from PIL import Image

from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.drafts import DraftRecovery, read_draft, save_draft
from app.github_updater import GITHUB_REPOSITORY, UpdateInfo, download_release_asset
from app.inspector_model import all_content_objects, set_content_object_text
from app.internal_preview import render_internal_preview
from app.preview_service import PreviewService
from app.table_preview import grid_geometry, render_table_image

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "templates/layout.yaml"


def illustrated_file(folder, *, answer=False):
    source = folder / ("参考答案.docx" if answer else "局部训练.docx")
    doc = Document()
    doc.add_paragraph("语文参考答案" if answer else "语文局部训练")
    doc.add_paragraph("一、古诗文阅读")
    p = doc.add_paragraph("1．结合材料说明" if not answer else "1．结合材料说明。")
    if answer:
        p = doc.add_paragraph("解析：请核对")
    mark = p.add_run("关键句")
    mark.underline = True
    dot = OxmlElement("w:em")
    dot.set(qn("w:val"), "underDot")
    mark._r.get_or_add_rPr().append(dot)
    image = BytesIO()
    Image.new("RGB", (24, 16), (210, 17, 98)).save(image, "PNG")
    p.add_run().add_picture(BytesIO(image.getvalue()))
    p.add_run("的含义。" if answer else "的含义。（6分）")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "校对表"
    table.cell(1, 1).text = "完整内容"
    doc.save(source)
    return source


@pytest.mark.parametrize("answer", [False, True])
@pytest.mark.parametrize("edit", ["追加", "前插", "删除"])
def test_moving_source_and_editing_keeps_illustrations_and_surviving_decorations(tmp_path, edit, answer):
    source = illustrated_file(tmp_path, answer=answer)
    original = sha256(source.read_bytes()).hexdigest()
    raw = import_exam(source)
    item = next(o for o in all_content_objects(raw) if "关键句" in o.text)
    text = {"追加": item.text + "新增文字", "前插": "新增文字" + item.text,
            "删除": item.text.replace("关键句", "关键")}[edit]
    set_content_object_text(raw, item, text)
    raw["metadata"]["target_pages"] = 0
    draft = save_draft(raw, tmp_path / "draft.json")
    moved = source.rename(tmp_path / "moved.docx")
    raw = read_draft(draft)
    assert len(raw["metadata"]["source_blobs"]) == 1
    out, _, _ = build_documents(raw, LAYOUT, tmp_path / "out", "result")
    with ZipFile(out) as z:
        assert z.testzip() is None
    doc = Document(out)
    assert len(doc.inline_shapes) == 1
    assert len(doc.tables) == 1 and doc.tables[0].cell(1, 1).text == "完整内容"
    p = next(p for p in doc.paragraphs if "关键" in p.text)
    assert text in p.text
    marked = "".join(run.text for run in p.runs if run.underline and run._r.xpath("./w:rPr/w:em"))
    assert marked == ("关键" if edit == "删除" else "关键句")
    left = ""
    for run in p.runs:
        if run._r.xpath("./w:drawing"):
            break
        left += run.text
    assert left.endswith(marked)
    preview = render_internal_preview(raw, LAYOUT, tmp_path / "preview")
    for path in preview.pages:
        with Image.open(path) as page:
            if any(color == (210, 17, 98) for _, color in page.convert("RGB").getcolors(page.width * page.height)):
                break
    else:
        pytest.fail("原图未显示在预览中")
    assert sha256(moved.read_bytes()).hexdigest() == original


def test_atomic_draft_failure_and_legacy_draft(tmp_path):
    source = illustrated_file(tmp_path)
    from json import dumps
    raw = import_exam(source)
    for item in raw["metadata"]["native_objects"] + raw["metadata"]["source_decorations"]:
        item.pop("object_ref", None)
    path = tmp_path / "old.json"
    path.write_text(dumps(raw), encoding="utf-8")
    assert read_draft(path)["metadata"]["native_objects"][0]["object_ref"]
    good = path.read_bytes()
    with patch.object(Path, "replace", side_effect=PermissionError("locked")), pytest.raises(PermissionError):
        save_draft(raw, path)
    assert path.read_bytes() == good
    assert not list(tmp_path.glob(".exam-draft-*"))


def test_live_recovery_files_are_exclusive_and_complete(tmp_path):
    raw = import_exam(illustrated_file(tmp_path))
    active = DraftRecovery(tmp_path / "recovery")
    observer = DraftRecovery(active.directory)
    try:
        errors = []
        active.save(raw, errors.append)
        active._last.result(10)
        assert not errors
        path, = observer.candidates()
        with observer.claim_abandoned(path) as acquired:
            assert not acquired
        assert read_draft(path)["metadata"]["source_blobs"]
        active.clear()
    finally:
        active.close()
        observer.close()
    assert not list(active.directory.glob("*"))


def test_buffers_live_until_installed_and_cache_reuses_pages(tmp_path):
    raw = import_exam(illustrated_file(tmp_path))
    service = PreviewService()
    def submit():
        got, ready = [], Event()
        service.submit(raw, LAYOUT, None, lambda x: (got.append(x), ready.set()))
        assert ready.wait(15)
        assert not isinstance(got[0], Exception), got
        return got[0]
    try:
        first = submit()
        cached = submit()
        assert first.pages == cached.pages
        service.acknowledge(cached.generation)
        assert first.pages[0].exists()
        raw["metadata"]["exam_name"] += "新稿"
        next_ = submit()
        assert next_.pages != first.pages
        assert first.pages[0].exists()
        assert next_.page_info[0][2] == sha256(next_.pages[0].read_bytes()).hexdigest()
        service.acknowledge(next_.generation)
        assert not first.pages[0].exists()
    finally:
        service.close()


def test_real_table_grid_spans_and_paragraph_spacing():
    doc = Document()
    table = doc.add_table(rows=3, cols=3)
    for col, size in zip(table.columns, [1, 2, 3]):
        col.width = Inches(size)
    cell = table.cell(0, 0).merge(table.cell(1, 1))
    cell.text = "甲"
    edges, spans = grid_geometry(table, 600)
    assert edges == [0, 100, 300, 600]
    assert any((r0, r1, c0, c1) == (0, 1, 0, 1) for _, r0, r1, c0, c1 in spans)
    a = render_table_image(table, max_width=600)
    assert a.getpixel((100, 8))[:3] == (255, 255, 255)
    assert a.getpixel((300, 8))[:3] != (255, 255, 255)
    cell.paragraphs[0].paragraph_format.space_before = Pt(42)
    cell.paragraphs[0].runs[0].font.size = Pt(24)
    assert render_table_image(table, max_width=600).height > a.height


@pytest.mark.parametrize("mode", ["api", "manifest", "mismatch", "missing"])
def test_download_checks_real_digest_and_cleans_failed_file(tmp_path, mode):
    payload = b"packaged application"
    name = "ChineseExamTypesetter_0.3.0.exe"
    base = GITHUB_REPOSITORY + "/releases/download/v0.3.0/"
    expected = sha256(payload).hexdigest()
    info = UpdateInfo("0.3.0", "", "", name, base+name, len(payload), "", True,
                      "sha256:" + expected if mode == "api" else "sha256:"+"0"*64 if mode == "mismatch" else "",
                      base+"SHA256SUMS.txt" if mode == "manifest" else "")
    def open_url(request, **kwargs):
        return BytesIO((expected+"  "+name+"\n").encode() if request.full_url.endswith(".txt") else payload)
    folder = tmp_path / "download"
    def make_folder(**kwargs):
        folder.mkdir()
        return str(folder)
    with patch("app.github_updater.urlopen", side_effect=open_url), patch("app.github_updater.tempfile.mkdtemp", side_effect=make_folder):
        if mode in {"missing", "mismatch"}:
            with pytest.raises((RuntimeError, ValueError), match="SHA-256"):
                download_release_asset(info)
            assert not folder.exists()
        else:
            assert download_release_asset(info).read_bytes() == payload

def test_word_vector_shape_keeps_choice_and_fallback_after_text_edit(tmp_path):
    from lxml import etree
    source = tmp_path / "绘图.docx"
    doc = Document()
    doc.add_paragraph("局部训练")
    doc.add_paragraph("一、阅读")
    p = doc.add_paragraph("1．图文题干")
    p.add_run("修订").underline = True
    comp = etree.fromstring(b'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml"><mc:Choice Requires="w"><w:drawing/></mc:Choice><mc:Fallback><w:pict><v:rect id="testShape"/></w:pict></mc:Fallback></mc:AlternateContent>')
    p.add_run()._r.append(comp)
    p.add_run("需要校对。（4分）")
    doc.save(source)
    raw = import_exam(source)
    item = next(o for o in all_content_objects(raw) if "图文题干" in o.text)
    set_content_object_text(raw, item, "新" + item.text)
    output, _, _ = build_documents(raw, LAYOUT, tmp_path, "out")
    body = Document(output).element.body
    assert len(body.xpath(".//*[local-name()='AlternateContent']")) == 1
    assert len(body.xpath(".//w:drawing | .//w:pict")) == 2
    assert any("绘图" in warning for warning in render_internal_preview(raw, LAYOUT, tmp_path/"preview").warnings)

def test_print_proof_uses_final_docx_and_discards_private_print_files(tmp_path):
    from threading import Event
    from pypdf import PdfWriter
    from app.print_proof import render_print_proof
    from docx import Document
    source = illustrated_file(tmp_path)
    raw = import_exam(source)
    raw["metadata"]["page_spacing_scale"] = 0.92
    def convert(docx, pdf):
        doc = Document(docx)
        assert any("关键句" in p.text for p in doc.paragraphs)
        assert len(doc.inline_shapes) == len(doc.tables) == 1
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(195, 250)
        writer.write(pdf)
        return pdf, "microsoft-word"
    folder = tmp_path/"private"
    folder.mkdir()
    with patch("app.print_proof.PdfExporter.export", side_effect=convert):
        pages, engine = render_print_proof(raw, LAYOUT, None, folder, Event())
    assert engine == "Microsoft Word" and len(pages) == 3 and all(p.exists() for p in pages)
    assert not list(folder.rglob("*.pdf")) and not list(folder.rglob("*.docx"))


def test_a_slow_print_proof_never_blocks_or_replaces_a_new_quick_preview(tmp_path):
    from threading import Event
    from concurrent.futures import CancelledError
    started, release, completed = Event(), Event(), Event()
    raw = import_exam(illustrated_file(tmp_path))
    raw["metadata"]["page_spacing_scale"] = 1.0
    service = PreviewService()
    old, new = [], []
    def wait_for_engine(raw, layout, template, directory, cancelled):
        started.set()
        release.wait(12)
        assert cancelled.is_set()
        raise CancelledError()
    try:
        with patch("app.print_proof.render_print_proof", side_effect=wait_for_engine):
            service.submit(raw, LAYOUT, None, old.append, print_proof=True)
            assert started.wait(5)
            raw["metadata"]["exam_name"] = "修改后的试卷"
            service.submit(raw, LAYOUT, None, lambda r: (new.append(r), completed.set()))
            assert completed.wait(8)
            assert new[0].engine == "internal" and new[0].pages
            release.set()
    finally:
        release.set()
        service.close()
    assert not old
