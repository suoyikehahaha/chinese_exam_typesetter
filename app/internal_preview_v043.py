"""v0.4.3 preview with reliable CJK fonts and Chinese line breaking."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from . import internal_preview_v02 as v02
from . import internal_preview_v041 as v041
from .chinese_line_break_v042 import wrap_plain_text, wrap_rich_text
from .font_resolver_v043 import load_preview_font


_PATCH_LOCK = Lock()


def render_internal_preview(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
) -> Any:
    """Render complete previews with CJK-safe fonts and punctuation rules."""

    with _PATCH_LOCK:
        previous_font = v02._font
        previous_plain = v02._wrap_text
        previous_rich = v02._wrap_rich_text
        v02._font = load_preview_font
        v02._wrap_text = wrap_plain_text
        v02._wrap_rich_text = wrap_rich_text
        try:
            return v041.render_internal_preview(raw_exam, layout_path, output_dir)
        finally:
            v02._font = previous_font
            v02._wrap_text = previous_plain
            v02._wrap_rich_text = previous_rich


__all__ = ["render_internal_preview"]

