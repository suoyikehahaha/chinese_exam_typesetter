"""Resolve the same saved paragraph/range overrides used by DOCX export."""

from typing import Any


def preview_format(owner: dict, target: str = "block", index: int = 0,
                   *, base: dict | None = None, prefix: int = 0) -> dict[str, Any]:
    result = dict(base or {})
    for entry in owner.get("paragraph_formats", []):
        if entry.get("target", target) == target and int(entry.get("target_index", 0)) == index:
            result.update(entry)
    ranges = list(result.get("_inline_formats", []))
    for entry in owner.get("inline_formats", []):
        if entry.get("target", target) == target and int(entry.get("target_index", entry.get("line", 0))) == index:
            offset = prefix + int(entry.get("text_offset", 0))
            ranges.append({**entry, "start": int(entry.get("start", 0)) + offset,
                           "end": int(entry.get("end", 0)) + offset})
    result["_inline_formats"] = ranges
    return result
