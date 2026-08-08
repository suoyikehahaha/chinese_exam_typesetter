from pathlib import Path
import tempfile
import unittest

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app.editor_importers import import_exam, parse_plain_lines


class FlexibleImporterTests(unittest.TestCase):
    def test_optional_notices_are_excluded_from_questions(self) -> None:
        raw = parse_plain_lines(
            [
                "某校语文考试",
                "注意事项：",
                "1．请填写姓名。",
                "2．答案写在答题卡上。",
                "一、阅读（5分）",
                "1．请概括材料内容。（5分）",
            ],
            "测试",
        )
        numbers = [
            block["question"]["number"]
            for block in raw["blocks"]
            if block.get("type") == "question"
        ]
        self.assertEqual([1], numbers)
        self.assertEqual(2, len(raw["metadata"]["notices"]))

    def test_exam_without_notices_starts_from_first_question(self) -> None:
        raw = parse_plain_lines(
            [
                "限时训练",
                "1．下列说法正确的一项是（3分）",
                "A．甲",
                "B．乙",
                "C．丙",
                "D．丁",
                "2．请说明理由。（4分）",
            ],
            "测试",
        )
        questions = [
            block["question"]
            for block in raw["blocks"]
            if block.get("type") == "question"
        ]
        self.assertEqual([1, 2], [item["number"] for item in questions])
        self.assertEqual([], raw["metadata"]["notices"])

    def test_inline_options_are_split(self) -> None:
        raw = parse_plain_lines(
            [
                "1．下列成语最恰当的一项是（3分）",
                "A．甲    B．乙    C．丙    D．丁",
            ],
            "测试",
        )
        question = next(
            block["question"]
            for block in raw["blocks"]
            if block.get("type") == "question"
        )
        self.assertEqual("objective", question["kind"])
        self.assertEqual(4, len(question["options"]))

    def test_text_pdf_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exam.pdf"
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            pdf = canvas.Canvas(str(path))
            pdf.setFont("STSong-Light", 12)
            y = 800
            for line in [
                "语文限时训练",
                "1．下列说法正确的一项是（3分）",
                "A．甲",
                "B．乙",
                "C．丙",
                "D．丁",
            ]:
                pdf.drawString(72, y, line)
                y -= 24
            pdf.save()
            raw = import_exam(path)
            question = next(
                block["question"]
                for block in raw["blocks"]
                if block.get("type") == "question"
            )
            self.assertEqual(1, question["number"])
            self.assertEqual(4, len(question["options"]))


if __name__ == "__main__":
    unittest.main()
