"""Word 中文标点和换行兼容设置。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def enable_chinese_typography(docx_path: str | Path) -> None:
    """启用中文禁则和行末标点溢出。"""

    target = Path(docx_path)
    document = Document(target)
    settings = document.settings.element
    compat = settings.find(qn("w:compat"))
    if compat is None:
        compat = OxmlElement("w:compat")
        settings.append(compat)
    for name in ("kinsoku", "overflowPunct"):
        if compat.find(qn(f"w:{name}")) is None:
            compat.append(OxmlElement(f"w:{name}"))
    document.save(target)
