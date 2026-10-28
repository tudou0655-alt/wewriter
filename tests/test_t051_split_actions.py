"""T-051: collecting and writing are separate, reusable offline actions."""
import json
import unittest

import test_core
from test_independent import IndependentModel, ResearchFake
from wewriter.engine import Engine
from wewriter.pipeline.collect import saved_candidates


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


if __name__ == '__main__':
    unittest.main()
