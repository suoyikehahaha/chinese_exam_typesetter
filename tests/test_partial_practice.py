"""Partial training must retain content and numbering without full-paper rules."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch
from docx import Document

from app.flexible_importers import parse_plain_lines, _normalize_composition, _normalize_question_details
from app.current_importer import import_exam
from app.models import ExamDocument
from app.validators.exam_validator import validate_exam
from app.page_target import get_target_pages, spacing_scale_for_target
from app.score_summary import calculate_score_summary
from app.preview_service import PreviewService
from app.current_pipeline import build_documents
from app.chinese_line_break import wrap_plain_text

LINES = [
    "限时训练一", "一、文言文阅读", "阅读下面的文言文，完成下面小题。",
    "子曰：学而时习之，不亦说乎？", "10．下列对原文理解不正确的一项是（3分）",
    "A．甲", "B．乙", "C．丙", "D．丁",
    "13．把文中画横线的句子翻译成现代汉语。（8分）",
    "（1）学而时习之。", "（2）不亦说乎？",
    "二、古代诗歌阅读", "阅读下面这首唐诗，完成下面小题。",
    "静夜思", "李白", "床前明月光，疑是地上霜。", "举头望明月，低头思故乡。",
    "15．下列对这首诗的赏析，不正确的一项是（3分）",
    "A．甲", "B．乙", "C．丙", "D．丁",
    "16．请简要分析诗人的思想感情。（6分）",
    "三、名篇名句默写", "17．补写出下列句子中的空缺部分。（6分）",
    "（1）学而时习之，________？", "（2）________，低头思故乡。",
]


class PartialPracticeTests(TestCase):
    def test_preserve_non_one_start_and_gaps(self):
        raw = parse_plain_lines(LINES)
        self.assertEqual([b['question']['number'] for b in raw['blocks'] if 'question' in b], [10, 13, 15, 16, 17])
        self.assertEqual(raw['metadata']['document_scope'], 'practice')
        self.assertEqual(get_target_pages(raw), 0)
        self.assertEqual(spacing_scale_for_target(raw, 2, .72), 1)
        self.assertEqual(calculate_score_summary(raw).target, 26)
        self.assertFalse([i for i in validate_exam(ExamDocument.from_dict(raw)) if i.severity == 'error'])

    def test_restart_only_at_section_boundary(self):
        raw = parse_plain_lines(['专项训练', '一、文言文', '1．请翻译下面句子。（3分）', '二、默写', '1．补写下列句子。（6分）'])
        self.assertEqual([b['question']['number'] for b in raw['blocks'] if 'question' in b], [1, 1])
        self.assertFalse([i for i in validate_exam(ExamDocument.from_dict(raw)) if i.severity == 'error'])

    def test_question_number_does_not_assign_semantics(self):
        raw = {'blocks': [{'type': 'question', 'question': {'number': 23, 'stem': '翻译', 'embedded_segments': [[{'text': '要求：忠实原文'}]]}}, {'type': 'question', 'question': {'number': 2, 'stem': '请给下面文字断句。', 'embedded_segments': [[{'text': '甲A乙B丙C丁D'}]]}}]}
        _normalize_composition(raw)
        _normalize_question_details(raw)
        self.assertNotIn('composition_material', raw['blocks'][0]['question'])
        self.assertEqual(raw['blocks'][1]['question']['segmentation_text'], '甲A乙B丙C丁D')

    def test_first_line_width_does_not_reduce_following_lines(self):
        class Draw:
            def textlength(self, text, font): return len(text)
        self.assertEqual(wrap_plain_text(Draw(), '甲乙丙丁戊己庚辛壬癸', object(), 5, first_width=3), ['甲乙丙', '丁戊己庚辛', '壬癸'])

    def test_docx_import_preview_export(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / '限时训练一.docx'
            document = Document()
            for line in LINES: document.add_paragraph(line)
            document.save(source)
            original = source.read_bytes()
            raw = import_exam(source)
            self.assertEqual(raw['metadata']['document_scope'], 'practice')
            self.assertEqual(raw['metadata']['exam_name'], '限时训练一')
            done, received = Event(), []
            service = PreviewService()
            try:
                service.submit(raw, 'templates/layout.yaml', None, lambda r: (received.append(r), done.set()))
                self.assertTrue(done.wait(15))
                if isinstance(received[0], Exception): raise received[0]
                self.assertLess(received[0].actual_pages, 8)
                self.assertEqual(received[0].spacing_scale, 1)
            finally:
                service.close()
            raw['metadata']['page_spacing_scale'] = 1
            with patch('app.current_pipeline._libreoffice_page_count', side_effect=AssertionError('practice must not calibrate')):
                output, _, _ = build_documents(raw, 'templates/layout.yaml', folder, 'partial')
            text = '\n'.join(p.text for p in Document(output).paragraphs)
            for value in ('限时训练一', '学而时习之', '李白', '低头思故乡', '17'):
                self.assertIn(value, text)
            self.assertEqual(source.read_bytes(), original)
