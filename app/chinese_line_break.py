"""Chinese line-breaking rules with hanging closing punctuation."""

from __future__ import annotations

from typing import Any
from functools import lru_cache


NO_LINE_START = frozenset(
    "，。！？；：、）》】〕〉”’…％‰℃,.!?;:%)]}"
    "\u2014"
)
NO_LINE_END = frozenset("（《【〔〈“‘([{\u2014")


@lru_cache(maxsize=32768)
def _font_character_width(character: str, font: Any) -> float:
    return float(font.getlength(character))


def _character_width(draw: Any, character: str, font: Any) -> float:
    if hasattr(font, "getlength"):
        return _font_character_width(character, font)
    return float(draw.textlength(character, font=font))


def wrap_plain_text(
    draw: Any,
    text: str,
    font: Any,
    width: int,
    first_width: int | None = None,
) -> list[str]:
    """Wrap text using Chinese kinsoku and permit closing punctuation overflow."""

    result: list[str] = []
    maximum = max(1.0, float(width if first_width is None else first_width))
    for raw_line in str(text).splitlines() or [""]:
        if not raw_line:
            result.append("")
            continue
        current: list[str] = []
        current_width = 0.0
        for character in raw_line:
            char_width = _character_width(draw, character, font)
            overflow = bool(current) and current_width + char_width > maximum
            if overflow and character in NO_LINE_START:
                current.append(character)
                current_width += char_width
                continue
            if overflow:
                moved: list[str] = []
                while current and current[-1] in NO_LINE_END:
                    moved.insert(0, current.pop())
                if current:
                    result.append("".join(current))
                    maximum = max(1.0, float(width))
                current = moved
                current_width = sum(_character_width(draw, item, font) for item in current)
            current.append(character)
            current_width += char_width
        result.append("".join(current))
    return result


def wrap_rich_text(
    draw: Any,
    text: str,
    font: Any,
    bold_font: Any,
    width: int,
    ranges: list[dict[str, Any]],
    first_width: int | None = None,
    font_at: Any = None,
) -> list[tuple[str, int, int]]:
    """Wrap decorated text while retaining source positions and kinsoku rules."""

    result: list[tuple[str, int, int]] = []
    maximum = max(1.0, float(width if first_width is None else first_width))
    offset = 0

    def active(position: int, key: str) -> bool:
        return any(
            int(mark.get("start", 0)) <= position < int(mark.get("end", 0))
            and bool(mark.get(key))
            for mark in ranges
        )

    for raw_line in str(text).splitlines() or [""]:
        if not raw_line:
            result.append(("", offset, offset))
            offset += 1
            continue
        current: list[tuple[str, int, float]] = []
        current_width = 0.0
        for relative, character in enumerate(raw_line):
            absolute = offset + relative
            chosen = font_at(absolute) if font_at else (bold_font if active(absolute, "bold") else font)
            char_width = _character_width(draw, character, chosen)
            overflow = bool(current) and current_width + char_width > maximum
            if overflow and character in NO_LINE_START:
                current.append((character, absolute, char_width))
                current_width += char_width
                continue
            if overflow:
                moved: list[tuple[str, int, float]] = []
                while current and current[-1][0] in NO_LINE_END:
                    moved.insert(0, current.pop())
                if current:
                    result.append(
                        (
                            "".join(item[0] for item in current),
                            current[0][1],
                            current[-1][1] + 1,
                        )
                    )
                current = moved
                maximum = max(1.0, float(width))
                current_width = sum(item[2] for item in current)
            current.append((character, absolute, char_width))
            current_width += char_width
        if current:
            result.append(
                (
                    "".join(item[0] for item in current),
                    current[0][1],
                    current[-1][1] + 1,
                )
            )
        offset += len(raw_line) + 1
    return result


__all__ = ["NO_LINE_END", "NO_LINE_START", "wrap_plain_text", "wrap_rich_text"]
