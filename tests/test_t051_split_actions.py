"""T-051: collecting and writing are separate, reusable offline actions."""
import json
import unittest

import test_core
from test_independent import IndependentModel, ResearchFake
from wewriter.engine import Engine
from wewriter.pipeline.collect import saved_candidates
from wewriter.webapp import Api, Backend


class MeasuredSource(test_core.FakeSource):
    list_calls = 0
    body_calls = 0

    def articles(self, account, limit):
        type(self).list_calls += 1
        when = (test_core.datetime.now(test_core.timezone.utc) - test_core.timedelta(hours=1)).isoformat()
        return [{'title': f'文章{i}', 'url': f'https://mp.weixin.qq.com/s/split{i}',
                 'posted_at': when, 'read_count': 2000 - i * 500, 'like_count': 0}
                for i in range(2)]

    def content(self, url):
        type(self).body_calls += 1
        return super().content(url)


class SplitActionsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_core.CoreTest()
        self.fixture.setUp()
        MeasuredSource.list_calls = MeasuredSource.body_calls = 0
        IndependentModel.calls = []
        self.engine = Engine(self.fixture.db, test_core.FakeVault(), source_factory=MeasuredSource,
                             model_factory=IndependentModel, research_factory=ResearchFake)

    def tearDown(self):
        self.fixture.tearDown()

    def collect(self):
        config = dict(self.fixture.config)
        config.update(task_action='collect', selection_mode='recent', writing_mode='separate_web')
        return self.engine.run('collect:1', config)

    def write(self, key):
        config = dict(self.fixture.config)
        config.update(task_action='write', selection_mode='hot', writing_mode='separate_web',
                      draft_count=1, min_sources=1)
        return self.engine.run(key, config)

    def test_collect_saves_metadata_without_models_or_body_fetch(self):
        self.assertEqual(self.collect(), '已完成')
        self.assertEqual(MeasuredSource.list_calls, 1)
        self.assertEqual(MeasuredSource.body_calls, 0)
        self.assertEqual(test_core.FakeModel.summaries, 0)
        self.assertEqual(self.fixture.db.rows('SELECT * FROM drafts'), [])
        self.assertEqual(len(saved_candidates(self.fixture.db, {'selection_mode': 'hot', 'min_reads': 0})), 2)
        job = self.fixture.db.rows("SELECT * FROM jobs WHERE run_key='collect:1'")[0]
        self.assertEqual(json.loads(job['selected']), [1, 2])
        self.assertIn('已采集并保存 2 篇', job['message'])

    def test_two_writing_tasks_reuse_saved_pool_without_listing_again(self):
        self.collect()
        self.assertEqual(self.write('write:1'), '已完成')
        self.assertEqual(self.write('write:2'), '已完成')
        self.assertEqual(MeasuredSource.list_calls, 1)
        self.assertEqual(MeasuredSource.body_calls, 2)
        self.assertEqual(len(self.fixture.db.rows('SELECT * FROM drafts')), 2)
        self.assertEqual(len(set(IndependentModel.calls)), 2)
        self.assertEqual(saved_candidates(self.fixture.db, {'selection_mode': 'hot', 'min_reads': 0}), [])

    def test_empty_library_does_not_list_accounts_or_call_models(self):
        self.assertEqual(self.write('write:empty'), '无新文章')
        self.assertEqual(MeasuredSource.list_calls, 0)
        self.assertEqual(MeasuredSource.body_calls, 0)
        self.assertIn('请先采集素材', self.fixture.db.rows(
            "SELECT message FROM jobs WHERE run_key='write:empty'")[0]['message'])

    def test_unknown_read_count_is_usable_only_without_minimum(self):
        self.fixture.db.save_article({
            'source_key': 'old-without-metrics', 'account_name': '测试公众号', 'title': '旧素材',
            'url': 'https://mp.weixin.qq.com/s/old',
            'published_at': (test_core.datetime.now(test_core.timezone.utc) -
                             test_core.timedelta(days=10)).isoformat()})
        self.assertEqual(len(saved_candidates(self.fixture.db,
                         {'selection_mode': 'hot', 'min_reads': 0})), 1)
        self.assertEqual(saved_candidates(self.fixture.db,
                         {'selection_mode': 'hot', 'min_reads': 100}), [])

    def test_manual_generation_queues_fresh_collection_even_with_empty_library(self):
        backend = Backend(self.fixture.db, test_core.FakeVault(), self.fixture.path)
        backend.busy = True  # Queue the task without starting a worker or any external request.
        result = Api(backend).wb_write(2)
        self.assertTrue(result['ok'])
        self.assertTrue(result['queued'])
        job = self.fixture.db.rows("SELECT config FROM jobs WHERE stage='排队中'")[0]
        self.assertEqual(json.loads(job['config'])['draft_count'], 2)
        self.assertNotIn('task_action', json.loads(job['config']))

        queued = self.fixture.db.rows("SELECT run_key,config FROM jobs WHERE stage='排队中'")[0]
        self.assertEqual(self.engine.run(queued['run_key'], json.loads(queued['config'])), '已完成')
        self.assertEqual(MeasuredSource.list_calls, 1)
        self.assertEqual(len(self.fixture.db.rows('SELECT id FROM drafts')), 2)

    def test_historical_task_shows_its_own_heat_snapshot(self):
        self.collect()
        job = self.fixture.db.rows("SELECT id FROM jobs WHERE run_key='collect:1'")[0]
        first = self.fixture.db.rows('SELECT article_id,reads FROM job_metrics WHERE job_id=? ORDER BY article_id',
                                     (job['id'],))[0]
        self.fixture.db.execute('UPDATE article_metrics SET reads=99999 WHERE article_id=?',
                                (first['article_id'],))
        detail = Api(Backend(self.fixture.db, test_core.FakeVault(), self.fixture.path)).wb_detail(job['id'])
        self.assertTrue(detail['ok'])
        self.assertEqual(detail['picked'][0]['reads'], first['reads'])
        self.assertIn('中国时间', detail['picked'][0]['observed_at'])


if __name__ == '__main__':
    unittest.main()
