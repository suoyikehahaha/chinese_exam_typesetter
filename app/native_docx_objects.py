"""从导入的 DOCX 中原样复制表格和图片对象。"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

from .word_document import WordTarget, edit_document, save_document
from docx.image.exceptions import UnrecognizedImageError
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.part import Part
from docx.oxml.ns import qn
from difflib import SequenceMatcher
from docx.text.run import Run
from .resource_anchors import bound_paragraph
from .paragraph_identity import paragraph_identity
from .source_document import source_document


def restore_native_objects(docx_path: WordTarget, raw_exam: dict[str, Any], *, defer_missing: bool = False) -> None:
    """恢复原文件中的表格 XML、图片数据、尺寸与定位参数。"""

    metadata = raw_exam.get("metadata", {})
    objects = metadata.get("native_objects", [])
    if not objects:
        return
    source = source_document(metadata, answer=raw_exam.get("document_kind") == "answer")
    if source is None:
        raise ValueError("无法读取原始 Word 文件中的图片或表格，请恢复原文件的位置，或重新导入后再导出。")
    target = edit_document(docx_path)
    restored = getattr(target.part, "restored_native", set())
    for spec in objects:
        kind = spec.get("kind")
        index = spec.get("source_table_index" if kind == "table" else "source_paragraph_index")
        key = (kind, index)
        if key in restored:
            continue
        if kind == "table":
            done = _restore_table(source, target, spec, defer_missing)
        elif kind == "drawing" and not defer_missing:
            done = _restore_drawing(source, target, spec)
        else:
            continue
        if done:
            restored.add(key)
    target.part.restored_native = restored
    save_document(target, docx_path)


def _restore_table(source: Any, target: Any, spec: dict[str, Any], defer_missing=False) -> bool:
    index = int(spec["source_table_index"])
    if not 0 <= index < len(source.tables):
        raise ValueError(f"原文第 {index + 1} 个表格无法读取，请重新导入。")
    paragraph = _find_paragraph(target, str(spec.get("marker", "")))
    if paragraph is None:
        if defer_missing:
            return False
        if any(b.get("type") == "answer_table" and b.get("source_table_index") == index for b in getattr(target.part, "rendered_answer_tables", ())):
            return True
        raise ValueError(f"表格 {index + 1} 缺少放置位置，请核对原文并重新导入。")
    table_element = deepcopy(source.tables[index]._element)
    _remap_image_relationships(table_element, source.part, target.part)
    paragraph._p.addnext(table_element)
    paragraph._element.getparent().remove(paragraph._element)
    return True


def _restore_drawing(source: Any, target: Any, spec: dict[str, Any]) -> bool:
    index = int(spec["source_paragraph_index"])
    if not 0 <= index < len(source.paragraphs):
        raise ValueError(f"原文第 {index + 1} 段的图片无法读取，请重新导入。")
    source_paragraph = source.paragraphs[index]
    target_paragraph = bound_paragraph(target, spec.get("object_ref", ()))
    if target_paragraph is None:
        target_paragraph = _find_paragraph(target, str(spec.get("marker") or spec.get("target_text") or ""), int(spec.get("occurrence", 0)))
    if target_paragraph is None:
        raise ValueError(f"原文第 {index + 1} 段的图片缺少放置位置，请核对该段后重新导入。")
    if spec.get("marker"):
        for run in list(target_paragraph.runs):
            target_paragraph._p.remove(run._r)
    matcher = SequenceMatcher(None, source_paragraph.text, target_paragraph.text, autojunk=False)
    matches = matcher.get_matching_blocks()
    cursor = 0
    for node in source_paragraph._p.xpath("./w:r/* | ./w:hyperlink/w:r/*"):
        if node.tag == qn("w:t"):
            cursor += len(node.text or "")
        elif node.tag in {qn("w:tab"), qn("w:br"), qn("w:cr")}:
            cursor += 1
        elif (node.tag in {qn("w:drawing"), qn("w:pict")} or
              node.tag.endswith("}AlternateContent") and any(n.tag in {qn("w:drawing"), qn("w:pict")} for n in node.iter())):
            copied = deepcopy(node)
            _remap_image_relationships(copied, source.part, target.part)
            a, b, size = next((span for span in matches if cursor <= span.a + span.size), matches[-1])
            _insert_drawing(target_paragraph, copied, b + max(0, min(size, cursor - a)))
    return True


def _insert_drawing(paragraph, drawing, offset):
    before = 0
    for run in paragraph.runs:
        value = run.text
        if value and offset < before + len(value):
            cut = max(0, offset - before)
            image_run = paragraph.add_run()
            image_run._r.append(drawing)
            if cut:
                tail = deepcopy(run._r)
                Run(tail, paragraph).text = value[cut:]
                run.text = value[:cut]
                run._r.addnext(tail)
                tail.addprevious(image_run._r)
            else:
                run._r.addprevious(image_run._r)
            return
        before += len(value)
    paragraph.add_run()._r.append(drawing)


def _remap_image_relationships(element: Any, source_part: Any, target_part: Any) -> None:
    for blip in element.iter():
        if blip.tag not in {qn("a:blip"), "{urn:schemas-microsoft-com:vml}imagedata"}:
            continue
        attribute = qn("r:embed") if blip.tag == qn("a:blip") else qn("r:id")
        old_id = blip.get(attribute)
        if not old_id or old_id not in source_part.related_parts:
            continue
        image_part = source_part.related_parts[old_id]
        try:
            new_id, _ = target_part.get_or_add_image(BytesIO(image_part.blob))
        except UnrecognizedImageError:
            new_id = _copy_opaque_image_part(image_part, target_part)
        blip.set(attribute, new_id)


def _copy_opaque_image_part(source_image_part: Any, target_part: Any) -> str:
    """Copy an Office-supported image without asking python-docx to decode it."""

    source_name = str(source_image_part.partname)
    suffix = Path(source_name).suffix.lower() or ".bin"
    partname = target_part.package.next_partname(
        f"/word/media/image%d{suffix}"
    )
    copied = Part(
        partname,
        str(source_image_part.content_type),
        bytes(source_image_part.blob),
        target_part.package,
    )
    return target_part.relate_to(copied, RT.IMAGE)


def _find_paragraph(document: Any, text: str, occurrence: int = 0) -> Any | None:
    from docx.text.paragraph import Paragraph
    key = paragraph_identity(text)
    for node in document.element.body.xpath(".//w:p"):
        paragraph = Paragraph(node, document)
        if key == paragraph_identity(paragraph.text):
            if occurrence == 0:
                return paragraph
            occurrence -= 1
    return None
