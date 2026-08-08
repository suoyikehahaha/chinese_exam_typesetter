from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from app.chinese_line_break_v042 import (
    NO_LINE_END,
    NO_LINE_START,
    wrap_plain_text,
    wrap_rich_text,
)
from app.internal_preview_v02 import _font


def _drawing() -> tuple[ImageDraw.ImageDraw, object]:
    image = Image.new("RGB", (800, 200), "white")
    return ImageDraw.Draw(image), _font("SimSun", 10.5)


class ChineseLineBreakV042Tests(unittest.TestCase):
    def test_closing_punctuation_hangs_beyond_right_boundary(self) -> None:
        draw, font = _drawing()
        text = "天地玄黄，宇宙洪荒。"
        maximum = int(draw.textlength("天地玄黄", font=font))

        lines = wrap_plain_text(draw, text, font, maximum)

        self.assertEqual(lines[0], "天地玄黄，")
        self.assertGreater(draw.textlength(lines[0], font=font), maximum)
        self.assertEqual("".join(lines), text)

    def test_opening_punctuation_moves_to_the_next_line(self) -> None:
        draw, font = _drawing()
        text = "甲乙丙“丁戊己庚"
        maximum = int(draw.textlength("甲乙丙“", font=font))

        lines = wrap_plain_text(draw, text, font, maximum)

        self.assertFalse(lines[0].endswith("“"))
        self.assertTrue(lines[1].startswith("“"))
        self.assertEqual("".join(lines), text)

    def test_rich_text_keeps_source_offsets_and_hanging_punctuation(self) -> None:
        draw, font = _drawing()
        bold_font = _font("SimSun", 10.5, True)
        text = "天地玄黄，宇宙洪荒。"
        maximum = int(draw.textlength("天地玄黄", font=font))
        ranges = [{"start": 1, "end": 5, "bold": True, "underline": True}]

        lines = wrap_rich_text(draw, text, font, bold_font, maximum, ranges)

        self.assertTrue(lines[0][0].endswith("，"))
        self.assertEqual("".join(line for line, _start, _end in lines), text)
        self.assertEqual(lines[0][1], 0)
        self.assertTrue(
            all(previous[2] == following[1] for previous, following in zip(lines, lines[1:]))
        )

    def test_wrapped_lines_obey_chinese_line_start_and_end_rules(self) -> None:
        draw, font = _drawing()
        text = "他说：“今天（天气很好），我们一起去学校。”然后继续上课。"
        maximum = int(draw.textlength("他说：“今天", font=font))

        lines = wrap_plain_text(draw, text, font, maximum)

        self.assertEqual("".join(lines), text)
        self.assertTrue(all(not line or line[0] not in NO_LINE_START for line in lines[1:]))
        self.assertTrue(all(not line or line[-1] not in NO_LINE_END for line in lines[:-1]))


if __name__ == "__main__":
    unittest.main()
