"""September source regressions; local originals remain read-only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from hashlib import sha256
import re
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from docx import Document
from app.current_importer import import_exam
from app.current_pipeline import build_documents
from app.models import ExamDocument
from app.validators import validate_exam
from app.score_summary import calculate_score_summary
from app.flexible_importers import parse_plain_lines, _normalize_question_details
from app.font_resolver import resolved_font_path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = Path('D:/Desktop')
SOURCES = [
    '（0916试题）武汉市2027届高中毕业生九月调研考试语文试题.docx',
    '（0916答案）武汉市2027届高中毕业生九月调研考试语文答案.docx',
    '2027届第一次六校联考语文试题（09-14）.docx',
    '2027届第一次六校联考语文答案（09-14）.docx',
    '2027一轮复习/限时训练/限时训练一/惠州一中2027届高三上学期语文限时训练（一）.docx',
    '2027一轮复习/限时训练/限时训练一/惠州一中2027届高三上学期语文限时训练（一）参考答案.docx',
]

def compact(text):
    return re.sub(r'\s+', '', text)

class StructureRegressionTests(unittest.TestCase):
    def test_late_and_single_heading_scores(self):
        raw = parse_plain_lines(['语文限时训练','一、语言文字运用','1．请阅读示例并作答。','请写出一组排比句。（4分）','二、名篇名句默写（本题共1小题，6分）','2．补写出下列句子中的空缺部分。','（1）填写空缺。'])
        self.assertEqual([b['question']['score'] for b in raw['blocks'] if 'question' in b], [4,6])

    def test_continuation_stays_with_subquestion(self):
        raw = parse_plain_lines(['语文限时训练','一、名篇名句默写','1．补写出下列句子。（6分）','（1）引用《春江花','月夜》中的句子。','（2）另一句。'])
        _normalize_question_details(raw)
        q=raw['blocks'][-1]['question']
        self.assertEqual(q['subquestions'][0], '引用《春江花\n月夜》中的句子。')
        self.assertFalse(q['embedded_segments'])

    def test_bonus_is_not_counted(self):
        raw=parse_plain_lines(['语文限时训练','一、阅读','1．请概括。（3分）','【附加题】（不计入分数）','2．请分析。（6分）'])
        self.assertEqual(calculate_score_summary(raw).total,3)
        self.assertFalse(raw['blocks'][-1]['question']['counts_toward_total'])

    def test_same_font_family_for_synthetic_bold(self):
        self.assertEqual(resolved_font_path('SimSun',bold=False),resolved_font_path('SimSun',bold=True))

    def test_joined_answers_and_title(self):
        with TemporaryDirectory() as folder:
            source=Path(folder)/'answers.docx'
            doc=Document(); doc.add_paragraph('阶段训练');doc.add_paragraph('语文 答案')
            doc.add_paragraph('1.A【解析】说明。2.B【解析】解释。')
            doc.save(source)
            raw=import_exam(source)
            self.assertEqual(raw['document_kind'],'answer')
            self.assertEqual([b['number'] for b in raw['blocks']], [1,2])
            self.assertEqual(raw['metadata']['answer_title_lines'],['阶段训练','语文 答案'])
            self.assertFalse(validate_exam(ExamDocument.from_dict(raw)))

    def test_answer_score_display(self):
        from app.ui.main_window import create_application
        from app.ui.outline_panel import OutlinePanel

        create_application([])
        panel = OutlinePanel()
        state = panel.refresh_score({"document_kind": "answer", "blocks": []})
        self.assertEqual(panel.score_total_label.text(), "独立答案文档")
        self.assertTrue(state.complete)

class SeptemberLocalTests(unittest.TestCase):
    def test_six_local_documents(self):
        if not all((DESKTOP/name).exists() for name in SOURCES):
            self.skipTest('Local September originals unavailable')
        for index,name in enumerate(SOURCES):
            with self.subTest(name=name), TemporaryDirectory() as folder:
                source=DESKTOP/name
                original=sha256(source.read_bytes()).hexdigest()
                raw=import_exam(source)
                if index in (1,3,5):
                    self.assertEqual(raw['document_kind'],'answer')
                    self.assertEqual([b['number'] for b in raw['blocks'] if b['type']=='answer_question'],list(range(1,15 if index==5 else 24)))
                    self.assertFalse(validate_exam(ExamDocument.from_dict(raw)))
                else:
                    self.assertEqual(calculate_score_summary(raw).total,47 if index==4 else 150)
                target,_,_=build_documents(raw,ROOT/'templates/layout.yaml',folder,'result')
                result=Document(target)
                text='\n'.join(p.text for p in result.paragraphs)
                if index in (1,3,5):
                    for p in Document(source).paragraphs:
                        if p.text.strip():
                            self.assertIn(compact(p.text),compact(text))
                if index==0:
                    self.assertEqual(len(result.tables),1)
                    self.assertEqual(len(result.element.xpath('.//w:drawing')),1)
                    self.assertEqual(len([b for b in raw['blocks'] if b['type']=='poetry']),2)
                if index==2:
                    self.assertEqual(len(raw['metadata']['notices']),4)
                    self.assertIn('本试卷满分150分',raw['metadata']['meta_text'])
                    self.assertNotIn('语文科\n试题',text)
                    for p in Document(source).paragraphs[4:7]:
                        self.assertIn(compact(p.text[2:]),compact(text))
                if index==4:
                    self.assertIn('《春江花\n月夜》',text)
                    p=next(p for p in result.paragraphs if '摄影爱好者' in p.text)
                    self.assertTrue(any(r.underline for r in p.runs))
                self.assertEqual(sha256(source.read_bytes()).hexdigest(),original)
