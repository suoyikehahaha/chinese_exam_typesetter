from __future__ import annotations

import unittest

from desktop_app import typeset_name


class ExportNameTests(unittest.TestCase):
    def test_typeset_suffix_is_added_once(self) -> None:
        self.assertEqual(typeset_name("青岛语文试题"), "青岛语文试题（排版）")
        self.assertEqual(
            typeset_name("青岛语文试题（排版）"),
            "青岛语文试题（排版）",
        )


if __name__ == "__main__":
    unittest.main()
