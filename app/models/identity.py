"""Stable identifiers for imported exam blocks and questions."""

from __future__ import annotations

from typing import Any


def ensure_block_ids(raw_exam: dict[str, Any]) -> dict[str, Any]:
    """Add persistent ids without changing semantic exam content."""

    blocks = raw_exam.setdefault("blocks", [])
    used: set[str] = set()
    for index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("id", "")).strip() or f"block-{index}"
        if block_id in used:
            suffix = 2
            candidate = f"{block_id}-{suffix}"
            while candidate in used:
                suffix += 1
                candidate = f"{block_id}-{suffix}"
            block_id = candidate
        block["id"] = block_id
        used.add(block_id)
        question = block.get("question")
        if isinstance(question, dict):
            question.setdefault("id", f"{block_id}-question")
    return raw_exam


def block_id(block: dict[str, Any], index: int) -> str:
    """Return a stable block id, including a safe fallback for old projects."""

    value = str(block.get("id", "")).strip()
    return value or f"block-{index + 1}"


__all__ = ["block_id", "ensure_block_ids"]
