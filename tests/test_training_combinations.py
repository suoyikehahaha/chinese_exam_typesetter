"""Real training fixture plus reordered, repeated and mixed section coverage."""
from copy import deepcopy
from hashlib import sha256
from itertools import permutations
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from docx import Document
from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.flexible_importers import materialize_automatic_numbering, parse_plain_lines
from app.internal_preview import render_internal_preview
from app.models import ExamDocument
from app.validators import validate_exam
from app.score_summary import calculate_score_summary

ROOT = Path(__file__).resolve().parents[1]


class TrainingCombinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = list((ROOT / '.codex-remote-attachments').glob('*/2bbc0750-4fcd-438e-b934-7053ac0349e1/*.docx'))
        if not candidates:
            raise unittest.SkipTest('真实训练附件不在当前计算机，通用局部训练测试仍运行')
        cls.source = candidates[0]

    def test_real_training_import_export_and_preview(self):
        original = sha256(self.source.read_bytes()).hexdigest()
        raw = import_exam(self.source)
        questions = [b['question'] for b in raw['blocks'] if b['type'] == 'question']
        self.assertEqual([q['number'] for q in questions], list(range(1, 18)))
        self.assertTrue(all(q['score'] is not None for q in questions))
        self.assertEqual(calculate_score_summary(raw).total, 68)
        self.assertEqual(len([b for b in raw['blocks'] if b['type'] == 'section_title']), 5)
        self.assertEqual(raw['metadata']['target_pages'], 0)
        self.assertEqual(len(raw['metadata']['header_lines']), 2)
        self.assertTrue(questions[0]['segmentation_text'])
        self.assertTrue(questions[4]['segmentation_text'])
        report = next(b for b in raw['blocks'] if str(b.get('title', '')).startswith('关于'))
        centered_indices = {entry['target_index'] for entry in report.get('paragraph_formats', []) if entry.get('alignment') == '居中'}
        self.assertNotIn(2, centered_indices, '报告正文不能因标题偏移被误设为居中')
        with TemporaryDirectory() as folder:
            result = render_internal_preview(raw, ROOT / 'templates/layout.yaml', Path(folder)/'preview')
            self.assertGreater(len(result.pages), 0)
            target, _, _ = build_documents(raw, ROOT/'templates/layout.yaml', folder, 'training')
            doc = Document(target)
            self.assertEqual(len(doc.tables), 1)
            self.assertEqual(len(doc.element.xpath('.//w:drawing')), 1)
            subquestions = [p for p in doc.paragraphs if re.match(r'^（[1-5]）', p.text) and any(cue in p.text for cue in ('赤壁赋', '与下图', '家访', '小明', '有些同学'))]
            self.assertEqual(len(subquestions), 5)
            self.assertTrue(all(any(run.underline and run.text for run in p.runs) for p in subquestions))
            text = '\n'.join(p.text for p in doc.paragraphs)
            for value in ('限时65分钟', '命题人：谢思敏', '三、研究结论', '请依据报告内容', '关于', '的研究报告'):
                self.assertIn(value, text)
            self.assertIn('15．', text)
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(), original)

    def test_all_325_ordered_section_selections(self):
        document = Document(self.source)
        materialize_automatic_numbering(document)
        sections = []
        for p in document.paragraphs:
            text = p.text.strip()
            if re.match(r'^[一二三四五]、(?:文言文阅读|古代诗歌阅读|语言文字运用|理解性默写)', text):
                sections.append([])
            if sections and text:
                sections[-1].append(text)
        self.assertEqual(len(sections), 5)
        count = 0
        for size in range(1, 6):
            for order in permutations(range(5), size):
                with self.subTest(order=order):
                    lines = ['语文限时训练'] + [line for i in order for line in sections[i]]
                    raw = parse_plain_lines(lines)
                    expected = [int(m.group(1)) for line in lines if (m := re.match(r'^(\d+)[.．]', line))]
                    actual = [b['question']['number'] for b in raw['blocks'] if b['type'] == 'question']
                    self.assertEqual(actual, expected)
                    self.assertEqual(raw['metadata']['document_scope'], 'practice')
                    self.assertEqual(raw['metadata']['target_pages'], 0)
                    self.assertFalse([issue for issue in validate_exam(ExamDocument.from_dict(raw)) if issue.severity == 'error'])
                    count += 1
        self.assertEqual(count, 325)

    def test_repeated_sections_can_restart_numbering(self):
        lines = ['语文限时训练']
        for label in ('文言文阅读Ⅰ', '文言文阅读Ⅱ', '古代诗歌阅读', '现代文阅读', '语言文字运用', '写作'):
            lines.extend([f'一、{label}', '1．请分析材料内容。（3分）', '2．请概括表达特点。（6分）'])
        raw = parse_plain_lines(lines)
        self.assertEqual(len([b for b in raw['blocks'] if b['type'] == 'question']), 12)
        self.assertFalse([issue for issue in validate_exam(ExamDocument.from_dict(raw)) if issue.severity == 'error'])

    def test_recombined_docx_roundtrips(self):
        # Preserve source package relationships when copying paragraphs/tables.
        for order in ((2,), (4,), (3,), (4, 2, 3), (1, 0), (0, 0, 4)):
            with self.subTest(order=order), TemporaryDirectory() as folder:
                doc = Document(self.source)
                materialize_automatic_numbering(doc)
                groups, header = [], []
                for node in list(doc.element.body):
                    if node.tag.endswith('}sectPr'):
                        continue
                    text = ''.join(node.itertext()) if not node.tag.endswith('}p') else ''.join(node.xpath('.//w:t/text()'))
                    if re.match(r'^[一二三四五]、(?:文言文阅读|古代诗歌阅读|语言文字运用|理解性默写)', text):
                        groups.append([])
                    (groups[-1] if groups else header).append(deepcopy(node))
                for node in list(doc.element.body):
                    if not node.tag.endswith('}sectPr'):
                        doc.element.body.remove(node)
                for node in header + [node for i in order for node in groups[i]]:
                    doc.element.body.insert(len(doc.element.body) - 1, deepcopy(node))
                source = Path(folder)/'组合限时训练.docx'
                doc.save(source)
                raw = import_exam(source)
                expected_counts = [4, 5, 2, 5, 1]
                self.assertEqual(sum(b['type']=='question' for b in raw['blocks']), sum(expected_counts[i] for i in order))
                self.assertEqual(len([b for b in raw['blocks'] if b['type']=='section_title']), len(order))
                output, _, _ = build_documents(raw, ROOT/'templates/layout.yaml', folder, 'output')
                rebuilt = Document(output)
                self.assertEqual(len(rebuilt.tables), order.count(3))
                self.assertEqual(len(rebuilt.element.xpath('.//w:drawing')), order.count(4))
