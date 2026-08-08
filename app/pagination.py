"""生成后的分页保护。"""

from __future__ import annotations

from pathlib import Path

from docx import Document


FOLLOWING_CONTENT_STYLES = {
    "Exam_embedded_body",
    "Exam_segmentation_text",
    "Exam_subquestion",
    "Exam_composition_material",
}


def apply_pagination_guards(path: str | Path) -> None:
    """保护诗歌、作文和题内材料，避免题干或短内容组跨页。"""

    target = Path(path)
    document = Document(target)
    paragraphs = document.paragraphs
    for index, paragraph in enumerate(paragraphs):
        next_style = paragraphs[index + 1].style.name if index + 1 < len(paragraphs) else ""
        if (
            paragraph.style.name == "Exam_subjective_stem"
            and next_style in FOLLOWING_CONTENT_STYLES
        ):
            paragraph.paragraph_format.keep_with_next = True
        if paragraph.style.name == "Exam_poetry":
            paragraph.paragraph_format.keep_with_next = next_style == "Exam_poetry"
        if paragraph.style.name == "Exam_embedded_body":
            paragraph.paragraph_format.keep_with_next = next_style == "Exam_embedded_body"
        if paragraph.style.name == "Exam_subquestion":
            paragraph.paragraph_format.keep_with_next = next_style == "Exam_subquestion"
    document.save(target)
