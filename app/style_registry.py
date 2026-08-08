"""Validated style access for the current rendering pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class StyleSpec:
    """Immutable style specification consumed by UI and renderers."""

    name: str
    font: str
    size_pt: float
    bold: bool = False
    alignment: str = "left"
    first_line_indent_chars: float = 0.0
    left_indent_chars: float = 0.0
    hanging_indent_chars: float = 0.0
    line_spacing: float = 1.25
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0


class StyleRegistry:
    """Read-only, validated view of ``layout.yaml`` styles."""

    def __init__(self, layout: Mapping[str, Any]) -> None:
        validate_layout(layout)
        self._styles = {
            name: StyleSpec(
                name=name,
                font=str(spec["font"]),
                size_pt=float(spec["size_pt"]),
                bold=bool(spec.get("bold", False)),
                alignment=str(spec.get("alignment", "left")),
                first_line_indent_chars=float(spec.get("first_line_indent_chars", 0)),
                left_indent_chars=float(spec.get("left_indent_chars", 0)),
                hanging_indent_chars=float(spec.get("hanging_indent_chars", 0)),
                line_spacing=float(spec.get("line_spacing", layout["defaults"].get("body_line_spacing", 1.25))),
                space_before_pt=float(spec.get("space_before_pt", 0)),
                space_after_pt=float(spec.get("space_after_pt", layout["defaults"].get("paragraph_after_pt", 0))),
            )
            for name, spec in layout["styles"].items()
        }

    def get(self, name: str) -> StyleSpec:
        """Return a named style or raise an actionable configuration error."""

        try:
            return self._styles[name]
        except KeyError as exc:
            raise KeyError(f"未定义版式样式：{name}") from exc

    def names(self) -> tuple[str, ...]:
        """Return registered style names in configuration order."""

        return tuple(self._styles)


def validate_layout(layout: Mapping[str, Any]) -> None:
    """Validate the minimum layout contract before rendering starts."""

    page = layout.get("page")
    fonts = layout.get("fonts")
    styles = layout.get("styles")
    defaults = layout.get("defaults")
    if not isinstance(page, Mapping) or not isinstance(fonts, Mapping):
        raise ValueError("版式配置必须包含 page 和 fonts")
    if not isinstance(styles, Mapping) or not styles:
        raise ValueError("版式配置必须包含至少一个 styles 项")
    if not isinstance(defaults, Mapping):
        raise ValueError("版式配置必须包含 defaults")
    required_page = {
        "width_mm", "height_mm", "margin_top_mm", "margin_bottom_mm",
        "margin_left_mm", "margin_right_mm", "header_distance_mm", "footer_distance_mm",
    }
    missing_page = required_page.difference(page)
    if missing_page:
        raise ValueError(f"版式配置缺少页面参数：{', '.join(sorted(missing_page))}")
    for name, spec in styles.items():
        if not isinstance(spec, Mapping) or "font" not in spec or "size_pt" not in spec:
            raise ValueError(f"样式 {name} 缺少 font 或 size_pt")


__all__ = ["StyleRegistry", "StyleSpec", "validate_layout"]
