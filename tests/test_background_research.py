"""T-019: web research runs in OpenAI background mode and is fetched by polling (offline)."""
import json
import threading
import unittest

import test_core
from test_hotspots import HotSource, HotModel
from wewriter.api import ApiError, Cancelled
from wewriter.config import defaults
from wewriter.engine import Engine
from wewriter.research import WebResearch

URL = defaults()['search_url']
ARTICLE = {'title': '原文', 'analysis': []}
CITATION = {'type': 'url_citation', 'start_index': 0, 'end_index': 4, 'url': 'https://example.org/notice', 'title': '通知'}
DONE = {'id': 'resp_1', 'status': 'completed', 'output': [
    {'type': 'web_search_call', 'status': 'completed'},
    {'type': 'message', 'content': [{'type': 'output_text', 'text': '官方通知已发布', 'annotations': [CITATION]}]}]}


class Clock:
    def __init__(self, step=0):
        self.now, self.step = 0, step

    def __call__(self):
        self.now += self.step
        return self.now


class ScriptedTransport:
    """Answers each request from a script: (expected url suffix, reply or exception)."""

    def __init__(self, *script, cancel=None):
        self.script = list(script)
        self.calls = []
        self.cancel = cancel or threading.Event()

    def request(self, url, key, payload=None, *, timeout=100):
        self.calls.append((url, payload))
        if self.cancel.is_set():
            raise Cancelled('任务已停止')
        if not self.script:
            return {'id': 'resp_1', 'status': 'in_progress'}
        reply = self.script.pop(0)
        if callable(reply):
            reply = reply()
        if isinstance(reply, BaseException):
            raise reply
        return reply


def research(transport, clock=None):
    searcher = WebResearch('test-only', defaults(), transport, clock=clock or Clock())
    searcher.poll_interval = 0
    return searcher


class PollingTest(unittest.TestCase):
    def test_created_id_is_stored_then_polled_until_completed(self):
        created = []
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'}, {'id': 'resp_1', 'status': 'in_progress'}, DONE)
        out = research(t).search(ARTICLE, on_created=created.append)
        self.assertEqual(created, ['resp_1'])
        self.assertEqual(out['notes'], '[W1]已发布')
        first = t.calls[0][1]
        self.assertTrue(first['background'])
        self.assertTrue(first['store'])
        self.assertEqual([c[0] for c in t.calls[1:]], [URL + '/resp_1'] * 2)
        self.assertEqual([c[1] for c in t.calls[1:]], [None, None])

    def test_network_error_while_polling_keeps_polling(self):
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'},
                              ApiError('网络连接失败', kind='network'), DONE)
        self.assertEqual(research(t).search(ARTICLE)['sources'][0]['url'], 'https://example.org/notice')
        self.assertEqual(sum(1 for url, payload in t.calls if url == URL), 1)  # never created twice

    def test_rate_limited_poll_is_repeated(self):
        busy = ApiError('HTTP 503')
        busy.retry_delay = 2
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'}, busy, DONE)
        self.assertTrue(research(t).search(ARTICLE)['notes'])

    def test_key_error_while_polling_stops(self):
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'}, ApiError('HTTP 401：API Key 无效', True))
        with self.assertRaises(ApiError) as caught:
            research(t).search(ARTICLE)
        self.assertTrue(caught.exception.fatal)

    def test_wait_limit_cancels_and_reports_a_network_timeout(self):
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'})
        with self.assertRaises(ApiError) as caught:
            research(t, Clock(step=200)).search(ARTICLE)
        self.assertEqual(caught.exception.kind, 'network')
        self.assertIn('10 分钟', str(caught.exception))
        self.assertEqual(t.calls[-1], (URL + '/resp_1/cancel', {}))

    def test_failed_search_is_an_error_not_a_degradation(self):
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'}, {'id': 'resp_1', 'status': 'failed', 'output': []})
        with self.assertRaises(ApiError) as caught:
            research(t).search(ARTICLE)
        self.assertNotEqual(caught.exception.kind, 'network')

    def test_resume_fetches_the_stored_id_without_a_new_search(self):
        t = ScriptedTransport(DONE)
        self.assertTrue(research(t).search(ARTICLE, response_id='resp_1')['notes'])
        self.assertEqual(t.calls, [(URL + '/resp_1', None)])

    def test_stop_task_interrupts_the_wait(self):
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'})
        t.script.append(lambda: (t.cancel.set(), {'id': 'resp_1', 'status': 'in_progress'})[1])
        with self.assertRaises(Cancelled):
            research(t).search(ARTICLE)

    def test_invalid_id_is_rejected(self):
        t = ScriptedTransport({'id': '../../files', 'status': 'queued'})
        with self.assertRaisesRegex(ApiError, '任务编号'):
            research(t).search(ARTICLE)


