"""Internal preview with reliable CJK fonts and Chinese line breaking."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from typing import Any

from . import internal_preview_native as native_preview
from .reference_profile import prepare_profile_exam
from .page_layout import adjust_inline_spacing
from .page_target import spacing_scale_for_target


def render_internal_preview(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
) -> Any:
    """Render complete previews with CJK-safe fonts and punctuation rules."""

    prepared = deepcopy(prepare_profile_exam(raw_exam))
    adjust_inline_spacing(prepared, spacing_scale_for_target(prepared))
    return native_preview.render_internal_preview(prepared, layout_path, output_dir)


__all__ = ["render_internal_preview"]
