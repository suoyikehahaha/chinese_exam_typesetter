from pathlib import Path
import unittest

from app.export_naming_v01 import (
    default_export_basename,
    imported_stem,
    with_export_suffix,
)


class ExportNamingTests(unittest.TestCase):
    def test_imported_stem_ignores_extension(self) -> None:
        self.assertEqual(
            imported_stem(Path("2026届_青岛二模_语文试题.docx")),
            "2026届_青岛二模_语文试题",
        )

    def test_default_uses_imported_filename(self) -> None:
        self.assertEqual(
            default_export_basename("D:/题库/武汉四调.docx"),
            "武汉四调",
        )

    def test_default_falls_back_without_source(self) -> None:
        self.assertEqual(default_export_basename(None), "语文试卷")

    def test_suffix_is_added_once(self) -> None:
        self.assertEqual(with_export_suffix("武汉四调"), "武汉四调（排版）")
        self.assertEqual(with_export_suffix("武汉四调（排版）"), "武汉四调（排版）")
