"""Named exam-layout presets layered over the editable base configuration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PRESET_LABELS = {
    "national": "全国卷",
    "national_ii_2026": "全国Ⅱ卷模板（2026）",
    "guangzhou": "广州卷",
    "source": "保留源版式",
}

_PAGE_PRESETS: dict[str, dict[str, float]] = {
    "national": {
        "margin_top_mm": 25.4,
        "margin_bottom_mm": 25.4,
        "margin_left_mm": 31.75,
        "margin_right_mm": 31.75,
    },
    "guangzhou": {
        "margin_top_mm": 13.0,
        "margin_bottom_mm": 13.0,
        "margin_left_mm": 23.5,
        "margin_right_mm": 22.8,
    },
}


def preset_page(raw_exam: dict[str, Any], preset: str | None = None) -> dict[str, float]:
    """Return preset margins, using the imported first section for source mode."""

    metadata = raw_exam.get("metadata", {})
    key = str(preset or metadata.get("layout_preset", "national"))
    if key == "source":
        source_page = metadata.get("source_page", {})
        if isinstance(source_page, dict) and all(name in source_page for name in _PAGE_PRESETS["national"]):
            return {name: float(source_page[name]) for name in _PAGE_PRESETS["national"]}
    return dict(_PAGE_PRESETS.get(key, _PAGE_PRESETS["national"]))


def apply_layout_preset(layout: dict[str, Any], raw_exam: dict[str, Any]) -> dict[str, Any]:
    """Apply the selected preset before user overrides are considered."""

    result = deepcopy(layout)
    metadata = raw_exam.get("metadata", {})
    preset = str(metadata.get("layout_preset", "national"))
    result.setdefault("page", {}).update(preset_page(raw_exam, preset))
    styles = result.setdefault("styles", {})
    if preset == "national_ii_2026":
        result["page"].update(header_distance_mm=15, footer_distance_mm=20)
        changes = {
            "exam_meta": {"alignment": "left", "first_line_indent_chars": 2},
            "section_title": {"font": "SimHei", "size_pt": 12},
            "material_author": {"font": "KaiTi", "size_pt": 10.5},
            "material_note": {"font": "FangSong", "size_pt": 9},
            "material_source": {"font": "FangSong", "size_pt": 10.5},
            "objective_stem": {"left_indent_chars": 0, "first_line_indent_chars": 0},
            "subjective_stem": {"left_indent_chars": 0, "first_line_indent_chars": 0},
            "choice_option": {"left_indent_chars": 1.5, "first_line_indent_chars": -1.7,
                              "hanging_indent_chars": 1.7, "indent_origin": "paragraph"},
        }
        for role, values in changes.items():
            styles.setdefault(role, {}).update(values)
    elif preset == "guangzhou":
        styles.setdefault("section_title", {})["size_pt"] = 12
        styles.setdefault("material_note", {}).update({"font": "SimHei", "size_pt": 10})
    elif preset == "national":
        styles.setdefault("section_title", {})["size_pt"] = 10.5
        styles.setdefault("material_note", {}).update({"font": "SimSun", "size_pt": 10.5})
        styles.setdefault("material_source", {}).update({"font": "SimSun", "size_pt": 10.5})
    return result


__all__ = ["PRESET_LABELS", "apply_layout_preset", "preset_page"]
