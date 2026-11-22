"""Small, UI-independent rules for document state and safe export names."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .export_naming import with_export_suffix


def draft_signature(raw: dict) -> str:
    """Ignore obsolete page-target fields and embedded bytes when checking for edits."""
    value = {**raw, "metadata": {k: v for k, v in raw.get("metadata", {}).items()
                                 if k not in {"target_pages", "page_spacing_scale", "source_blobs"}}}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def suggested_export_path(source: Path | None, folder: Path, name: str) -> Path:
    """Offer a fresh filename and never suggest replacing the imported original."""
    stem = with_export_suffix(name)
    target = folder / f"{stem}.docx"
    index = 2
    while target.exists() or (source is not None and target.resolve() == source.resolve()):
        target = folder / f"{stem}-{index}.docx"
        index += 1
    return target


def validate_format(spec: dict) -> dict:
    """Reject invalid typed values before they can break preview or DOCX export."""
    import math

    limits = {
        "size_pt": (5, 72), "line_spacing": (0.5, 4),
        "left_indent_chars": (0, 20), "right_indent_chars": (0, 20),
        "special_indent_chars": (0, 20),
        "space_before_pt": (0, 200), "space_after_pt": (0, 200),
    }
    labels = {"size_pt": "字号", "line_spacing": "行距", "left_indent_chars": "左缩进",
              "right_indent_chars": "右缩进", "special_indent_chars": "特殊缩进",
              "space_before_pt": "段前距", "space_after_pt": "段后距"}
    for key, (minimum, maximum) in limits.items():
        value = float(spec[key])
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{labels[key]}应在 {minimum} 至 {maximum} 之间")
    return spec
