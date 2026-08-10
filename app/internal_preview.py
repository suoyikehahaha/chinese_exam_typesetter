"""Internal preview with reliable CJK fonts and Chinese line breaking."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from . import internal_preview_core as preview_core
from . import internal_preview_native as native_preview
from .chinese_line_break import wrap_plain_text, wrap_rich_text
from .font_resolver import load_preview_font


_PATCH_LOCK = Lock()


def render_internal_preview(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
) -> Any:
    """Render complete previews with CJK-safe fonts and punctuation rules."""

    with _PATCH_LOCK:
        previous_font = preview_core._font
        previous_plain = preview_core._wrap_text
        previous_rich = preview_core._wrap_rich_text
        preview_core._font = load_preview_font
        preview_core._wrap_text = wrap_plain_text
        preview_core._wrap_rich_text = wrap_rich_text
        try:
            return native_preview.render_internal_preview(raw_exam, layout_path, output_dir)
        finally:
            preview_core._font = previous_font
            preview_core._wrap_text = previous_plain
            preview_core._wrap_rich_text = previous_rich


__all__ = ["render_internal_preview"]
