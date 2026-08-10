"""Page-layout overrides shared by the preview and DOCX pipeline."""

from __future__ import annotations

from typing import Any

from .page_target import adjusted_layout as adjusted_layout_base


PAGE_KEYS = (
    "margin_top_mm",
    "margin_bottom_mm",
    "margin_left_mm",
    "margin_right_mm",
)


def adjusted_layout(layout: dict[str, Any], raw_exam: dict[str, Any]) -> dict[str, Any]:
    """Apply safe user page overrides after target-page rhythm adjustment."""

    result = adjusted_layout_base(layout, raw_exam)
    overrides = raw_exam.get("metadata", {}).get("page_overrides", {})
    page = result.setdefault("page", {})
    for key in PAGE_KEYS:
        if key not in overrides:
            continue
        try:
            value = float(overrides[key])
        except (TypeError, ValueError):
            continue
        page[key] = max(5.0, min(45.0, value))
    return result


__all__ = ["PAGE_KEYS", "adjusted_layout"]
