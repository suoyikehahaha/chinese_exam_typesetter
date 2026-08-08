"""Content-object model for the v0.4 contextual inspector.

The desktop UI edits one semantic object at a time while the structured exam
dictionary remains the only export source.  This module contains no Tk code so
object selection, batch formatting and preview positioning can be tested
without opening a window.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
import re
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ContentObject:
    """One independently editable semantic object in the exam structure."""

    key: str
    block_index: int | None
    target: str
    target_index: int
    role: str
    label: str
    text: str
    line_index: int

    @property
    def type_signature(self) -> tuple[str, str]:
        """Return the stable signature used by batch formatting."""

        return self.target, self.role


ROLE_LABELS = {
    "exam_name": "试卷名称",
    "subject_name": "科目名称",
    "exam_meta": "试卷说明",
    "notice_title": "须知标题",
    "notice": "须知",
    "section_title": "大题标题",
    "subsection_name": "阅读模块",
    "subsection_meta": "题数与分值",
    "instruction": "阅读提示",
    "material_title": "材料标题",
    "material_author": "作者",
    "material_body": "材料正文",
    "material_note": "注释",
    "material_source": "出处",
    "poetry_line": "诗歌正文",
    "question_stem": "题干",
    "choice_option": "选择项",
    "embedded": "题内材料",
    "segmentation": "断句材料",
    "subquestion": "题内小题",
    "composition_material": "作文材料",
    "composition_prompt": "作文引导语",
    "composition_requirements": "写作要求",
    "answer_header": "答案题号",
    "answer_body": "答案正文",
    "structure_text": "结构内容",
}


def object_key(block_index: int | None, target: str, target_index: int = 0) -> str:
    """Build a stable key accepted by the preview locator map."""

    owner = "meta" if block_index is None else str(block_index)
    return f"obj:{owner}:{target}:{target_index}"


def metadata_content_objects(raw_exam: Mapping[str, Any]) -> list[ContentObject]:
    """Return editable objects for the document header."""

    metadata = raw_exam.get("metadata", {})
    result = [
        _object(None, "exam_name", 0, "exam_name", str(metadata.get("exam_name", "")), 0),
        _object(None, "subject_name", 0, "subject_name", str(metadata.get("subject_name", "")), 1),
        _object(None, "meta_text", 0, "exam_meta", str(metadata.get("meta_text", "")), 2),
    ]
    for index, value in enumerate(metadata.get("notices", [])):
        result.append(_object(None, "notice", index, "notice", str(value), 4 + index))
    return result


def content_objects_for_block(block: Mapping[str, Any], block_index: int) -> list[ContentObject]:
    """Split one structure block into independently selectable objects."""

    kind = str(block.get("type", ""))
    if kind in {"section_title", "instruction", "answer_section"}:
        role = "section_title" if kind == "section_title" else (
            "instruction" if kind == "instruction" else "structure_text"
        )
        return [_object(block_index, "text", 0, role, str(block.get("text", "")), 0)]
    if kind in {"subsection", "answer_subsection"}:
        result = [
            _object(block_index, "name", 0, "subsection_name", str(block.get("name", "")), 0)
        ]
        if str(block.get("meta", "")).strip():
            result.append(
                _object(block_index, "meta", 0, "subsection_meta", str(block.get("meta", "")), 0)
            )
        return result
    if kind in {"material", "poetry"}:
        return _material_objects(block, block_index)
    if kind == "question":
        return _question_objects(block.get("question", {}), block_index)
    if kind in {"answer_question", "answer_text"}:
        result: list[ContentObject] = []
        if block.get("header"):
            result.append(_object(block_index, "header", 0, "answer_header", str(block["header"]), 0))
        for index, paragraph in enumerate(block.get("paragraphs", [])):
            text = str(paragraph.get("text", "")) if isinstance(paragraph, Mapping) else str(paragraph)
            result.append(_object(block_index, "answer_paragraph", index, "answer_body", text, len(result)))
        return result
    return []


def all_content_objects(raw_exam: Mapping[str, Any]) -> list[ContentObject]:
    """Return header and block objects in document order."""

    result = metadata_content_objects(raw_exam)
    for index, block in enumerate(raw_exam.get("blocks", [])):
        if isinstance(block, Mapping):
            result.extend(content_objects_for_block(block, index))
    return result


def _material_objects(block: Mapping[str, Any], block_index: int) -> list[ContentObject]:
    result: list[ContentObject] = []
    line_index = 0
    if block.get("title"):
        result.append(_object(block_index, "title", 0, "material_title", str(block["title"]), line_index))
        line_index += 1
    if block.get("author"):
        result.append(_object(block_index, "author", 0, "material_author", str(block["author"]), line_index))
        line_index += 1
    roles = [str(value) for value in block.get("paragraph_roles", [])]
    for index, value in enumerate(block.get("paragraphs", [])):
        role = roles[index] if index < len(roles) else "body"
        text = str(value)
        if role in {"source", "publication_note"} or _looks_like_source(text):
            semantic_role = "material_source"
        elif role == "author":
            semantic_role = "material_author"
        elif _looks_like_material_label(text):
            semantic_role = "material_title"
        else:
            semantic_role = "poetry_line" if block.get("type") == "poetry" else "material_body"
        result.append(_object(block_index, "paragraph", index, semantic_role, text, line_index))
        line_index += 1
    if block.get("note"):
        result.append(_object(block_index, "note", 0, "material_note", str(block["note"]), line_index))
        line_index += 1
    if block.get("source"):
        result.append(_object(block_index, "source", 0, "material_source", str(block["source"]), line_index))
    return result


def _question_objects(question: Mapping[str, Any], block_index: int) -> list[ContentObject]:
    result = [
        _object(block_index, "stem", 0, "question_stem", str(question.get("stem", "")), 0)
    ]
    for index, value in enumerate(question.get("options", [])):
        result.append(_object(block_index, "option", index, "choice_option", str(value), len(result)))
    for index, segments in enumerate(question.get("embedded_segments", [])):
        text = "".join(str(item.get("text", "")) for item in segments)
        result.append(_object(block_index, "embedded", index, "embedded", text, len(result)))
    if question.get("segmentation_text"):
        result.append(
            _object(block_index, "segmentation", 0, "segmentation", str(question["segmentation_text"]), len(result))
        )
    for index, value in enumerate(question.get("subquestions", [])):
        result.append(_object(block_index, "subquestion", index, "subquestion", str(value), len(result)))
    for target, role in (
        ("composition_material", "composition_material"),
        ("composition_prompt", "composition_prompt"),
        ("composition_requirements", "composition_requirements"),
    ):
        for index, value in enumerate(question.get(target, [])):
            result.append(_object(block_index, target, index, role, str(value), len(result)))
    return result


def _object(
    block_index: int | None,
    target: str,
    target_index: int,
    role: str,
    text: str,
    line_index: int,
) -> ContentObject:
    return ContentObject(
        object_key(block_index, target, target_index),
        block_index,
        target,
        target_index,
        role,
        ROLE_LABELS.get(role, role),
        text,
        line_index,
    )


def set_content_object_text(raw_exam: dict[str, Any], item: ContentObject, value: str) -> None:
    """Commit one object without rewriting sibling content."""

    if item.block_index is None:
        metadata = raw_exam.setdefault("metadata", {})
        if item.target == "notice":
            notices = metadata.setdefault("notices", [])
            if item.target_index < len(notices):
                notices[item.target_index] = value
        else:
            metadata[item.target] = value
        return
    blocks = raw_exam.get("blocks", [])
    if not 0 <= item.block_index < len(blocks):
        return
    block = blocks[item.block_index]
    if block.get("type") == "question":
        _set_question_text(block.setdefault("question", {}), item, value)
        return
    if item.target == "paragraph":
        values = block.setdefault("paragraphs", [])
        if item.target_index < len(values):
            values[item.target_index] = value
    elif item.target == "answer_paragraph":
        values = block.setdefault("paragraphs", [])
        if item.target_index < len(values):
            if isinstance(values[item.target_index], dict):
                values[item.target_index]["text"] = value
            else:
                values[item.target_index] = value
    else:
        block[item.target] = value


def _set_question_text(question: dict[str, Any], item: ContentObject, value: str) -> None:
    if item.target == "stem":
        question["stem"] = value
    elif item.target == "option":
        question.setdefault("options", [])[item.target_index] = value
    elif item.target == "embedded":
        segments = question.setdefault("embedded_segments", [])[item.target_index]
        old_label = "".join(str(part.get("text", "")) for part in segments if part.get("role") == "label")
        label = old_label if old_label and value.startswith(old_label) else _split_label(value)[0]
        body = value[len(label):] if label else value
        question["embedded_segments"][item.target_index] = (
            [{"text": label, "role": "label"}, {"text": body, "role": "body"}]
            if label
            else [{"text": body, "role": "body"}]
        )
    elif item.target == "segmentation":
        question["segmentation_text"] = value
    else:
        values = question.setdefault(item.target, [])
        if item.target_index < len(values):
            values[item.target_index] = value


def _split_label(value: str) -> tuple[str, str]:
    match = re.match(r"^(.{1,18}?[：:])\s*(.*)$", value, re.S)
    return (match.group(1), match.group(2)) if match else ("", value)


def format_owner(raw_exam: dict[str, Any], item: ContentObject) -> dict[str, Any] | None:
    """Return the mapping that owns paragraph and inline overrides."""

    if item.block_index is None:
        return None
    block = raw_exam.get("blocks", [])[item.block_index]
    return block.get("question", {}) if block.get("type") == "question" else block


def paragraph_format_for(raw_exam: dict[str, Any], item: ContentObject) -> dict[str, Any]:
    """Return the effective format for one object."""

    owner = format_owner(raw_exam, item)
    default = default_format_for(item)
    if owner is None:
        return default
    for entry in owner.get("paragraph_formats", []):
        if _format_entry_matches(entry, item):
            return {**default, **entry}
    if item.role == "question_stem":
        return {**default, **owner.get("format", {})}
    if item.role == "choice_option":
        spec = owner.get("format", {})
        return {
            **default,
            "font": spec.get("option_font", default["font"]),
            "size_pt": spec.get("option_size_pt", default["size_pt"]),
            "left_indent_chars": spec.get("option_left_indent_chars", 1.5),
            "special_indent": "悬挂",
            "special_indent_chars": spec.get("option_hanging_indent_chars", 1.7),
        }
    return default


def set_paragraph_format(raw_exam: dict[str, Any], item: ContentObject, spec: Mapping[str, Any]) -> None:
    """Store one object format in the paragraph override list."""

    owner = format_owner(raw_exam, item)
    if owner is None:
        return
    entry = {"target": item.target, "target_index": item.target_index, **dict(spec)}
    if item.block_index is not None and raw_exam["blocks"][item.block_index].get("type") != "question":
        entry["target"] = "block"
        entry["target_index"] = item.line_index
    values = [
        current
        for current in owner.get("paragraph_formats", [])
        if not _format_entry_matches(current, item)
    ]
    values.append(entry)
    owner["paragraph_formats"] = values
    if item.role == "question_stem":
        owner["format"] = {**owner.get("format", {}), **dict(spec)}
    elif item.role == "choice_option":
        base = owner.setdefault("format", {})
        base.update(
            {
                "option_font": spec.get("font", "宋体"),
                "option_size_pt": spec.get("size_pt", 10.5),
                "option_left_indent_chars": spec.get("left_indent_chars", 1.5),
                "option_hanging_indent_chars": spec.get("special_indent_chars", 1.7),
            }
        )


def remove_paragraph_format(raw_exam: dict[str, Any], item: ContentObject) -> None:
    """Remove the explicit object override and reveal the template value."""

    owner = format_owner(raw_exam, item)
    if owner is None:
        return
    owner["paragraph_formats"] = [
        current for current in owner.get("paragraph_formats", []) if not _format_entry_matches(current, item)
    ]


def _format_entry_matches(entry: Mapping[str, Any], item: ContentObject) -> bool:
    if item.block_index is None:
        return False
    target = item.target
    target_index = item.target_index
    if target not in {"stem", "option", "embedded", "segmentation", "subquestion", "composition_material", "composition_prompt", "composition_requirements"}:
        target, target_index = "block", item.line_index
    return str(entry.get("target", "")) == target and int(entry.get("target_index", 0)) == target_index


def inline_formats_for(raw_exam: dict[str, Any], item: ContentObject) -> list[dict[str, Any]]:
    """Return character formats belonging to the selected object."""

    owner = format_owner(raw_exam, item)
    if owner is None:
        return []
    return [dict(entry) for entry in owner.get("inline_formats", []) if _inline_entry_matches(entry, item)]


def set_inline_format(
    raw_exam: dict[str, Any],
    item: ContentObject,
    start: int,
    end: int,
    spec: Mapping[str, Any],
) -> None:
    """Add a character format for one selected text range."""

    owner = format_owner(raw_exam, item)
    if owner is None or start >= end:
        return
    values = [
        entry
        for entry in owner.get("inline_formats", [])
        if not (_inline_entry_matches(entry, item) and int(entry.get("start", 0)) < end and int(entry.get("end", 0)) > start)
    ]
    target = item.target if item.block_index is not None and raw_exam["blocks"][item.block_index].get("type") == "question" else "block"
    target_index = item.target_index if target != "block" else item.line_index
    values.append(
        {
            "target": target,
            "target_index": target_index,
            "line": item.line_index,
            "start": start,
            "end": end,
            **dict(spec),
        }
    )
    owner["inline_formats"] = values


def _inline_entry_matches(entry: Mapping[str, Any], item: ContentObject) -> bool:
    target = item.target
    target_index = item.target_index
    if item.block_index is None:
        return False
    if target not in {"stem", "option", "embedded", "segmentation", "subquestion", "composition_material", "composition_prompt", "composition_requirements"}:
        target, target_index = "block", item.line_index
    return str(entry.get("target", "")) == target and int(entry.get("target_index", 0)) == target_index


def default_format_for(item: ContentObject) -> dict[str, Any]:
    """Return the confirmed national-volume template defaults for one role."""

    role = item.role
    font = "宋体"
    size = 10.5
    bold = False
    alignment = "左对齐"
    left = 0.0
    special = "无"
    amount = 0.0
    if role == "exam_name":
        size, alignment = 16.0, "居中"
    elif role == "subject_name":
        font, size, bold, alignment = "黑体", 22.0, True, "居中"
    elif role == "exam_meta":
        alignment = "居中"
    elif role == "section_title":
        font, size, bold = "黑体", 12.0, True
    elif role == "subsection_name":
        bold = True
    elif role in {"instruction", "notice"}:
        special, amount = "首行", 2.0
    elif role == "material_title":
        font, bold, alignment = "黑体", True, "居中"
    elif role == "material_author":
        font, alignment = "仿宋", "居中"
    elif role in {"material_body", "embedded", "composition_material"}:
        font, special, amount = "楷体", "首行", 2.0
    elif role == "poetry_line":
        font, alignment = "楷体", "居中"
    elif role == "material_note":
        font, size, special, amount = "仿宋", 9.0, "首行", 2.0
    elif role == "material_source":
        font, alignment = "仿宋", "右对齐"
    elif role == "question_stem":
        special, amount = "悬挂", 1.5
    elif role == "choice_option":
        left, special, amount = 1.5, "悬挂", 1.7
    elif role == "segmentation":
        font, special, amount = "楷体", "首行", 2.0
    elif role in {"subquestion", "composition_prompt", "composition_requirements"}:
        special, amount = "首行", 2.0
    return {
        "font": font,
        "size_pt": size,
        "bold": bold,
        "left_indent_chars": left,
        "right_indent_chars": 0.0,
        "special_indent": special,
        "special_indent_chars": amount,
        "alignment": alignment,
        "line_spacing": 1.25,
        "space_before_pt": 0.0,
        "space_after_pt": 0.0,
        "keep_with_next": False,
        "page_break_before": False,
    }


def objects_in_scope(
    raw_exam: Mapping[str, Any],
    selected: ContentObject,
    scope: str,
) -> list[ContentObject]:
    """Select same-type objects within the requested batch scope."""

    all_items = all_content_objects(raw_exam)
    if scope == "整份试卷":
        candidates = all_items
    elif scope == "当前大题":
        start, end = _major_section_bounds(raw_exam, selected.block_index)
        candidates = [item for item in all_items if item.block_index is not None and start <= item.block_index < end]
    else:
        candidates = [item for item in all_items if item.block_index == selected.block_index]
    return [item for item in candidates if item.type_signature == selected.type_signature]


def _major_section_bounds(raw_exam: Mapping[str, Any], block_index: int | None) -> tuple[int, int]:
    if block_index is None:
        return 0, len(raw_exam.get("blocks", []))
    starts = [
        index
        for index, block in enumerate(raw_exam.get("blocks", []))
        if isinstance(block, Mapping) and block.get("type") == "section_title"
    ]
    start = max((value for value in starts if value <= block_index), default=0)
    end = min((value for value in starts if value > block_index), default=len(raw_exam.get("blocks", [])))
    return start, end


def build_object_locators(
    raw_exam: Mapping[str, Any],
    block_locators: Mapping[Any, tuple[int, float]],
) -> dict[Any, tuple[int, float]]:
    """Add content-object positions between neighboring block anchors."""

    result: dict[Any, tuple[int, float]] = dict(block_locators)
    blocks = raw_exam.get("blocks", [])
    anchors = {
        int(key): (int(value[0]), float(value[1]))
        for key, value in block_locators.items()
        if isinstance(key, int)
    }
    meta_anchor = anchors.get(-1, (0, 0.0))
    meta_items = metadata_content_objects(raw_exam)
    for index, item in enumerate(meta_items):
        result[item.key] = (meta_anchor[0], min(0.32, meta_anchor[1] + index * 0.04))
    for block_index, block in enumerate(blocks):
        if block_index not in anchors or not isinstance(block, Mapping):
            continue
        items = content_objects_for_block(block, block_index)
        if not items:
            continue
        start = anchors[block_index][0] + anchors[block_index][1]
        next_anchor = _next_anchor(anchors, block_index)
        finish = (next_anchor[0] + next_anchor[1]) if next_anchor else start + min(0.26, 0.035 * len(items) + 0.03)
        finish = max(start + 0.02, finish)
        weights = [_object_weight(item.text) for item in items]
        total = max(1.0, sum(weights))
        consumed = 0.0
        for item, weight in zip(items, weights):
            point = start + (finish - start) * (consumed / total)
            page = max(0, floor(point))
            result[item.key] = (page, max(0.0, min(1.0, point - page)))
            consumed += weight
    return result


def _next_anchor(anchors: Mapping[int, tuple[int, float]], block_index: int) -> tuple[int, float] | None:
    indexes = sorted(index for index in anchors if index > block_index)
    return anchors[indexes[0]] if indexes else None


def _object_weight(text: str) -> float:
    return max(1.0, min(8.0, len(text) / 34.0 + text.count("\n") + 1.0))


def _looks_like_material_label(text: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:材料|文本|资料)[一二三四五六七八九十0-9]+[：:]?\s*", text))


def _looks_like_source(text: str) -> bool:
    return bool(re.match(r"^\s*[（(].*(?:摘|选|译|发表于|有删改|有改动).*[）)]\s*$", text))


def summary_text(item: ContentObject, limit: int = 38) -> str:
    """Return a compact one-line description for the object list."""

    value = re.sub(r"\s+", " ", item.text).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


__all__ = [
    "ContentObject",
    "all_content_objects",
    "build_object_locators",
    "content_objects_for_block",
    "default_format_for",
    "format_owner",
    "inline_formats_for",
    "metadata_content_objects",
    "object_key",
    "objects_in_scope",
    "paragraph_format_for",
    "remove_paragraph_format",
    "set_content_object_text",
    "set_inline_format",
    "set_paragraph_format",
    "summary_text",
]
