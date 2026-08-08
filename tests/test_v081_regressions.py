from __future__ import annotations

from pathlib import Path
import unittest

from app.flexible_importers_v7 import import_exam


class V081RegressionTests(unittest.TestCase):
    def test_three_acceptance_exams_keep_formal_titles_and_23_questions(self) -> None:
        cases = [
            (
                Path(r"D:\Desktop\2026届_菏泽二模_语文试题.docx"),
                "2026年菏泽市高三二模考试",
            ),
            (
                Path(r"D:\Desktop\2026届_临沂二模_语文试题.docx"),
                "2026年普通高等学校招生全国统一考试（模拟）",
            ),
            (
                Path(
                    r"D:\Desktop\青岛市2026年高三年级第三次适应性检测语文试题.docx"
                ),
                "青岛市2026年高三年级第三次适应性检测",
            ),
        ]
        for source, title in cases:
            if not source.exists():
                continue
            with self.subTest(source=source.name):
                exam = import_exam(source)
                self.assertEqual(exam["metadata"]["exam_name"], title)
                numbers = [
                    block["question"]["number"]
                    for block in exam["blocks"]
                    if block.get("type") == "question"
                ]
                self.assertEqual(numbers, list(range(1, 24)))


if __name__ == "__main__":
    unittest.main()
