import unittest
from wewriter.api import Model, EveryInfra, ApiError
from wewriter.config import defaults, migrate, validate
from test_core import FakeTransport
import test_core

class ProviderTests(unittest.TestCase):
    def test_legacy_migration_preserves_deepseek(self):
        old = defaults()
        old['providers'] = {'qwen': {'model':'old', 'base_url':'https://old.example'}, 'deepseek': {'model':'custom', 'base_url':'https://api.deepseek.com'}}
        old['summary_provider'] = 'qwen'
        old['search_url'] = 'https://dashscope.aliyuncs.com/old'
        old['skip_promotions'] = True
        old['promotion_keywords'] = ['双11']
        new = migrate(old)
        validate(new)
        self.assertEqual(new['summary_provider'], 'openai')
        self.assertNotIn('qwen', new['providers'])
        self.assertNotIn('skip_promotions', new)
        self.assertNotIn('promotion_keywords', new)
        self.assertEqual(new['providers']['deepseek']['model'], 'custom')
        self.assertEqual(old['summary_provider'], 'qwen')

    def test_openai_responses_and_incomplete_rejected(self):
        result = {'status':'completed', 'output':[{'type':'message', 'content':[{'type':'output_text','text':'{"ok":true}'}]}]}
        transport = FakeTransport(result)
        model = Model('openai', defaults()['providers']['openai'], 'test', transport)
        self.assertTrue(model.complete('JSON', 'test')['ok'])
        self.assertTrue(transport.payloads[0][0].endswith('/responses'))
        self.assertFalse(transport.payloads[0][1]['store'])
        self.assertIn('JSON', transport.payloads[0][1]['input'])
        result['status'] = 'incomplete'
        with self.assertRaises(ApiError): model.complete('JSON', 'test')

    def test_comments_empty_vs_invalid_and_anonymous(self):
        t = FakeTransport({'results': []})
        api = EveryInfra('test', t)
        self.assertEqual(api.comments('https://mp.weixin.qq.com/s/test'), [])
        t.response = {'results':[{'text':'读者疑问', 'author_name':'private', 'like_count':3}]}
        result = api.comments('https://mp.weixin.qq.com/s/test')
        self.assertNotIn('author_name', result[0])
        self.assertEqual(result[0]['id'], 'C1')
        self.assertEqual(t.payloads[-1][1]['action'], 'article_comments')
        t.response = {}
        with self.assertRaises(ApiError): api.comments('https://mp.weixin.qq.com/s/test')

    def test_comment_evidence_must_exist(self):
        model = test_core.AdapterTest().model({'insights':[{'point':'疑问', 'comment_ids':['C9']}]})
        with self.assertRaises(ApiError): model.analyze_comments([{'id':'C1','text':'问题'}])

    def test_withdrawn_comment_analysis_is_never_called_even_for_old_snapshots(self):
        # T-010: an old task snapshot (retry, schedule) with comments_enabled=True
        # must neither fetch EveryInfra comments nor call the comment model.
        from unittest.mock import patch
        from test_independent import IndependentModel, ResearchFake
        from test_hotspots import HotSource
        from wewriter.engine import Engine
        fixture = test_core.CoreTest(); fixture.setUp()
        class CommentModel(IndependentModel):
            comment_calls = 0
            fail_title = None
            def analyze_comments(self, comments):
                CommentModel.comment_calls += 1
                return {'insights': [{'point': '读者疑问', 'comment_ids': ['C1']}]}
        try:
            fixture.config.update(writing_mode='separate_web', draft_count=2, comments_enabled=True)
            engine = Engine(fixture.db, test_core.FakeVault(), source_factory=HotSource,
                            model_factory=CommentModel, research_factory=ResearchFake)
            with patch('wewriter.engine.EveryInfra.comments', return_value=[{'id': 'C1', 'text': '问题'}]) as fetch:
                status = engine.run('comments-test', fixture.config)
                message = ' | '.join(r['message'] for r in fixture.db.rows("SELECT message FROM jobs"))
                self.assertEqual(status, '已完成', message)
                fetch.assert_not_called()
            self.assertEqual(CommentModel.comment_calls, 0)
            self.assertFalse(migrate(dict(fixture.config))['comments_enabled'])
            drafts = fixture.db.rows('SELECT report FROM drafts')
            self.assertTrue(drafts)
            self.assertFalse(any('读者疑问' in d['report'] for d in drafts))
        finally:
            fixture.tearDown()

    def test_everyinfra_article_source_migrates_to_weread(self):
        old = defaults()
        old['source_provider'] = 'everyinfra'
        self.assertEqual(migrate(old)['source_provider'], 'weread')
        with self.assertRaises(ValueError): validate(old)
