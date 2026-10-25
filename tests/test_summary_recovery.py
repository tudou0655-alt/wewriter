import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from wewriter.api import ApiError, Cancelled, Model, Transport
from wewriter.config import defaults
from wewriter.db import Database
from wewriter.engine import Engine, _Run
from wewriter.webapp import article_state
from test_core import FakeModel, FakeSource, FakeVault


class SummaryRecoveryTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'test.sqlite')
        self.db.add_account('虚构账号', 'test')
        self.config = defaults()
        self.config.update(writing_mode='combined', selection_mode='recent', min_sources=1)
        FakeSource.calls = 0
        FakeModel.failing_write = False

    def test_exhaustion_preserves_body_and_resume_uses_cache(self):
        class Flaky(FakeModel):
            calls = 0
            failing = True

            def summary(self, article, content):
                Flaky.calls += 1
                if Flaky.failing:
                    raise ApiError('虚构网络故障', kind='network')
                return super().summary(article, content)

        e = Engine(self.db, FakeVault(), source_factory=FakeSource, model_factory=Flaky)
        with patch.object(e.cancel, 'wait', return_value=False):
            self.assertEqual(e.run('retry-summary', self.config), '失败')
        self.assertEqual(Flaky.calls, 3)
        row = self.db.rows("SELECT * FROM articles WHERE content<>''")[0]
        self.assertTrue(row['error'].startswith('摘要分析失败'))
        job = self.db.rows("SELECT * FROM jobs WHERE run_key='retry-summary'")[0]
        self.assertEqual(job['error_code'], 'network')
        counts = json.loads(job['funnel'])['counts']
        self.assertEqual(counts['summary_failed'], 1)
        self.assertEqual(counts['body_failed'], 0)
        Flaky.failing = False
        self.assertEqual(e.run('retry-summary', self.config), '已完成')
        self.assertEqual(FakeSource.calls, 2)  # first body not fetched again
        self.assertFalse(self.db.rows('SELECT * FROM articles WHERE error<>\'\''))

    def context(self, model):
        e = Engine(self.db, FakeVault())
        t = Transport(e.cancel)
        ctx = _Run(e, self.db.job('unit', self.config), self.config, t, None)
        ctx._models[self.config['summary_provider']] = model
        return e, ctx, t

    def test_real_transport_retries_do_not_multiply_and_diagnostics_safe(self):
        e, ctx, t = self.context(None)
        ctx._models[self.config['summary_provider']] = Model(
            self.config['summary_provider'], self.config['providers'][self.config['summary_provider']],
            'FAKE_TEST_KEY', t)
        t.opener = Mock()
        t.opener.open.side_effect = URLError(socket.gaierror('FAKE_SECRET_MUST_NOT_BE_LOGGED'))
        with patch.object(e.cancel, 'wait', return_value=False), self.assertLogs('wewriter', level='WARNING') as logs:
            with self.assertRaises(ApiError) as err:
                e._summary_attempts(ctx, {'id': 1, 'title': '虚构标题'}, '虚构正文', 1)
        self.assertEqual(t.opener.open.call_count, 3)
        self.assertIn('DNS', str(err.exception))
        self.assertNotIn('FAKE_SECRET_MUST_NOT_BE_LOGGED', ''.join(logs.output))
        self.assertEqual(t.retry_attempts, 3)

    def test_success_after_one_failure(self):
        model = Mock()
        model.summary.side_effect = [ApiError('超时', kind='network'), {'summary': '成功'}]
        e, ctx, _ = self.context(model)
        with patch.object(e.cancel, 'wait', return_value=False):
            self.assertEqual(e._summary_attempts(ctx, {'id': 1}, '正文', 1), {'summary': '成功'})
        self.assertEqual(model.summary.call_count, 2)

    def test_fatal_and_output_errors_are_not_retried(self):
        for error in (ApiError('虚构权限错误', True), ApiError('输出不完整')):
            model = Mock()
            model.summary.side_effect = error
            e, ctx, _ = self.context(model)
            with self.assertRaises(ApiError):
                e._summary_attempts(ctx, {'id': 1}, '正文', 1)
            self.assertEqual(model.summary.call_count, 1)

    def test_cancel_during_backoff(self):
        model = Mock()
        model.summary.side_effect = ApiError('超时', kind='network')
        e, ctx, t = self.context(model)
        with patch.object(e.cancel, 'wait', return_value=True), self.assertRaises(Cancelled):
            e._summary_attempts(ctx, {'id': 1}, '正文', 1)
        self.assertEqual(model.summary.call_count, 1)
        self.assertEqual(t.retry_attempts, 3)

    def test_new_and_legacy_article_errors(self):
        self.assertEqual(article_state(False, '已保存正文', '旧的网络错误'), 'processing')
        self.assertEqual(article_state(False, '已保存正文', '摘要分析失败（正文已保存）：超时'), 'summary')
        self.assertEqual(article_state(False, '', '正文错误'), 'bad')

    def test_successful_chunks_are_reused(self):
        article_id = self.db.save_article(dict(source_key='chunks', account_name='虚构账号',
                                              title='分段测试', url='https://example.test/chunks',
                                              published_at='2026-10-25T00:00:00+00:00'))
        model = Mock()
        model.summary.side_effect = [{'summary': '第一段'}, ApiError('超时', kind='network'),
                                     ApiError('超时', kind='network'), ApiError('超时', kind='network')]
        e, ctx, _ = self.context(model)
        with patch.object(e.cancel, 'wait', return_value=False), self.assertRaises(ApiError):
            e._summaries(ctx, {'id': article_id}, '甲' * 24000 + '乙' * 200)
        model.summary.side_effect = None
        model.summary.return_value = {'summary': '第二段'}
        result = e._summaries(ctx, {'id': article_id}, '甲' * 24000 + '乙' * 200)
        self.assertEqual(result, [{'summary': '第一段'}, {'summary': '第二段'}])
        self.assertEqual(model.summary.call_count, 5)

    def test_long_server_retry_delay_is_not_ignored(self):
        model = Mock()
        error = ApiError('HTTP 503')
        error.retry_delay = 900
        model.summary.side_effect = error
        e, ctx, _ = self.context(model)
        with self.assertRaises(ApiError):
            e._summary_attempts(ctx, {'id': 1}, '正文', 1)
        self.assertEqual(model.summary.call_count, 1)

