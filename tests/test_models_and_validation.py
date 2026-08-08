from pathlib import Path
import unittest

from app.models import load_exam
from app.validators import validate_exam


ROOT = Path(__file__).resolve().parents[1]


class ModelValidationTests(unittest.TestCase):
    def test_sample_has_23_continuous_questions_and_150_points(self) -> None:
        exam = load_exam(ROOT / "samples" / "exam.json")
        issues = validate_exam(exam)
        self.assertEqual([], [issue for issue in issues if issue.severity == "error"])
        self.assertEqual([], [issue for issue in issues if issue.code == "score-total"])

    def test_dynamic_language_question_types_are_data_driven(self) -> None:
        exam = load_exam(ROOT / "samples" / "exam.json")
        questions = {
            block.question.number: block.question
            for block in exam.blocks
            if block.question is not None
        }
        self.assertEqual("subjective", questions[18].kind)
        self.assertEqual("objective", questions[19].kind)
        self.assertEqual("subjective", questions[20].kind)
        self.assertEqual("objective", questions[21].kind)
        self.assertEqual("subjective", questions[22].kind)


if __name__ == "__main__":
    unittest.main()
