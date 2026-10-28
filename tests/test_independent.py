import json
import unittest
import test_core
from test_hotspots import HotSource, HotModel
from wewriter.engine import Engine
from wewriter.research import WebResearch
from wewriter.api import ApiError, Model
from wewriter.config import defaults


class ResearchFake:
    calls = 0
    def __init__(self, *args):
        pass
    def search(self, article):
        type(self).calls += 1
        return {'notes': '联网事实[W1]', 'retrieved_at': '2026-10-10', 'sources': [
            {'source_id': 'W1', 'title': '来源', 'url': 'https://example.org/research', 'kind': 'web'}]}


class IndependentModel(HotModel):
    fail_title = None
    calls = []
    def write(self, sources, config):
        original = [s for s in sources if 'article_id' in s]
        assert len(original) == 1
        assert any(s['source_id'].startswith('W') for s in sources)
        title = original[0]['title']
        type(self).calls.append(title)
        if title == self.fail_title:
            raise ApiError('test write failure')
        return {'title': title + '独立稿', 'body': '内容[' + original[0]['source_id'] + '][W1]',
                'outline': [], 'review_notes': []}


class IndependentTest(unittest.TestCase):
    def test_batch_reuses_one_source_session_for_all_child_articles(self):
        class CountingSource(HotSource):
            instances = 0
            closes = 0
            def __init__(self, *args):
                type(self).instances += 1
                super().__init__(*args)
            def close(self):
                type(self).closes += 1
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=2)
            e = Engine(f.db, test_core.FakeVault(), source_factory=CountingSource,
                       model_factory=IndependentModel, research_factory=ResearchFake)
            self.assertEqual(e.run('shared-source:test', f.config), '已完成')
            self.assertEqual(CountingSource.instances, 1)
            self.assertEqual(CountingSource.closes, 1)
        finally:
            f.tearDown()

    def test_material_failure_uses_next_hottest_article_as_replacement(self):
        class FourHotSource(HotSource):
            def articles(self, account, limit):
                when = test_core.datetime.now(test_core.timezone.utc) - test_core.timedelta(hours=1)
                return [{'title': f'文章{i}', 'url': f'https://mp.weixin.qq.com/s/replacement{i}',
                         'posted_at': when.isoformat(), 'read_count': (i + 1) * 1000,
                         'like_count': i} for i in range(4)]
            def content(self, url):
                if url.endswith('replacement3'):
                    raise ApiError('微信读书没有返回正文，可能要求验证或文章已删除')
                return '这是一篇能够正常提取和分析的公众号正文。' * 20
        class LegacyQueueEngine(Engine):
            def run_separate(self, job, config, selected, source=None):
                # Simulate a task saved by v0.9.6, which kept only the requested
                # three IDs even though metrics for the fourth were captured.
                self.db.update_job(job['id'], selected=json.dumps(selected[:3]))
                return super().run_separate(job, config, selected[:3], source)
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=3)
            IndependentModel.calls = []; IndependentModel.fail_title = None
            e = LegacyQueueEngine(f.db, test_core.FakeVault(), source_factory=FourHotSource,
                                  model_factory=IndependentModel, research_factory=ResearchFake)
            self.assertEqual(e.run('replacement:test', f.config), '已完成')
            self.assertEqual(len(f.db.rows('SELECT * FROM drafts')), 3)
            parent = f.db.rows("SELECT * FROM jobs WHERE run_key='replacement:test'")[0]
            self.assertEqual(len(json.loads(parent['selected'])), 4)
            self.assertIn('热度第 4 篇补位', parent['message'])
            self.assertEqual(len(f.db.rows('SELECT * FROM batch_items')), 4)
        finally:
            IndependentModel.fail_title = None
            f.tearDown()

    def test_partial_retry_keeps_done_draft_and_search_cache(self):
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=2)
            IndependentModel.calls = []; ResearchFake.calls = 0
            IndependentModel.fail_title = '文章0'
            e = Engine(f.db, test_core.FakeVault(), source_factory=HotSource,
                       model_factory=IndependentModel, research_factory=ResearchFake)
            self.assertEqual(e.run('batch:test', f.config), '部分完成')
            self.assertEqual(len(f.db.rows('SELECT * FROM drafts')), 1)
            IndependentModel.fail_title = None
            self.assertEqual(e.run('batch:test', f.config), '已完成')
            self.assertEqual(len(f.db.rows('SELECT * FROM drafts')), 2)
            self.assertEqual(IndependentModel.calls.count('文章1'), 1)
            self.assertEqual(ResearchFake.calls, 2)
            self.assertEqual(len(f.db.rows('SELECT * FROM batch_items')), 2)
        finally:
            IndependentModel.fail_title = None
            f.tearDown()

    def test_no_search_sources_never_claims_online_research(self):
        t = test_core.FakeTransport({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'memory'}]}]})
        with self.assertRaises(ApiError):
            WebResearch('test', defaults(), t).search({'title':'test','analysis':[]})
        self.assertEqual(t.payloads[0][1]['tool_choice'], 'required')

    def test_provider_sources_used_and_invalid_citations_rejected(self):
        citation = {'type': 'url_citation', 'start_index': 2, 'end_index': 5, 'url': 'https://example.org/fact', 'title': '机构'}
        result = {'status': 'completed', 'output': [{'type': 'web_search_call', 'status': 'completed'},
            {'type':'message', 'content':[{'type':'output_text', 'text':'事实引用一', 'annotations':[citation]}]}]}
        t = test_core.FakeTransport(result)
        out = WebResearch('test', defaults(), t).search({'title':'test','analysis':[]})
        self.assertEqual(out['notes'], '事实[W1]')
        self.assertEqual(out['sources'][0]['source_id'], 'W1')
        self.assertEqual(t.timeouts[-1], 300)
        citation['end_index'] = 99
        with self.assertRaises(ApiError):
            WebResearch('test', defaults(), t).search({'title':'test','analysis':[]})

    def test_writer_requires_actual_web_citation(self):
        m = test_core.AdapterTest().model({'title':'标题','body':'正文[S1]','outline':[],'review_notes':[]})
        cfg = defaults();cfg['writing_mode'] = 'single_web';cfg['draft_first_mode'] = False
        with self.assertRaises(ApiError):
            m.write([{'source_id':'S1'},{'source_id':'W1'}],cfg)

    def test_network_failure_degrades_to_original_only(self):
        class NetworkResearch:
            def __init__(self, *args): pass
            def search(self, article):
                raise ApiError('timeout', kind='network')
        class DegradedModel(HotModel):
            def write(self, sources, config):
                self.assert_degraded = config.get('research_degraded')
                assert self.assert_degraded
                assert not any(s['source_id'].startswith('W') for s in sources)
                return {'title': sources[0]['title'] + '降级稿',
                        'body': '仅基于原文[' + sources[0]['source_id'] + ']',
                        'outline': [], 'review_notes': ['未使用联网资料']}
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=2)
            e = Engine(f.db, test_core.FakeVault(), source_factory=HotSource,
                       model_factory=DegradedModel, research_factory=NetworkResearch)
            self.assertEqual(e.run('degraded:test', f.config), '已完成')
            drafts = f.db.rows('SELECT report,sources FROM drafts')
            self.assertEqual(len(drafts), 2)
            self.assertTrue(all('仅基于公众号原文' in d['report'] for d in drafts))
            self.assertTrue(all(not any(s['source_id'].startswith('W') for s in json.loads(d['sources'])) for d in drafts))
        finally:
            f.tearDown()

    def test_non_network_search_error_does_not_degrade(self):
        class BadResearch:
            def __init__(self, *args): pass
            def search(self, article): raise ApiError('API Key 无效', fatal=True)
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=2)
            e = Engine(f.db, test_core.FakeVault(), source_factory=HotSource,
                       model_factory=IndependentModel, research_factory=BadResearch)
            self.assertEqual(e.run('no-degrade:test', f.config), '失败')
            self.assertFalse(f.db.rows('SELECT * FROM drafts'))
        finally:
            f.tearDown()
