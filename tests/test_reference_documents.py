from __future__ import annotations

from pathlib import Path
from threading import Event
import tempfile
import unittest
from zipfile import ZipFile

from app.answer_typesetting import answer_blocks, exam_blocks
from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.preview_service import PreviewResult, PreviewService


ROOT = Path(__file__).resolve().parents[1]
REFERENCES = (
    (Path(r"D:\Desktop\惠州市2026年高考试题研讨\2026年全国Ⅰ卷语文试题.docx"), 22, "national"),
    (Path(r"D:\Desktop\惠州市2026年高考试题研讨\2025年全国Ⅰ卷\【浩宇文韵】2025年全国Ⅰ卷语文试题.docx"), 23, "national"),
    (Path(r"D:\Downloads\（排版）2026届广州市高三年级阶段训练 语文试题.docx"), 23, "guangzhou"),
    (
        Path(r"D:\Desktop\2027一轮复习\葛\高三语文学法指导讲座素材库\02_指定任务成果\高考试题分析原件\2026全国卷含解析\2026年全国Ⅱ卷语文试题及评分参考（含解析）.docx"),
        23,
        "national",
    ),
)


class ReferenceDocumentTests(unittest.TestCase):
    def _require(self, source: Path) -> None:
        if not source.is_file():
            self.skipTest(f"参考文档不在当前计算机上：{source}")

    def test_all_reference_documents_keep_expected_question_sequence(self) -> None:
        for source, expected_count, _preset in REFERENCES:
            with self.subTest(source=source.name):
                self._require(source)
                raw = import_exam(source)
                numbers = [
                    int(block["question"]["number"])
                    for block in exam_blocks(raw)
                    if block.get("type") == "question"
                ]
                self.assertEqual(numbers, list(range(1, expected_count + 1)))

    def test_scoring_reference_is_separate_from_exam_body(self) -> None:
        source = REFERENCES[3][0]
        self._require(source)
        raw = import_exam(source)
        self.assertEqual(raw.get("document_kind"), "exam_with_answer")
        self.assertEqual(len([block for block in exam_blocks(raw) if block.get("type") == "question"]), 23)
        self.assertGreater(len(answer_blocks(raw)), 20)
        self.assertTrue(any(block.get("type") == "answer_section" for block in answer_blocks(raw)))

    def test_guangzhou_document_with_office_images_exports(self) -> None:
        source = REFERENCES[2][0]
        self._require(source)
        raw = import_exam(source)
        raw.setdefault("metadata", {})["layout_preset"] = "guangzhou"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            target, _pdf, _engine = build_documents(
                raw,
                ROOT / "templates" / "layout.yaml",
                output,
                "guangzhou-reference",
                temporary_dir=output / "work",
            )
            self.assertIsNotNone(target)
            self.assertTrue(target.is_file())
            with ZipFile(target) as archive:
                self.assertIn("word/document.xml", archive.namelist())
                self.assertTrue(any(name.startswith("word/media/") for name in archive.namelist()))

    def test_question_only_references_reach_eight_page_preview(self) -> None:
        for source, _expected_count, preset in REFERENCES[:3]:
            with self.subTest(source=source.name):
                self._require(source)
                raw = import_exam(source)
                raw.setdefault("metadata", {})["layout_preset"] = preset
                service = PreviewService()
                completed = Event()
                received: list[PreviewResult | Exception] = []
                try:
                    service.submit(
                        raw,
                        ROOT / "templates" / "layout.yaml",
                        None,
                        lambda result: (received.append(result), completed.set()),
                    )
                    self.assertTrue(completed.wait(30), "预览在30秒内未完成")
                    self.assertTrue(received)
                    if isinstance(received[0], Exception):
                        raise received[0]
                    self.assertEqual(received[0].target_pages, 8)
                    self.assertEqual(received[0].actual_pages, 8)
                finally:
                    service.close()


if __name__ == "__main__":
    unittest.main()
