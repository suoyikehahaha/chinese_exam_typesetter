from pathlib import Path
import tempfile
import unittest

from docx import Document

from app.config import load_layout
from app.editor_importers import parse_plain_lines
from app.models import ExamDocument
from app.question_overrides import apply_question_overrides
from app.renderers import DocxRenderer


ROOT = Path(__file__).resolve().parents[1]


class EditorFeatureTests(unittest.TestCase):
    def test_plain_text_import_detects_questions_and_options(self) -> None:
        raw = parse_plain_lines(
            [
                "一、阅读",
                "阅读下面的文字，完成1～2题。",
                "示例材料。",
                "1．下列说法正确的一项是（3分）",
                "A．选项甲",
                "B．选项乙",
                "C．选项丙",
                "D．选项丁",
                "2．请概括材料内容。（5分）",
            ],
            "导入测试",
        )
        questions = [
            block["question"]
            for block in raw["blocks"]
            if block.get("type") == "question"
        ]
        self.assertEqual(2, len(questions))
        self.assertEqual("objective", questions[0]["kind"])
        self.assertEqual(4, len(questions[0]["options"]))
        self.assertEqual("subjective", questions[1]["kind"])

    def test_question_format_override_changes_indentation(self) -> None:
        raw = {
            "metadata": {
                "exam_name": "测试卷",
                "subject_name": "语文",
                "meta_text": "共1题。",
                "total_score": 5,
                "notices": [],
            },
            "blocks": [
                {
                    "type": "question",
                    "question": {
                        "number": 1,
                        "kind": "subjective",
                        "stem": "测试题干",
                        "score": 5,
                        "format": {
                            "font": "仿宋",
                            "size_pt": 12,
                            "first_line_indent_chars": 2,
                            "alignment": "左对齐",
                            "line_spacing": 1.5,
                        },
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "override.docx"
            DocxRenderer(load_layout(ROOT / "templates" / "layout.yaml")).render(
                ExamDocument.from_dict(raw), path
            )
            apply_question_overrides(path, raw)
            document = Document(path)
            paragraph = next(item for item in document.paragraphs if item.text.startswith("1．"))
            self.assertAlmostEqual(24, paragraph.paragraph_format.first_line_indent.pt)
            self.assertEqual("FangSong", paragraph.runs[0].font.name)


if __name__ == "__main__":
    unittest.main()
