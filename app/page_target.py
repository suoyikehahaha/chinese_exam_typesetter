"""Compatibility for older callers and drafts that previously used page-count targets."""
from copy import deepcopy
from typing import Any


DEFAULT_TARGET_PAGES = 0
MIN_SPACING_SCALE = MAX_SPACING_SCALE = 1.0


def use_natural_pagination(metadata):
    # Zero also tells older releases to respect the document's natural length.
    metadata["target_pages"] = 0
    metadata.pop("page_spacing_scale", None)


def get_target_pages(raw_exam):
    return 0


def spacing_scale_for_target(raw_exam, measured_pages=None, current_scale=None):
    return 1.0


def adjusted_layout(layout, raw_exam):
    return deepcopy(layout)


def estimate_content_pages(raw_exam: dict[str, Any]) -> int:
    """Estimate pages from Chinese line capacity without launching Office."""

    chars = 0
    paragraphs = 0
    for block in raw_exam.get("blocks", []):
        block_type = str(block.get("type", ""))
        if block_type in {"section_title", "instruction"}:
            chars += len(str(block.get("text", "")))
            paragraphs += 1
        elif block_type == "subsection":
            chars += len(str(block.get("name", ""))) + len(str(block.get("meta", "")))
            paragraphs += 1
        elif block_type == "question":
            question = block.get("question", {})
            chars += len(str(question.get("stem", ""))) + 8
            paragraphs += 1 + len(question.get("options", []))
            paragraphs += len(question.get("embedded_segments", []))
            paragraphs += len(question.get("subquestions", []))
            paragraphs += len(question.get("composition_material", []))
            paragraphs += len(question.get("composition_prompt", []))
            paragraphs += len(question.get("composition_requirements", []))
            chars += sum(len(str(option)) for option in question.get("options", []))
        else:
            values = [block.get("title", ""), block.get("author", ""), block.get("note", ""), block.get("source", "")]
            values.extend(block.get("paragraphs", []))
            chars += sum(len(str(value)) for value in values)
            paragraphs += sum(1 for value in values if str(value).strip())
    metadata = raw_exam.get("metadata", {})
    chars += sum(len(str(metadata.get(key, ""))) for key in ("exam_name", "subject_name", "meta_text"))
    notices = [str(item) for item in metadata.get("notices", []) if str(item).strip()]
    chars += sum(len(item) for item in notices)
    paragraphs += 3 + len(notices)
    return max(1, (chars + 1499) // 1500, (paragraphs + 47) // 48)
