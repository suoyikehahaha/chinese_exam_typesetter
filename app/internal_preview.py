"""Internal preview with reliable CJK fonts and Chinese line breaking."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import internal_preview_native as native_preview
from .resource_anchors import project_source_resources
from .reference_profile import prepare_profile_exam


def render_internal_preview(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
) -> Any:
    """Render complete previews with CJK-safe fonts and punctuation rules."""

    prepared = prepare_profile_exam(raw_exam)
    project_source_resources(prepared)
    return native_preview.render_internal_preview(prepared, layout_path, output_dir)


__all__ = ["render_internal_preview"]
