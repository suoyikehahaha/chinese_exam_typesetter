"""Preview locators for exam and answer blocks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import preview_locator_v1 as v1


def build_preview_locators(
    pdf_path: str | Path,
    raw_exam: dict[str, Any],
) -> dict[int, tuple[int, float]]:
    """Locate ordinary exam blocks plus Guangzhou-style answer blocks."""

    original = v1._block_anchor

    def anchor(block: dict[str, Any]) -> str:
        kind = block.get("type")
        if kind == "answer_section":
            return str(block.get("text", ""))
        if kind == "answer_subsection":
            return f"{block.get('name', '')}{block.get('meta', '')}"
        if kind in {"answer_question", "answer_text"}:
            header = str(block.get("header", "")).strip()
            if header:
                return header
            for entry in block.get("paragraphs", []):
                text = str(entry.get("text", "")).strip()
                if text:
                    return text
        return original(block)

    v1._block_anchor = anchor
    try:
        return v1.build_preview_locators(pdf_path, raw_exam)
    finally:
        v1._block_anchor = original


__all__ = ["build_preview_locators"]
