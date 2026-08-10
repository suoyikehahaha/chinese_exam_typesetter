from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import ImageFont

from app import internal_preview_core as preview_core
from app.font_resolver import load_preview_font, resolved_font_path
from app.internal_preview import render_internal_preview


ROOT = Path(__file__).resolve().parents[1]


class FontResolutionTests(unittest.TestCase):
    def test_every_configured_chinese_face_resolves_to_freetype(self) -> None:
        for name in ("SimSun", "SimHei", "KaiTi", "FangSong"):
            for bold in (False, True):
                with self.subTest(name=name, bold=bold):
                    font = load_preview_font(name, 10.5, bold)
                    self.assertIsInstance(font, ImageFont.FreeTypeFont)
                    self.assertNotEqual(resolved_font_path(name, bold=bold), "Pillow default font")
                    masks = {bytes(font.getmask(character)) for character in "语文试卷排版"}
                    self.assertGreaterEqual(len(masks), 4)

    def test_renderer_uses_new_font_resolver_and_restores_global(self) -> None:
        previous = preview_core._font
        raw = {
            "metadata": {
                "exam_name": "2026年普通高等学校招生全国统一考试",
                "subject_name": "语　文",
                "notices": ["答题前，考生务必填写姓名和考号。"],
            },
            "blocks": [
                {"type": "section_title", "text": "一、阅读（72分）"},
                {
                    "type": "material",
                    "title": "材料一",
                    "paragraphs": ["语言文字是文化传承的重要载体。"],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            result = render_internal_preview(
                raw,
                ROOT / "templates" / "layout.yaml",
                Path(folder),
            )
            self.assertTrue(result.pages)
            self.assertTrue(all(path.stat().st_size > 0 for path in result.pages))
            self.assertIs(preview_core._font, previous)


if __name__ == "__main__":
    unittest.main()