class MigrationTest(unittest.TestCase):
    def test_v4_database_gains_pending_table_idempotently(self):
        import sqlite3
        from contextlib import closing
        from wewriter.db import SCHEMA_VERSION, Database
        f = test_core.CoreTest()
        f.setUp()
        try:
            path = f.path / 'v4.sqlite'
            Database(path)
            with closing(sqlite3.connect(path)) as raw:
                raw.execute('DROP TABLE research_pending')
                raw.execute('PRAGMA user_version=4')
                raw.commit()
            db = Database(path)
            db.execute("INSERT INTO research_pending VALUES(1,'resp_1','t')")
            Database(path)
            self.assertEqual(SCHEMA_VERSION, 6)
            self.assertEqual(len(db.rows('SELECT * FROM research_pending')), 1)
        finally:
            f.tearDown()


class Writer(HotModel):
    def write(self, sources, config):
        marks = ''.join(f"[{s['source_id']}]" for s in sources)
        return {'title': sources[0]['title'] + '稿', 'body': '内容。' + marks, 'outline': [], 'review_notes': []}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreTest()
        self.f.setUp()
        self.f.config.update(writing_mode='single_web', selection_mode='hot', min_sources=1, article_limit=1)

    def tearDown(self):
        self.f.tearDown()

    def engine(self, transport, clock=None):
        def factory(key, config, _real_transport):
            searcher = WebResearch(key, config, transport, clock=clock or Clock())
            searcher.poll_interval = 0
            return searcher
        e = Engine(self.f.db, test_core.FakeVault(), source_factory=HotSource, model_factory=Writer,
                   research_factory=factory)
        transport.cancel = e.cancel
        return e

    def test_retry_fetches_the_pending_search_instead_of_starting_another(self):
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'})
        e = self.engine(t)
        # Stop the task while the search is still running on the server.
        t.script.append(lambda: (e.stop(), {'id': 'resp_1', 'status': 'in_progress'})[1])
        self.assertEqual(e.run('bg:1', self.f.config), '已停止')
        self.assertEqual(self.f.db.rows('SELECT response_id FROM research_pending'), [{'response_id': 'resp_1'}])
        t.calls.clear()
        t.script = [DONE]
        self.assertEqual(e.run('bg:1', self.f.config), '已完成')
        self.assertEqual(t.calls, [(URL + '/resp_1', None)])
        self.assertFalse(self.f.db.rows('SELECT * FROM research_pending'))
        self.assertTrue(self.f.db.rows('SELECT * FROM research_cache'))
        sources = json.loads(self.f.db.rows('SELECT sources FROM drafts')[0]['sources'])
        self.assertTrue(any(s['source_id'] == 'W1' for s in sources))

    def test_wait_limit_degrades_to_original_only(self):
        t = ScriptedTransport({'id': 'resp_1', 'status': 'queued'})
        e = self.engine(t, Clock(step=200))
        self.assertEqual(e.run('bg:2', self.f.config), '部分完成')
        self.assertIn((URL + '/resp_1/cancel', {}), t.calls)
        self.assertFalse(self.f.db.rows('SELECT * FROM research_pending'))
        draft = self.f.db.rows('SELECT report FROM drafts')[0]
        self.assertIn('## 联网研究状态', draft['report'])


if __name__ == '__main__':
    unittest.main()
