"""Stable naming rules for files exported from the desktop workbench."""

from __future__ import annotations

from pathlib import Path


EXPORT_SUFFIX = "（排版）"


def safe_filename(value: str) -> str:
    """移除 Windows 文件名禁用字符并清理首尾空白与点号。"""

    forbidden = '<>:"/\\|?*'
    return "".join("_" if char in forbidden else char for char in value).strip(" .")


def typeset_name(value: str) -> str:
    """生成带单一中文排版后缀的安全文件名。"""

    name = safe_filename(value.strip())
    return name if name.endswith(EXPORT_SUFFIX) else f"{name}{EXPORT_SUFFIX}"


def imported_stem(source_path: Path | str | None) -> str:
    """Return the visible filename stem of an imported document."""

    if source_path is None:
        return ""
    return Path(source_path).stem.strip()


def default_export_basename(
    source_path: Path | str | None,
    fallback: str = "语文试卷",
) -> str:
    """Use the imported filename as the export dialog's initial basename."""

    return imported_stem(source_path) or fallback.strip() or "语文试卷"


def with_export_suffix(basename: str) -> str:
    """Append the Chinese export suffix exactly once."""

    clean = basename.strip() or "语文试卷"
    if clean.endswith(EXPORT_SUFFIX):
        return clean
    return f"{clean}{EXPORT_SUFFIX}"


__all__ = [
    "EXPORT_SUFFIX",
    "default_export_basename",
    "imported_stem",
    "safe_filename",
    "typeset_name",
    "with_export_suffix",
]
