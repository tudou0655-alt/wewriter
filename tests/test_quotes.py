import unittest
from unittest.mock import Mock
from wewriter.api import original_quote, Model, ApiError

class QuoteTests(unittest.TestCase):
    def test_layout_returns_source_span(self):
        self.assertEqual(original_quote('政策明确要求', '前言：政策\n明确  要求。'), '政策\n明确  要求')
        self.assertEqual(original_quote('“项目申报政策”', '「项目申报政策」'), '项目申报政策')
    def test_content_changes_rejected(self):
        for quote, source in [('可以申报','不可以申报')]:
            # A substring match is not semantic entailment; validation only verifies source text.
            self.assertEqual(original_quote(quote,source),quote)
        for quote, source in [('补贴1000元','补贴100元'), ('实施办法','执行办法'), ('第一句第三句','第一句第二句第三句'), ('ab','a b')]:
            self.assertIsNone(original_quote(quote,source))
    def test_one_bounded_correction(self):
        def result(q): return {'summary':'摘要','facts':[{'claim':'观点','quote':q}],'uncertainties':[]}
        m=Model('deepseek',{},'',None)
        m.complete=Mock(side_effect=[result('改写内容'),result('原文内容')])
        self.assertEqual(m.summary({'title':'测试'},'原文内容')['facts'][0]['quote'],'原文内容')
        self.assertEqual(m.complete.call_count,2)
        m.complete=Mock(return_value=result('改写内容'))
        fallback = m.summary({'title':'测试'},'这是一段足够长度的原文内容，用于安全的摘录兜底。')
        self.assertIn('原文连续片段', fallback['uncertainties'][0])
        self.assertEqual(m.complete.call_count,2)
