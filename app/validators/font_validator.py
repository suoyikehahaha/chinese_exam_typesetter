"""Windows 中文字体预检。"""

from __future__ import annotations

from pathlib import Path
import os


FONT_FILES = {
    "SimSun": ("simsun.ttc", "simsun.ttf"),
    "SimHei": ("simhei.ttf",),
    "KaiTi": ("simkai.ttf", "kaiti.ttf"),
    "FangSong": ("simfang.ttf", "fangsong.ttf"),
}


def check_required_fonts(font_names: list[str]) -> dict[str, bool]:
    """检查 Windows Fonts 目录中的字体文件。"""

    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    fonts_dir = windows_dir / "Fonts"
    existing = {item.name.lower() for item in fonts_dir.glob("*")} if fonts_dir.exists() else set()
    return {
        name: any(filename.lower() in existing for filename in FONT_FILES.get(name, ()))
        for name in font_names
    }
