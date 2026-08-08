from __future__ import annotations

import unittest

from app.github_updater_v1 import normalize_repository_url


class V083UpdaterTests(unittest.TestCase):
    def test_repository_url_is_normalized(self) -> None:
        self.assertEqual(
            normalize_repository_url("https://github.com/example/exam-tool.git"),
            "https://github.com/example/exam-tool",
        )

    def test_non_github_repository_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_repository_url("https://example.com/exam-tool")


if __name__ == "__main__":
    unittest.main()
