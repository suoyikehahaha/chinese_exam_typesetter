"""Reliable Windows CJK font resolution for frozen preview builds."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Iterable

from PIL import ImageFont


_ALIASES = {"宋体": "SimSun", "黑体": "SimHei", "楷体": "KaiTi", "仿宋": "FangSong"}

_FACE_FILES = {
    "SimSun": ("STSONG.TTF", "simsun.ttc", "SimsunExtG.ttf"),
    "SimHei": ("simhei.ttf", "msyhbd.ttc", "Dengb.ttf"),
    "KaiTi": ("simkai.ttf", "STKAITI.TTF", "STKaitiSC-Regular.ttf"),
    "FangSong": ("simfang.ttf", "STFANGSO.TTF", "STFangsong.ttf"),
}

_BOLD_FILES = {
    "SimSun": ("STZHONGS.TTF", "msyhbd.ttc", "Dengb.ttf"),
    "SimHei": ("simhei.ttf", "msyhbd.ttc", "Dengb.ttf"),
    "KaiTi": ("simkai.ttf", "msyhbd.ttc"),
    "FangSong": ("simfang.ttf", "msyhbd.ttc"),
}

_GLYPH_PROBE = "语文试卷排版中国考试"

_CJK_FALLBACKS = (
    "STSONG.TTF",
    "msyh.ttc",
    "Deng.ttf",
    "simsun.ttc",
    "simhei.ttf",
    "MiSans Normal.ttf",
    "MiSans.ttf",
)


def _font_directories() -> tuple[Path, ...]:
    windows = Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows")
    local_value = os.environ.get("LOCALAPPDATA", "")
    candidates = [windows / "Fonts"]
    if local_value:
        candidates.append(Path(local_value) / "Microsoft" / "Windows" / "Fonts")
    return tuple(candidates)


def _candidate_paths(name: str, bold: bool) -> Iterable[Path]:
    normalized = _ALIASES.get(str(name), str(name))
    filenames: list[str] = []
    if bold:
        filenames.extend(_BOLD_FILES.get(normalized, ()))
    filenames.extend(_FACE_FILES.get(normalized, ()))
    filenames.extend(_CJK_FALLBACKS)
    seen: set[str] = set()
    for directory in _font_directories():
        for filename in filenames:
            candidate = directory / filename
            identity = str(candidate).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            yield candidate


@lru_cache(maxsize=128)
def load_preview_font(
    name: str,
    size_pt: float,
    bold: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a CJK font without trusting virtualized font-folder checks."""

    pixel_size = max(8, int(round(float(size_pt) * 1.33)))
    for candidate in _candidate_paths(name, bool(bold)):
        try:
            font = ImageFont.truetype(str(candidate), pixel_size)
        except (OSError, ValueError):
            continue
        masks = {bytes(font.getmask(character)) for character in _GLYPH_PROBE}
        if len(masks) >= 4:
            return font
    return ImageFont.load_default()


def resolved_font_path(name: str, size_pt: float = 10.5, bold: bool = False) -> str:
    font = load_preview_font(name, size_pt, bold)
    return str(getattr(font, "path", "Pillow default font"))


__all__ = ["load_preview_font", "resolved_font_path"]
