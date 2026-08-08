import unittest
from unittest.mock import patch

from app.validators.font_validator import check_required_fonts


class FontValidatorTests(unittest.TestCase):
    @patch("app.validators.font_validator.Path.exists", return_value=False)
    def test_missing_font_directory_reports_false(self, _mock_exists) -> None:
        result = check_required_fonts(["SimSun", "SimHei"])
        self.assertEqual({"SimSun": False, "SimHei": False}, result)


if __name__ == "__main__":
    unittest.main()
