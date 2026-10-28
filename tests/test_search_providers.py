"""Offline provider switching and official-price accounting with fictional responses."""
import tempfile
import unittest
from pathlib import Path

from wewriter.api import ApiError, Model
from wewriter.config import defaults, migrate, validate
from wewriter.costs import CostMeter, _deepseek_band, summary
from wewriter.db import Database
from wewriter.engine import _Run
from wewriter.pipeline.collect import Funnel
from wewriter.research import DeepSeekResearch
from datetime import datetime
from zoneinfo import ZoneInfo


class FakeTransport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def request(self, url, key, payload, **kwargs):
        self.calls.append((url, key, payload))
        return self.result


class ProviderTest(unittest.TestCase):
    def test_new_defaults_and_old_job_snapshot(self):
        current = defaults()
        self.assertEqual((current['search_provider'], current['summary_provider'],
                          current['writer_provider']), ('deepseek', 'deepseek', 'deepseek'))
        validate(current)
        old = migrate({'search_url': 'https://api.openai.com/v1/responses',
                       'search_model': 'gpt-5-mini'})
        self.assertEqual(old['search_provider'], 'openai')
        bailian = migrate({'search_provider': 'bailian', 'search_model': 'qwen3.8-flash',
            'search_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1/responses'})
        self.assertEqual(bailian['search_url'],
                         'https://dashscope.aliyuncs.com/compatible-mode/v1/responses')

    def test_deepseek_search_uses_native_endpoint_and_real_urls(self):
        fake = FakeTransport({'results': [
            {'url': 'https://example.gov.cn/notice', 'title': '政策原件',
             'summary': '2026 年公布申请条件', 'date': '2026-10-01'},
            {'url': 'https://example.gov.cn/notice', 'title': '重复链接'},
            {'url': 'javascript:alert(1)', 'title': '坏链接'}]})
        result = DeepSeekResearch('FAKE_KEY', defaults(), fake).search({'title': '政策申请条件'})
        self.assertEqual(len(result['sources']), 1)
        self.assertIn('[W1]', result['notes'])
        self.assertIn('搜索摘要，使用前核对原件', result['notes'])
        self.assertEqual(fake.calls[0][0], 'https://api.deepseek.com/v1/tools/search')
        self.assertEqual(fake.calls[0][2]['count'], 8)

    def test_empty_search_cannot_use_model_memory(self):
        with self.assertRaises(ApiError):
            DeepSeekResearch('FAKE_KEY', defaults(), FakeTransport({'results': []})).search(
                {'title': '虚构政策'})

    def test_key_facts_get_second_search_and_verify_requests_page_content(self):
        fake = FakeTransport({'results': [{'url': 'https://example.gov.cn/a',
            'title': '官方公告', 'summary': '摘要', 'content': '正文中的项目编号 FICT-123'}]})
        client = DeepSeekResearch('FAKE_KEY', defaults(), fake)
        client.search({'title': '虚构申报项目', 'key_facts': [
            {'identifiers': ['FICT-123'], 'claim': '虚构说法'}]})
        self.assertEqual(len(fake.calls), 2)
        self.assertIn('FICT-123', fake.calls[1][2]['query'])
        finding = client.verify('虚构项目', [{'identifiers': ['FICT-123']}])
        self.assertTrue(fake.calls[-1][2]['need_content'])
        self.assertIn('网页正文节选', finding['notes'])

    def test_bailian_text_uses_responses_protocol(self):
        response = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': '{}'}]}]}
        fake = FakeTransport(response)
        found = Model('bailian', {'model': 'qwen3.8-flash',
            'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1'},
            'FAKE_KEY', fake).complete('只输出 JSON', '虚构资料')
        self.assertEqual(found, {})
        self.assertEqual(fake.calls[0][0], 'https://dashscope.aliyuncs.com/compatible-mode/v1/responses')

    def test_same_deepseek_provider_can_use_cheaper_summary_model(self):
        class EngineStub:
            vault = type('VaultStub', (), {'get': lambda _self, _name: 'FAKE_KEY'})()
            model_factory = staticmethod(lambda provider, settings, key, transport: settings['model'])
        config = defaults()
        config['providers']['deepseek']['model'] = 'deepseek-v4-pro'
        config['summary_model'] = 'deepseek-flash'
        run = _Run(EngineStub(), {'id': 1}, config, FakeTransport({}), None)
        self.assertEqual(run.summarizer, 'deepseek-flash')
        self.assertEqual(run.writer, 'deepseek-v4-pro')


class PricesTest(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'prices.sqlite')
        job = self.db.job('prices', defaults())
        self.meter = CostMeter(Funnel.for_attempt(self.db, job, False))
        self.job_id = job['id']

    def test_deepseek_search_fee_only_when_results_exist(self):
        url = 'https://api.deepseek.com/v1/tools/search'
        first = self.meter.begin(url, {'query': '虚构'})
        self.meter.finish(first, {'results': [{'url': 'https://example.org'}]})
        second = self.meter.begin(url, {'query': '无结果'})
        self.meter.finish(second, {'results': []})
        result = summary(self.db, self.job_id)
        self.assertEqual(result['groups'][0]['search_calls'], 1)
        self.assertEqual(result['known_cny'], '0.0050')
        self.assertEqual(result['unknown_requests'], 0)

    def test_bailian_cache_tokens_and_actual_tool_count(self):
        event = self.meter.begin('https://dashscope.aliyuncs.com/compatible-mode/v1/responses',
                                 {'model': 'qwen3.8-flash', 'tools': [{'type': 'web_search'}]})
        self.meter.finish(event, {'status': 'completed', 'usage': {
            'input_tokens': 1_000_000, 'output_tokens': 1_000_000,
            'input_tokens_details': {'cached_tokens': 200_000},
            'x_tools': {'web_search': {'count': 2}}}, 'output': []})
        found = summary(self.db, self.job_id)
        self.assertEqual(found['groups'][0]['search_calls'], 2)
        self.assertEqual(found['known_cny'], '3.3680')

    def test_deepseek_offpeak_and_holiday(self):
        zone = ZoneInfo('Asia/Shanghai')
        self.assertEqual(_deepseek_band(datetime(2026, 10, 27, 10, tzinfo=zone)), 'peak')
        self.assertEqual(_deepseek_band(datetime(2026, 10, 2, 10, tzinfo=zone)), 'offpeak')
        self.assertEqual(_deepseek_band(datetime(2026, 10, 27, 20, tzinfo=zone)), 'offpeak')

    def test_openai_long_context_and_default_cache_write_price(self):
        url = 'https://api.openai.com/v1/responses'
        event = self.meter.begin(url, {'model': 'gpt-6-luna'})
        self.meter.finish(event, {'usage': {'input_tokens': 1_000_000,
            'output_tokens': 1_000_000, 'input_tokens_details': {
                'cached_tokens': 100_000, 'cache_write_tokens': 100_000,
                'cache_write_tokens_details': {'ttl_30m_tokens': 100_000,
                                               'ttl_12h_tokens': 0}}}})
        found = summary(self.db, self.job_id)
        # (800k * $0.20 + 100k * $0.02 + 100k * $0.25 + 1M * $0.75) * 6.70
        self.assertEqual(found['known_cny'], '6.2779')
        second = self.meter.begin(url, {'model': 'gpt-6-luna'})
        self.meter.finish(second, {'usage': {'input_tokens': 1_000, 'output_tokens': 100,
            'input_tokens_details': {'cache_write_tokens': 100}}})
        self.assertEqual(summary(self.db, self.job_id)['unknown_requests'], 0)


if __name__ == '__main__':
    unittest.main()
