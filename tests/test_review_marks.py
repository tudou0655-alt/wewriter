"""T-017: reviewer hints are placed on the paragraphs they concern so the page can mark them in red."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from wewriter.db import Database
from wewriter.ui import review_marks
from wewriter.webapp import Api, Backend

TITLE = '2026 年专精特新补贴提高到 50 万元'
BODY = ('今年补贴提高到 **50 万元**，比去年多 20 万元。[S1]\n\n'
        '## 申报条件\n\n'
        '据业内人士透露，“已有超过三千家企业提交申请”。[W1]\n\n'
        '- 申报截止 11 月 30 日 [W1]\n- 材料需盖章\n\n'
        '这一政策受到广泛关注，各地企业纷纷响应。')
REPORT = ('# 每日素材报告\n\n## 写作大纲\n- a\n\n## 审核提示\n'
          '- 引号内容未能在资料中逐字核对，发布前请核实或改为转述：“已有超过三千家企业提交申请”\n'
          '- 可能新增了资料没有的传播范围、关注度、时间或判断依据，发布前请核实或删除：这一政策受到广泛关注，各地企业纷纷响应。\n'
          '- 二审逐句核对没有包含标题，发布前请人工核对标题：2026 年专精特新补贴提高到 50 万元\n'
          '- 补贴金额需以正式文件为准\n\n'
          '## AI 二审\n'
          '- 逐句核对｜保留｜申报截止 11 月 30 日｜证据：无｜官方通知未给出截止日\n'
          '- 逐句核对｜保留｜今年补贴提高到 50 万元｜证据：S1：补贴提高到 50 万元\n'
          '- 本次修改了标题\n\n'
          '来源标记与引用片段已做格式校验。\n')


class ReviewMarksTest(unittest.TestCase):
    def test_hints_land_on_their_paragraphs_and_title(self):
        marks, unplaced = review_marks(TITLE, BODY, REPORT)
        self.assertEqual(sorted(marks), [-1, 2, 3, 4])
        self.assertIn('三千家', marks[2][0])
        self.assertIn('广泛关注', marks[4][0])
        self.assertTrue(marks[3][0].startswith('二审未找到证据：申报截止'))
        self.assertIn('标题', marks[-1][0])
        self.assertEqual(unplaced, ['补贴金额需以正式文件为准'])

    def test_supported_sentences_and_other_sections_are_not_marked(self):
        marks, _ = review_marks(TITLE, BODY, REPORT)
        self.assertNotIn(0, marks)  # its check has evidence
        self.assertNotIn(1, marks)

    def test_no_report_or_edited_text_marks_nothing(self):
        self.assertEqual(review_marks(TITLE, BODY, ''), ({}, []))
        marks, unplaced = review_marks(TITLE, '全新改写的正文。', REPORT)
        self.assertEqual(marks.keys() - {-1}, set())
        self.assertIn('补贴金额需以正式文件为准', unplaced)

    def test_short_fragments_do_not_mark_unrelated_paragraphs(self):
        marks, unplaced = review_marks('标题', '材料需盖章。\n\n其他内容。', '## 审核提示\n- 数据：盖章\n')
        self.assertEqual(marks, {})
        self.assertEqual(unplaced, ['数据：盖章'])


class DraftApiTest(unittest.TestCase):
    def test_draft_view_carries_marks(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / 'w.sqlite')
        job = db.job('marks-job', db.config())
        draft_id = db.save_draft(job['id'], TITLE, BODY, [], REPORT)
        result = Api(Backend(db, Mock(), Path(temp.name))).dr_get(draft_id)
        self.assertTrue(result['ok'], result)
        self.assertEqual(sorted(result['marks']), ['-1', '2', '3', '4'])
        self.assertEqual(result['unplaced'], ['补贴金额需以正式文件为准'])
