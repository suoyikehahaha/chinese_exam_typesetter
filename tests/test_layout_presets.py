from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from docx import Document
from docx.shared import Mm

from app.config import load_layout
from app.current_importer import import_exam
from app.layout_presets import apply_layout_preset, preset_page


ROOT = Path(__file__).resolve().parents[1]


class LayoutPresetTests(unittest.TestCase):
    def test_national_and_guangzhou_typography(self) -> None:
        layout = load_layout(ROOT / "templates" / "layout.yaml")
        national = apply_layout_preset(layout, {"metadata": {"layout_preset": "national"}})
        guangzhou = apply_layout_preset(layout, {"metadata": {"layout_preset": "guangzhou"}})
        self.assertEqual(national["page"]["margin_left_mm"], 31.75)
        self.assertEqual(national["styles"]["section_title"]["size_pt"], 10.5)
        self.assertEqual(national["styles"]["material_note"]["font"], "SimSun")
        self.assertEqual(guangzhou["page"]["margin_top_mm"], 13.0)
        self.assertEqual(guangzhou["styles"]["section_title"]["size_pt"], 12)
        self.assertEqual(guangzhou["styles"]["material_note"]["font"], "SimHei")

    def test_source_preset_uses_imported_first_section_margins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.docx"
            document = Document()
            section = document.sections[0]
            section.top_margin = Mm(14)
            section.bottom_margin = Mm(15)
            section.left_margin = Mm(24)
            section.right_margin = Mm(23)
            document.add_paragraph("1．下列说法正确的一项是（3分）")
            document.save(source)
            raw = import_exam(source)
        raw["metadata"]["layout_preset"] = "source"
        values = preset_page(raw)
        self.assertAlmostEqual(values["margin_top_mm"], 14.0, places=1)
        self.assertAlmostEqual(values["margin_bottom_mm"], 15.0, places=1)
        self.assertAlmostEqual(values["margin_left_mm"], 24.0, places=1)
        self.assertAlmostEqual(values["margin_right_mm"], 23.0, places=1)


if __name__ == "__main__":
    unittest.main()
