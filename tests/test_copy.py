"""T-014: copy a draft's clean text to the clipboard without exporting a file."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wewriter.db import Database
from wewriter.ui import clipboard_text
from wewriter.webapp import Api, Backend
from wewriter.windows import cf_html

SOURCES = [{'source_id': 'S1', 'title': '公众号原文', 'url': 'https://mp.weixin.qq.com/s/source'},
           {'source_id': 'W1', 'title': '政府网站', 'url': 'https://gov.example/policy'}]
BODY = ('## 一、政策变化\n\n今年补贴提高到 **50 万元**。[S1]\n\n- 申报截止 11 月 30 日 [W1]\n- 需要 <盖章> 材料\n\n'
        '1. 先自查\n2. 再申报\n\n> 以官方通知为准\n\n| 年份 | 金额 |\n|---|---|\n| 2025 | 30 万 |\n| 2026 | 50 万 |')


class ClipboardTextTest(unittest.TestCase):
    def test_plain_text_is_clean_and_readable(self):
        plain, _ = clipboard_text('标题', BODY, SOURCES)
        for gone in ('[S1]', '[W1]', '**', '## ', '](http', '|---'):
            self.assertNotIn(gone, plain)
        self.assertTrue(plain.startswith('标题\n\n一、政策变化\n\n今年补贴提高到 50 万元。'))
        self.assertIn('- 申报截止 11 月 30 日', plain)
        self.assertIn('1. 先自查\n2. 再申报', plain)
        self.assertIn('2026\t50 万', plain)
        self.assertIn('政府网站：https://gov.example/policy', plain)

    def test_html_keeps_structure_and_escapes_text(self):
        _, html = clipboard_text('标题', BODY, SOURCES)
        for need in ('<h1>标题</h1>', '<h2>一、政策变化</h2>', '<strong>50 万元</strong>', '<li>先自查</li>',
                     '<blockquote>以官方通知为准</blockquote>', '<th style', '<td style="border:1px solid #ccc;padding:4px 8px">50 万</td>',
                     '<a href="https://gov.example/policy">政府网站</a>'):
            self.assertIn(need, html)
        self.assertIn('&lt;盖章&gt;', html)
        self.assertNotIn('<盖章>', html)
        self.assertNotIn('[S1]', html)

    def test_html_escapes_quotes_before_creating_link_attributes(self):
        _, html = clipboard_text('标题', '[链接](https://example.test/a?x="onclick=bad)', [])
        self.assertIn('href="https://example.test/a?x=&quot;onclick=bad"', html)
        self.assertNotIn('href="https://example.test/a?x="onclick', html)

    def test_cf_html_offsets_point_at_the_fragment(self):
        data = cf_html('<p>中文</p>')
        self.assertTrue(data.endswith(b'\0'))
        header = data.decode('utf-8').split('\r\n')
        offsets = {k: int(v) for k, v in (line.split(':') for line in header[1:5])}
        self.assertEqual(data[offsets['StartFragment']:offsets['EndFragment']].decode('utf-8'), '<p>中文</p>')
        self.assertTrue(data[offsets['StartHTML']:].startswith(b'<html>'))
        self.assertEqual(data[offsets['EndHTML']:], b'\0')


class CopyApiTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'w.sqlite')
        job = self.db.job('copy-job', self.db.config())
        self.draft_id = self.db.save_draft(job['id'], '标题', BODY, SOURCES, '报告')
        self.api = Api(Backend(self.db, Mock(), Path(self.temp.name)))

    def test_copies_clean_text_and_html(self):
        with patch('wewriter.webapp.copy_to_clipboard', return_value=True) as put:
            result = self.api.dr_copy(self.draft_id)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['copied'])
        text, html = put.call_args.args
        self.assertEqual(result['text'], text)
        self.assertNotIn('[S1]', text)
        self.assertIn('<h2>', html)
        self.assertGreater(result['chars'], 20)

    def test_reports_clipboard_refusal_so_the_page_can_fall_back(self):
        with patch('wewriter.webapp.copy_to_clipboard', return_value=False):
            result = self.api.dr_copy(self.draft_id)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['copied'])
        self.assertIn('今年补贴提高到 50 万元。', result['text'])

    def test_cleared_or_missing_draft_is_an_error(self):
        self.db.execute('UPDATE drafts SET cleared=1 WHERE id=?', (self.draft_id,))
        self.assertFalse(self.api.dr_copy(self.draft_id)['ok'])
        self.assertFalse(self.api.dr_copy(9999)['ok'])
