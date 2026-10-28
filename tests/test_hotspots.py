import unittest
from wewriter.hotspots import count, rank
from wewriter.config import defaults, validate
import test_core
from test_core import FakeSource, FakeModel, FakeVault
from wewriter.engine import Engine
import json


class RankingTest(unittest.TestCase):
    def test_missing_is_not_zero_and_threshold_is_applied(self):
        c = {1: {'reads': None, 'likes': 999, 'published': 4},
             2: {'reads': 500, 'likes': 0, 'published': 3},
             3: {'reads': 1000, 'likes': 0, 'published': 1},
             4: {'reads': 1000, 'likes': 5, 'published': 2}}
        self.assertEqual(rank(c, 'hot', 600), [4, 3])
        self.assertEqual(rank(c, 'recent'), [1, 2, 4, 3])
        for v in [None, -1, True, '10万+', 1.5]:
            self.assertIsNone(count(v))
        self.assertEqual(count('1000'), 1000)


class HotSource(FakeSource):
    def articles(self, account, limit):
        items = super().articles(account, limit)
        for i, item in enumerate(items):
            item.update(read_count=(i + 1) * 1000, like_count=i)
        return items


class HotModel(FakeModel):
    def analyze_style(self, content, title=''):
        return {'tone': '客观', 'structure': '问题—证据—建议'}


class HotPipelineTest(unittest.TestCase):
    def test_ranking_and_metrics_persist_without_extra_style_call(self):
        fixture = test_core.CoreTest()
        fixture.setUp()
        try:
            fixture.config['selection_mode'] = 'hot'
            engine = Engine(fixture.db, FakeVault(), source_factory=HotSource, model_factory=HotModel)
            self.assertEqual(engine.run('hot:test', fixture.config), '已完成')
            draft = fixture.db.rows('SELECT * FROM drafts')[0]
            sources = json.loads(draft['sources'])
            self.assertEqual(sources[0]['metrics']['reads'], 2000)
            self.assertNotIn('style_reference', sources[0])
            self.assertNotIn('风格参考', draft['report'])
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
