"""Read original DOCX resources, including a self-contained converted DOC."""

import base64
from io import BytesIO
from pathlib import Path

from docx import Document


def source_document(metadata: dict, *, answer: bool = False):
    embedded = metadata.get("converted_docx_base64")
    if embedded:
        try:
            return Document(BytesIO(base64.b64decode(embedded, validate=True)))
        except Exception as exc:
            raise ValueError("草稿中的 Word 转换资源已损坏，请重新导入原始 DOC 文件。") from exc
    key = "answer_source_docx_path" if answer else "source_docx_path"
    source = Path(str(metadata.get(key, "")))
    return Document(source) if source.is_file() else None
