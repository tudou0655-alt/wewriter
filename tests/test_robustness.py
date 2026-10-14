"""Regression tests for v0.10.1 stability fixes."""
import json
import os
import socket
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import test_core
from test_hotspots import HotSource
from test_independent import IndependentModel, ResearchFake
from wewriter.api import ApiError, Model, Transport
from wewriter.config import defaults
from wewriter.db import Database
from wewriter.engine import Engine
from wewriter.weread import WeReadBrowser, WeReadSource, cleanup_stale_profiles

BOOK = 'MP_WXS_123'


class SequenceTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []

    def request(self, url, key, payload=None, *, timeout=100):
        self.payloads.append(payload)
        return self.responses.pop(0)


def chat(result):
    return {'choices': [{'finish_reason': 'stop',
                         'message': {'content': json.dumps(result, ensure_ascii=False)}}]}


class DraftRepairTest(unittest.TestCase):
    def test_invalid_citation_is_repaired_once(self):
        bad = chat({'title': '标题', 'body': '正文[S999]', 'outline': [], 'review_notes': []})
        good = chat({'title': '标题', 'body': '正文[S1]', 'outline': [], 'review_notes': []})
        transport = SequenceTransport(bad, good)
        model = Model('deepseek', defaults()['providers']['deepseek'], 'x', transport)
        self.assertEqual(model.write([{'source_id': 'S1'}], defaults())['body'], '正文[S1]')
        self.assertEqual(len(transport.payloads), 2)
        feedback = json.loads(transport.payloads[1]['messages'][1]['content'])['validation_feedback']
        self.assertIn('来源标记', feedback)

    def test_repair_is_attempted_only_once(self):
        bad = chat({'title': '标题', 'body': '正文[S999]', 'outline': [], 'review_notes': []})
        transport = SequenceTransport(bad, bad, bad)
        model = Model('deepseek', defaults()['providers']['deepseek'], 'x', transport)
        with self.assertRaises(ApiError):
            model.write([{'source_id': 'S1'}], defaults())
        self.assertEqual(len(transport.payloads), 2)

    def test_degraded_draft_missing_review_notes_is_validation_error_not_crash(self):
        cfg = defaults(); cfg.update(writing_mode='single_web', research_degraded=True)
        bad = chat({'title': '标题', 'body': '正文[S1]', 'outline': []})
        good = chat({'title': '标题', 'body': '正文[S1]', 'outline': [], 'review_notes': []})
        model = Model('deepseek', defaults()['providers']['deepseek'], 'x',
                      SequenceTransport(bad, good))
        result = model.write([{'source_id': 'S1'}], cfg)
        self.assertTrue(any('未使用联网' in n for n in result['review_notes']))


class TransportRetryTest(unittest.TestCase):
    def response(self):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_dns_failure_is_retried(self):
        transport = Transport()
        transport.cancel.wait = Mock(return_value=False)
        error = urllib.error.URLError(socket.gaierror('dns'))
        with patch.object(transport.opener, 'open', side_effect=[error, self.response()]) as call:
            self.assertEqual(transport.request('https://example.com', 'k', {}), {'ok': True})
        self.assertEqual(call.call_count, 2)

    def test_read_timeout_is_not_retried(self):
        transport = Transport()
        with patch.object(transport.opener, 'open', side_effect=TimeoutError()) as call:
            with self.assertRaises(ApiError) as caught:
                transport.request('https://example.com', 'k', {})
        self.assertEqual(call.call_count, 1)
        self.assertEqual(caught.exception.kind, 'network')


class BatchIsolationTest(unittest.TestCase):
    def test_unexpected_crash_in_one_article_does_not_abort_batch(self):
        class CrashModel(IndependentModel):
            def write(self, sources, config):
                original = [s for s in sources if 'article_id' in s][0]
                if original['title'] == '文章0':
                    raise KeyError('unexpected')
                return super().write(sources, config)
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=2)
            IndependentModel.fail_title = None
            e = Engine(f.db, test_core.FakeVault(), source_factory=HotSource,
                       model_factory=CrashModel, research_factory=ResearchFake)
            self.assertEqual(e.run('crash:test', f.config), '部分完成')
            self.assertEqual(len(f.db.rows('SELECT * FROM drafts')), 1)
            failed = f.db.rows("SELECT message FROM jobs WHERE status='失败'")
            self.assertTrue(any('KeyError' in r['message'] for r in failed))
        finally:
            f.tearDown()

    def test_retrying_one_child_refreshes_parent_status(self):
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=2)
            IndependentModel.fail_title = '文章0'
            e = Engine(f.db, test_core.FakeVault(), source_factory=HotSource,
                       model_factory=IndependentModel, research_factory=ResearchFake)
            self.assertEqual(e.run('parent:test', f.config), '部分完成')
            failed = f.db.rows("SELECT run_key,config FROM jobs WHERE run_key LIKE 'batch:%' AND status='失败'")[0]
            IndependentModel.fail_title = None
            self.assertEqual(e.run(failed['run_key'], json.loads(failed['config'])), '已完成')
            parent = f.db.rows("SELECT status FROM jobs WHERE run_key='parent:test'")[0]
            self.assertEqual(parent['status'], '已完成')
        finally:
            IndependentModel.fail_title = None
            f.tearDown()


class WeReadRobustnessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def test_malformed_list_entry_is_skipped(self):
        session = Mock()
        session.shelf.return_value = [{'bookId': BOOK}]
        session.check.side_effect = lambda value: value
        session.request.side_effect = [
            {'reviews': [{'subReviews': [
                None, {'review': {'reviewId': 'OTHER_x', 'mpInfo': {'time': 1760000000}}},
                {'review': {'reviewId': BOOK + '_nodate', 'mpInfo': {}}},
                {'review': {'reviewId': BOOK + '_good', 'mpInfo': {'title': '好文章', 'time': 1760000000}}}]}]},
            {'reviews': []}]
        source = WeReadSource('x', self.db, Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False)),
                              session)
        items = source.articles(BOOK, 10)
        self.assertEqual([i['title'] for i in items], ['好文章'])

    def test_close_asks_edge_to_exit_through_cdp(self):
        browser = WeReadBrowser(Path(self.temp.name) / 'p')
        cdp_browser = Mock()
        browser.browser, browser.playwright = cdp_browser, Mock()
        process = Mock(); process.poll.return_value = 0; process.pid = None
        browser.process = process
        browser.close()
        cdp_browser.new_browser_cdp_session.return_value.send.assert_called_once_with('Browser.close')
        process.terminate.assert_not_called()

    def test_stale_profiles_are_swept_but_fresh_ones_kept(self):
        root = Path(self.temp.name) / 'weread-edge-profile'
        old = Path(self.temp.name) / 'weread-edge-profile-runtime-old'
        fresh = Path(self.temp.name) / 'weread-edge-profile-runtime-new'
        other = Path(self.temp.name) / 'article-images'
        for item in (old, fresh, other):
            item.mkdir()
            (item / 'f').write_text('x')
        stale = time.time() - 7200
        os.utime(old, (stale, stale))
        os.utime(other, (stale, stale))
        self.assertEqual(cleanup_stale_profiles(root), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())


if __name__ == '__main__':
    unittest.main()


class SelectionFilterTest(unittest.TestCase):
    def test_promotion_titles_are_not_filtered_by_title(self):
        class PromoSource(test_core.FakeSource):
            def articles(self, account, limit):
                when = (test_core.datetime.now(test_core.timezone.utc) - test_core.timedelta(hours=1)).isoformat()
                return [{'title': '双11爆品清单', 'url': 'https://mp.weixin.qq.com/s/promo', 'posted_at': when},
                        {'title': '政策解读', 'url': 'https://mp.weixin.qq.com/s/policy', 'posted_at': when}]
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(article_limit=2, min_sources=1)
            e = Engine(f.db, test_core.FakeVault(), source_factory=PromoSource, model_factory=test_core.FakeModel)
            e.run('promo:test', f.config)
            job = f.db.rows("SELECT selected FROM jobs WHERE run_key='promo:test'")[0]
            titles = [f.db.rows('SELECT title FROM articles WHERE id=?', (i,))[0]['title']
                      for i in json.loads(job['selected'])]
            self.assertEqual(titles, ['双11爆品清单', '政策解读'])
        finally:
            f.tearDown()

    def test_image_only_article_skipped_by_default_with_material_code(self):
        class ShortSource(test_core.FakeSource):
            def content(self, url):
                return '短文字'
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(article_limit=1, min_sources=1)
            e = Engine(f.db, test_core.FakeVault(), source_factory=ShortSource, model_factory=test_core.FakeModel)
            self.assertEqual(e.run('short:test', f.config), '失败')
            job = f.db.rows("SELECT error_code,message FROM jobs WHERE run_key='short:test'")[0]
            self.assertEqual(job['error_code'], 'material')
            self.assertIn('图片为主', job['message'])
        finally:
            f.tearDown()

    def test_missing_writer_key_fails_before_collection(self):
        class NoKeyVault:
            def get(self, name):
                if name == 'deepseek':
                    raise ValueError('尚未配置 deepseek 的 API Key')
                return 'k'
        f = test_core.CoreTest(); f.setUp()
        try:
            test_core.FakeSource.calls = 0
            e = Engine(f.db, NoKeyVault(), source_factory=test_core.FakeSource, model_factory=test_core.FakeModel)
            self.assertEqual(e.run('nokey:test', f.config), '失败')
            self.assertEqual(f.db.rows('SELECT COUNT(*) n FROM articles')[0]['n'], 0)
        finally:
            f.tearDown()


class WeReadRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.sqlite')
        self.cancel = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))

    def tearDown(self):
        self.temp.cleanup()

    def test_browser_reloads_once_on_expired_ticket(self):
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel, Mock(),
                              profile_dir=Path(self.temp.name) / 'profile')
        source.browser = Mock()
        source.browser.content.side_effect = [ApiError('-2041', True, kind='weread_ticket'), '<html>ok</html>']
        self.assertEqual(source._browser_call('content', 'rid'), '<html>ok</html>')
        source.browser.reload_worker.assert_called_once()
        source.close()

    def test_browser_ticket_failure_twice_is_raised(self):
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel, Mock(),
                              profile_dir=Path(self.temp.name) / 'profile')
        source.browser = Mock()
        source.browser.content.side_effect = ApiError('-2041', True, kind='weread_ticket')
        with self.assertRaises(ApiError):
            source._browser_call('content', 'rid')
        self.assertEqual(source.browser.content.call_count, 2)
        source.close()

    def test_session_renews_once_on_2041(self):
        from wewriter.weread import WeReadSession
        session = Mock()
        session.check.side_effect = [ApiError('-2041', True, kind='weread_ticket'), {'reviews': []}]
        session.request.side_effect = [{'errCode': -2041}, {'reviews': []}]
        saver = Mock()
        source = WeReadSource('x', self.db, self.cancel, session, state_saver=saver)
        self.assertEqual(source.request('/web/mp/articles'), {'reviews': []})
        session.renew.assert_called_once()
        saver.assert_called_once()

    def test_2041_code_is_marked_as_ticket(self):
        from wewriter.weread import WeReadSession
        with self.assertRaises(ApiError) as caught:
            WeReadSession.check({'errCode': -2041})
        self.assertEqual(caught.exception.kind, 'weread_ticket')
