"""Page-layout overrides shared by the preview and DOCX pipeline."""

from __future__ import annotations

from typing import Any

from .layout_presets import apply_layout_preset
from .page_target import adjusted_layout as adjusted_layout_base


PAGE_KEYS = (
    "margin_top_mm",
    "margin_bottom_mm",
    "margin_left_mm",
    "margin_right_mm",
)


def adjusted_layout(layout: dict[str, Any], raw_exam: dict[str, Any]) -> dict[str, Any]:
    """Apply safe user page overrides after target-page rhythm adjustment."""

    result = adjusted_layout_base(apply_layout_preset(layout, raw_exam), raw_exam)
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


def adjust_inline_spacing(raw_exam: dict[str, Any], scale: float) -> None:
    """Apply identical bounded spacing to preview and export copies."""
    for block in raw_exam.get("blocks", []):
        owners = [block]
        if block.get("type") == "question":
            owners.append(block.get("question", {}))
        for owner in owners:
            specs = list(owner.get("paragraph_formats", []))
            if isinstance(owner.get("format"), dict):
                specs.append(owner["format"])
            for spec in specs:
                if "line_spacing" in spec:
                    spec["line_spacing"] = max(0.95, min(1.8, float(spec["line_spacing"]) * scale))
                for key in ("space_before_pt", "space_after_pt"):
                    if key in spec:
                        spec[key] = max(0.0, float(spec[key]) * scale)


__all__ = ["PAGE_KEYS", "adjusted_layout", "adjust_inline_spacing"]
