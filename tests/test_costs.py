"""Offline cost-ledger regression tests; all requests and keys are fictional."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock

from wewriter.api import Dajiala, Model, Transport
from wewriter.config import defaults
from wewriter.costs import CostMeter, summary
from wewriter.db import Database
from wewriter.pipeline.collect import Funnel
from wewriter.webapp import Api, Backend


class Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, *_):
        return self.value


class StreamResponse(Response):
    def __init__(self, events):
        self.lines = [part for event in events for part in
                      (b'data: ' + json.dumps(event).encode() + b'\n', b'\n')]
        self.lines += [b'data: [DONE]\n', b'\n']

    def readline(self, *_):
        return self.lines.pop(0) if self.lines else b''


class CostsTest(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'costs.sqlite')
        self.job = self.db.job('cost-fixture', defaults())
        self.funnel = Funnel.for_attempt(self.db, self.job, False)
        self.meter = CostMeter(self.funnel)

    def test_actual_provider_call_and_model_tokens_are_separate(self):
        transport = Transport()
        transport.cost_meter = self.meter
        transport.opener = Mock()
        transport.opener.open.side_effect = [
            Response({'code': 0, 'data': []}),
            Response({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
                      'usage': {'prompt_tokens': 1000, 'completion_tokens': 200,
                                'prompt_cache_hit_tokens': 100}})]
        Dajiala('OBVIOUSLY_FAKE_TEST_KEY', transport).history('虚构公众号')
        Model('deepseek', {'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com'},
              'OBVIOUSLY_FAKE_TEST_KEY', transport).complete('test', 'test')
        result = summary(self.db, self.job['id'])
        self.assertTrue(result['tracked'])
        self.assertEqual([(g['kind'], g['requests']) for g in result['groups']],
                         [('collection', 1), ('model', 1)])
        self.assertEqual(result['groups'][1]['input_tokens'], 1000)
        self.assertEqual(result['groups'][1]['cached_tokens'], 100)
        self.assertEqual(result['unknown_requests'], 0)
        stored = self.db.rows('SELECT funnel FROM jobs WHERE id=?', (self.job['id'],))[0]['funnel']
        self.assertNotIn('OBVIOUSLY_FAKE_TEST_KEY', stored)

    def test_streamed_writing_usage_is_recorded(self):
        transport = Transport()
        transport.cost_meter = self.meter
        transport.opener = Mock()
        transport.opener.open.return_value = StreamResponse([
            {'choices': [{'delta': {'content': '{}'}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 300, 'completion_tokens': 100}},
        ])
        result = Model('deepseek', {'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com'},
                       'OBVIOUSLY_FAKE_TEST_KEY', transport).complete('test', 'test', stream=True)
        self.assertEqual(result, {})
        group = summary(self.db, self.job['id'])['groups'][0]
        self.assertEqual((group['requests'], group['input_tokens'], group['output_tokens']), (1, 300, 100))
        self.assertEqual(group['unknown'], 0)

    def test_retry_preserves_previous_attempts_and_unknown_costs(self):
        first = self.meter.begin('https://api.deepseek.com/chat/completions', {'model': 'deepseek-flash'})
        second = self.meter.begin('https://api.deepseek.com/chat/completions', {'model': 'deepseek-flash'})
        self.meter.finish(second, {'usage': {'prompt_tokens': 40, 'completion_tokens': 10}})
        self.assertNotEqual(first, second)
        saved = self.db.rows('SELECT * FROM jobs WHERE id=?', (self.job['id'],))[0]
        Funnel.for_attempt(self.db, saved, False)
        result = summary(self.db, self.job['id'])
        self.assertEqual(result['groups'][0]['requests'], 2)
        self.assertEqual(result['unknown_requests'], 1)

    def test_transport_http_retry_counts_both_attempts(self):
        transport = Transport()
        transport.cost_meter = self.meter
        transport.retry_attempts = 2
        transport.cancel.wait = lambda _seconds: False
        transport.opener = Mock()
        transient = urllib.error.HTTPError('https://www.dajiala.com/fbmain/monitor/v3/post_history',
                                            503, 'temporary', {'Retry-After': '0'}, None)
        transport.opener.open.side_effect = [transient, Response({'code': 0, 'data': []})]
        Dajiala('OBVIOUSLY_FAKE_TEST_KEY', transport).history('虚构公众号')
        result = summary(self.db, self.job['id'])
        self.assertEqual(result['groups'][0]['requests'], 2)
        self.assertEqual(result['unknown_requests'], 1)

    def test_background_search_poll_is_not_another_billable_request(self):
        event = self.meter.begin('https://api.openai.com/v1/responses',
                                 {'model': 'gpt-5-mini', 'tools': [{'type': 'web_search'}]})
        self.meter.finish(event, {'id': 'resp_fictional', 'status': 'queued'})
        result = {'id': 'resp_fictional', 'status': 'completed',
                  'usage': {'input_tokens': 200, 'output_tokens': 20},
                  'output': [{'type': 'web_search_call', 'status': 'completed',
                              'action': {'type': 'search'}}]}
        self.meter.finish_search(result)
        self.meter.finish_search(result)
        found = summary(self.db, self.job['id'])
        self.assertEqual(found['groups'][0]['requests'], 1)
        self.assertEqual(found['groups'][0]['search_calls'], 1)
        self.assertEqual(found['unknown_requests'], 0)

    def test_luna_prices_nested_cache_write_lifetimes(self):
        event = self.meter.begin('https://api.openai.com/v1/responses',
                                 {'model': 'gpt-6-luna', 'tools': [{'type': 'web_search'}]})
        self.meter.finish(event, {'id': 'resp_luna_fictional', 'status': 'completed',
            'usage': {'input_tokens': 12000, 'output_tokens': 500,
                      'input_tokens_details': {'cached_tokens': 4000, 'cache_write_tokens': 7000,
                          'cache_write_tokens_details': {'ttl_30m_tokens': 3000,
                                                         'ttl_12h_tokens': 4000}}},
            'output': [{'type': 'web_search_call', 'status': 'completed',
                        'action': {'type': 'search'}}]})
        found = summary(self.db, self.job['id'])
        self.assertEqual(found['unknown_requests'], 0)
        self.assertEqual(found['groups'][0]['search_calls'], 1)
        self.assertEqual(found['groups'][0]['cny'], '0.0788')
        self.assertEqual(self.meter.events[0]['cache_writes'], [3000, 4000])

    def test_luna_aggregate_cache_writes_use_request_default_ttl(self):
        event = self.meter.begin('https://api.openai.com/v1/responses', {'model': 'gpt-6-luna'})
        self.meter.finish(event, {'usage': {'input_tokens': 1000, 'output_tokens': 100,
            'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 500}}})
        found = summary(self.db, self.job['id'])
        self.assertEqual(self.meter.events[0]['cache_writes'], [500, 0])
        self.assertEqual(found['groups'][0]['cny'], '0.0011')
        self.assertEqual(found['unknown_requests'], 0)

    def test_older_openai_model_does_not_apply_cache_write_premium(self):
        event = self.meter.begin('https://api.openai.com/v1/responses', {'model': 'gpt-5-mini'})
        self.meter.finish(event, {'usage': {'input_tokens': 1000, 'output_tokens': 100,
            'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 500}}})
        found = summary(self.db, self.job['id'])
        self.assertEqual(found['unknown_requests'], 0)
        self.assertEqual(found['groups'][0]['cny'], '0.0030')

    def test_old_luna_event_without_write_breakdown_stays_unknown(self):
        event = self.meter.begin('https://api.openai.com/v1/responses', {'model': 'gpt-6-luna'})
        self.meter.finish(event, {'usage': {'input_tokens': 1000, 'output_tokens': 100,
            'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 500,
                'cache_write_tokens_details': {'ttl_30m_tokens': 400, 'ttl_12h_tokens': 400}}}})
        found = summary(self.db, self.job['id'])
        self.assertEqual(found['unknown_requests'], 1)
        self.assertEqual(found['cache_unknown_requests'], 1)
        self.assertEqual(found['known_cny'], '0.0000')

    def test_parent_and_child_add_once(self):
        self.meter.finish(self.meter.begin('https://www.dajiala.com/fbmain/monitor/v3/post_history',
                                           {'nickname': '虚构'}), {'code': 0, 'data': []})
        child = self.db.job('cost-child', defaults())
        self.db.execute('INSERT INTO batch_items VALUES(?,?,?)', (self.job['id'], 999, child['id']))
        child_meter = CostMeter(Funnel.for_attempt(self.db, child, False))
        event = child_meter.begin('https://api.deepseek.com/chat/completions',
                                  {'model': 'deepseek-v4-pro'})
        child_meter.finish(event, {'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
        result = summary(self.db, self.job['id'])
        self.assertEqual(sum(g['requests'] for g in result['groups']), 2)

    def test_unknown_model_and_old_job_are_not_zero_cost_claims(self):
        self.assertFalse(summary(self.db, self.job['id'])['tracked'])
        event = self.meter.begin('https://api.openai.com/v1/responses', {'model': 'future-model'})
        self.meter.finish(event, {'usage': {'input_tokens': 500, 'output_tokens': 50}})
        result = summary(self.db, self.job['id'])
        self.assertEqual(result['unknown_requests'], 1)
        self.assertEqual(result['known_cny'], '0.0000')

    def test_workbench_exposes_current_and_historical_costs(self):
        event = self.meter.begin('https://www.dajiala.com/fbmain/monitor/v3/post_history',
                                 {'nickname': '虚构'})
        self.meter.finish(event, {'code': 0, 'data': []})
        api = Api(Backend(self.db, Mock(), self.temp.name))
        current = api.wb_state()
        self.assertTrue(current['ok'])
        self.assertEqual(current['job']['costs']['groups'][0]['requests'], 1)
        detail = api.wb_detail(self.job['id'])
        self.assertTrue(detail['ok'])
        self.assertEqual(detail['costs']['known_cny'], '0.1600')


if __name__ == '__main__':
    unittest.main()
