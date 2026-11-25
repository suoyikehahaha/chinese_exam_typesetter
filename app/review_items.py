"""待核对事项提取逻辑（与界面框架解耦）。"""

from __future__ import annotations

from typing import Any, Iterable

from .score_summary import question_leaf_score


def review_items(
    raw: dict[str, Any],
    preview_warnings: Iterable[str] = (),
) -> list[tuple[str, int | None]]:
    """提取当前文档中需要教师核对的分值、结构与预览提示条目。"""

    blocks = raw.get("blocks", [])
    rows: list[tuple[str, int | None]] = []
    ids = {
        block.get("id"): index
        for index, block in enumerate(blocks)
        if isinstance(block, dict) and block.get("id")
    }
    questions = [
        (index, block["question"])
        for index, block in enumerate(blocks)
        if isinstance(block, dict) and block.get("type") == "question"
    ]
    for index, question in questions:
        if question.get("counts_toward_total", True) and question_leaf_score(question) is None:
            rows.append((f"第 {question.get('number', '?')} 题 · 待填写分值", index))
    if not questions and raw.get("document_kind") != "answer":
        rows.append(("识别到 0 道编号题，请核对文档结构。", None))
    for item in raw.get("diagnostics", []):
        if isinstance(item, dict):
            rows.append(
                (str(item.get("message") or item.get("code", "识别提示")), ids.get(item.get("block")))
            )
        else:
            rows.append((str(item), None))
    rows.extend((str(message), None) for message in preview_warnings)
    return rows


__all__ = ["review_items"]
