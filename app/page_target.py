"""Safe page-targeting helpers for the current DOCX renderer."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_TARGET_PAGES = 8
MIN_TARGET_PAGES = 1
MAX_TARGET_PAGES = 32


def get_target_pages(raw_exam: dict[str, Any]) -> int:
    """Read and clamp the requested output page count."""

    value = raw_exam.get("metadata", {}).get("target_pages", DEFAULT_TARGET_PAGES)
    try:
        return max(MIN_TARGET_PAGES, min(MAX_TARGET_PAGES, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_TARGET_PAGES


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


def spacing_scale_for_target(raw_exam: dict[str, Any]) -> float:
    """Choose a bounded spacing multiplier for the requested page count."""

    estimated = estimate_content_pages(raw_exam)
    target = get_target_pages(raw_exam)
    # Page count is roughly proportional to vertical rhythm.  Increase rhythm
    # for a short document and tighten it for an overlong document.
    ratio = target / max(estimated, 1)
    return max(0.72, min(1.8, ratio))


def adjusted_layout(layout: dict[str, Any], raw_exam: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose paragraph rhythm is adjusted within safe bounds."""

    result = deepcopy(layout)
    scale = spacing_scale_for_target(raw_exam)
    defaults = result.setdefault("defaults", {})
    base = float(defaults.get("body_line_spacing", 1.25))
    defaults["body_line_spacing"] = max(0.95, min(1.8, base * scale))
    for spec in result.get("styles", {}).values():
        if "line_spacing" in spec:
            value = float(spec["line_spacing"])
            spec["line_spacing"] = max(0.95, min(1.8, value * scale))
        if "space_before_pt" in spec:
            spec["space_before_pt"] = max(0.0, float(spec["space_before_pt"]) * scale)
        if "space_after_pt" in spec:
            spec["space_after_pt"] = max(0.0, float(spec["space_after_pt"]) * scale)
    return result


__all__ = [
    "DEFAULT_TARGET_PAGES",
    "adjusted_layout",
    "estimate_content_pages",
    "get_target_pages",
    "spacing_scale_for_target",
]
