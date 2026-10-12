import unittest

from wewriter.ui import export_document, source_website_text


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {'source_id': 'S2', 'title': '公众号原文', 'url': 'https://mp.weixin.qq.com/s/source'},
            {'source_id': 'W2', 'title': '政府网站', 'url': 'https://gov.example/policy'},
            {'source_id': 'W9', 'title': '未引用资料', 'url': 'https://example.org/unused'},
        ]

    def test_export_removes_internal_marks_and_duplicate_source_section(self):
        body = '正文事实。[S2] 补充事实 [W2]\n\n## 参考来源\n\n- 重复来源 [W2]'
        result = export_document('标题', body, self.sources)
        self.assertNotIn('[S2]', result)
        self.assertNotIn('[W2]', result)
        self.assertEqual(result.count('## 参考资料网站'), 1)
        self.assertIn('[公众号原文](https://mp.weixin.qq.com/s/source)', result)
        self.assertIn('[政府网站](https://gov.example/policy)', result)
        self.assertNotIn('未引用资料', result)

    def test_txt_has_urls_without_markdown_or_ids(self):
        result = export_document('标题', '正文[S2]', self.sources, markdown=False)
        self.assertIn('公众号原文：https://mp.weixin.qq.com/s/source', result)
        self.assertNotIn('[S2]', result)
        self.assertNotIn('](http', result)

    def test_website_view_uses_cited_sources(self):
        result = source_website_text('正文[W2]', self.sources)
        self.assertIn('https://gov.example/policy', result)
        self.assertNotIn('unused', result)
