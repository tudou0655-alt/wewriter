"""T-020: a batch article that failed on a timeout is replaced by the next hottest one (offline)."""
import json
import unittest

import test_core
from test_hotspots import HotModel
from test_independent import ResearchFake
from wewriter.api import ApiError
from wewriter.engine import Engine


class ManySource(test_core.FakeSource):
    def articles(self, account, limit):
        when = (test_core.datetime.now(test_core.timezone.utc) - test_core.timedelta(hours=1)).isoformat()
        return [{'title': f'文章{i}', 'url': f'https://mp.weixin.qq.com/s/many{i}', 'posted_at': when,
                 'read_count': (6 - i) * 1000, 'like_count': 0} for i in range(6)]


class OneSource(ManySource):
    def articles(self, account, limit):
        return super().articles(account, limit)[:1]


class FailingWriter(HotModel):
    failures = {}
    calls = []

    def write(self, sources, config):
        title = next(s['title'] for s in sources if 'article_id' in s)
        type(self).calls.append(title)
        if title in self.failures:
            raise self.failures[title]()
        return {'title': title + '稿', 'body': '内容。[' + sources[0]['source_id'] + '][W1]',
                'outline': [], 'review_notes': []}


def timeout():
    return ApiError('网络连接失败或超时（请求超时）', kind='network')


def bad_key():
    return ApiError('HTTP 401：API Key 无效', True)


class BatchReplacementTest(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreTest()
        self.f.setUp()
        FailingWriter.calls = []

    def tearDown(self):
        FailingWriter.failures = {}
        self.f.tearDown()

    def run_batch(self, count, failures):
        FailingWriter.failures = failures
        self.f.config.update(writing_mode='separate_web', draft_count=count)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=ManySource,
                        model_factory=FailingWriter, research_factory=ResearchFake)
        status = engine.run('batch-replace', self.f.config)
        return status, self.f.db.rows("SELECT * FROM jobs WHERE run_key='batch-replace'")[0]

    def test_timeout_is_replaced_and_explained(self):
        status, job = self.run_batch(2, {'文章0': timeout})
        self.assertEqual(status, '已完成')
        self.assertEqual(len(self.f.db.rows('SELECT * FROM drafts')), 2)
        self.assertIn('热度第 1 篇因超时或网络中断失败，已由热度第 3 篇补位', job['message'])
        child = self.f.db.rows("SELECT error_code FROM jobs WHERE run_key LIKE 'batch:%' AND status='失败'")
        self.assertEqual([c['error_code'] for c in child], ['network'])

    def test_short_queue_stops_before_research_and_writing(self):
        self.f.config.update(writing_mode='separate_web', draft_count=2)
        research_calls = []

        def research_factory(*args, **kwargs):
            research_calls.append(1)
            return ResearchFake(*args, **kwargs)

        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=OneSource,
                        model_factory=FailingWriter, research_factory=research_factory)
        self.assertEqual(engine.run('batch-too-short', self.f.config), '失败')
        job = self.f.db.rows("SELECT message,error_code FROM jobs WHERE run_key='batch-too-short'")[0]
        self.assertIn('目标 2 篇，只有 1 篇', job['message'])
        self.assertIn('未启动联网研究或模型写作', job['message'])
        self.assertIn('采集费用', job['message'])
        self.assertEqual(research_calls, [])
        self.assertEqual(FailingWriter.calls, [])
        self.assertEqual(self.f.db.rows('SELECT * FROM batch_items'), [])
        self.assertEqual(self.f.db.rows('SELECT * FROM drafts'), [])

    def test_one_candidate_can_fill_one_requested_draft(self):
        self.f.config.update(writing_mode='separate_web', draft_count=1)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=OneSource,
                        model_factory=FailingWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('batch-one', self.f.config), '已完成')
        self.assertEqual(len(self.f.db.rows('SELECT * FROM drafts')), 1)

    def test_key_error_is_not_replaced(self):
        status, job = self.run_batch(2, {'文章0': bad_key})
        self.assertEqual(status, '部分完成')
        self.assertNotIn('补位', job['message'])
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1'])
        self.assertEqual(len(self.f.db.rows('SELECT * FROM batch_items')), 2)

    def test_at_most_two_timeout_replacements_per_batch(self):
        status, job = self.run_batch(3, {'文章0': timeout, '文章1': timeout, '文章2': timeout})
        self.assertEqual(status, '部分完成')
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1', '文章2', '文章3', '文章4'])
        self.assertIn('热度第 1 篇因超时或网络中断失败，已由热度第 4 篇补位', job['message'])
        self.assertIn('热度第 2 篇因超时或网络中断失败，已由热度第 5 篇补位', job['message'])
        self.assertEqual(job['message'].count('补位'), 2)

    def test_failing_replacements_count_toward_the_limit(self):
        status, _ = self.run_batch(1, {f'文章{i}': timeout for i in range(6)})
        self.assertEqual(status, '失败')
        # One primary plus two replacements; the third timeout is not replaced.
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1', '文章2'])

    def test_material_and_timeout_replacements_together(self):
        class GappySource(ManySource):
            def content(self, url):
                if url.endswith('many0'):
                    raise ApiError('微信读书没有返回正文，可能要求验证或文章已删除')
                return super().content(url)
        FailingWriter.failures = {'文章1': timeout}
        self.f.config.update(writing_mode='separate_web', draft_count=2)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=GappySource,
                        model_factory=FailingWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('batch-mixed', self.f.config), '已完成')
        message = self.f.db.rows("SELECT message FROM jobs WHERE run_key='batch-mixed'")[0]['message']
        self.assertIn('热度第 1 篇素材提取失败，已由热度第 3 篇补位', message)
        self.assertIn('热度第 2 篇因超时或网络中断失败，已由热度第 4 篇补位', message)
        warnings = json.loads(self.f.db.rows("SELECT warnings FROM jobs WHERE run_key='batch-mixed'")[0]['warnings'])
        self.assertTrue(any(w.startswith('未完成：热度第 2 篇') for w in warnings))


if __name__ == '__main__':
    unittest.main()
