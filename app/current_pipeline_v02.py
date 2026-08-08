"""Current runtime build pipeline with page-target spacing and DOCX-only output."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import load_layout
from .current_pipeline_v01 import build_documents as build_documents_base
from .page_target_v01 import adjusted_layout, spacing_scale_for_target


def build_documents(
    raw_exam: dict[str, Any],
    layout_path: str | Path,
    output_dir: str | Path,
    basename: str,
    *,
    template_path: str | Path | None = None,
    export_docx: bool = True,
    export_pdf: bool = False,
    temporary_dir: str | Path | None = None,
) -> tuple[Path | None, None, str]:
    """Build only DOCX; ``export_pdf`` is accepted for old callers and ignored."""

    adjusted = deepcopy(raw_exam)
    _adjust_inline_spacing(adjusted, spacing_scale_for_target(adjusted))
    output = Path(output_dir)
    work = Path(temporary_dir) if temporary_dir else output
    work.mkdir(parents=True, exist_ok=True)
    target_layout = work / "layout-targeted.yaml"
    target_layout.write_text(
        _dump_yaml(adjusted_layout(load_layout(layout_path), adjusted)),
        encoding="utf-8",
    )
    try:
        docx_path, _ignored_pdf, _ignored_engine = build_documents_base(
            adjusted,
            target_layout,
            output,
            basename,
            template_path=template_path,
            export_docx=export_docx,
            export_pdf=False,
            temporary_dir=work,
        )
    finally:
        target_layout.unlink(missing_ok=True)
    return docx_path, None, "docx-only"


def _adjust_inline_spacing(raw_exam: dict[str, Any], scale: float) -> None:
    """Apply a bounded rhythm change to explicit question overrides."""

    for block in raw_exam.get("blocks", []):
        specs: list[dict[str, Any]] = []
        if block.get("type") == "question":
            question = block.get("question", {})
            if isinstance(question.get("format"), dict):
                specs.append(question["format"])
            specs.extend(item for item in question.get("paragraph_formats", []) if isinstance(item, dict))
        specs.extend(item for item in block.get("paragraph_formats", []) if isinstance(item, dict))
        if isinstance(block.get("format"), dict):
            specs.append(block["format"])
        for spec in specs:
            if "line_spacing" in spec:
                spec["line_spacing"] = max(0.95, min(1.8, float(spec["line_spacing"]) * scale))
            for key in ("space_before_pt", "space_after_pt"):
                if key in spec:
                    spec[key] = max(0.0, float(spec[key]) * scale)


def _dump_yaml(data: dict[str, Any]) -> str:
    """Serialize the small nested mapping accepted by the bundled YAML reader."""

    lines: list[str] = []

    def visit(mapping: dict[str, Any], indent: int) -> None:
        for key, value in mapping.items():
            prefix = " " * indent + f"{key}:"
            if isinstance(value, dict):
                lines.append(prefix)
                visit(value, indent + 2)
            else:
                lines.append(f"{prefix} {_yaml_scalar(value)}")

    visit(data, 0)
    return "\n".join(lines) + "\n"


def _yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    text = str(value)
    if any(character in text for character in (":", "#", "\n")):
        return '"' + text.replace('"', '\\"') + '"'
    return text


__all__ = ["build_documents"]
