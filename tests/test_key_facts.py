"""T-024: key identifiers are checked in the search before writing; the post-review search is a fallback (offline)."""
import json
import unittest

import test_core
from test_hotspots import HotSource
from test_verify_items import FOUND, Research, VerifyFixture, Writer
from wewriter.api import fact_identifiers, identifier_status, key_facts
from wewriter.config import defaults
from wewriter.engine import Engine
from wewriter.research import WebResearch

CLAIM = '项目预算2200万元，最高限价2090万元，成交供应商为马鞍山市城市规划设计研究院，见自然资发〔2025〕226号'


class ExtractionTest(unittest.TestCase):
    def test_identifiers_by_rule(self):
        self.assertEqual(fact_identifiers('原始材料还称成交金额为392万元、成交供应商为贵州省环境科学研究设计院'),
                         ['392万元', '贵州省环境科学研究设计院'])
        self.assertEqual(fact_identifiers('自然资源部、住房城乡建设部2025年12月31日印发的自然资发〔2025〕226号'),
                         ['自然资源部', '住房城乡建设部', '2025年12月31日', '自然资发〔2025〕226号'])
        self.assertEqual(fact_identifiers('项目编号：AHZB-2026-0123，由马鞍山市公共资源交易中心发布'),
                         ['项目编号：AHZB-2026-0123', '马鞍山市公共资源交易中心'])
        self.assertEqual(fact_identifiers('遵义市生态环境局拟采购方案编制服务'), ['遵义市生态环境局'])
        self.assertEqual(fact_identifiers('这一布局影响全局，读者普遍关心'), [])
        # Shapes seen in the 2026-10-26 replay of drafts 11 and 12.
        self.assertEqual(fact_identifiers('原文称采购人为遵义市生态环境局。'), ['遵义市生态环境局'])
        self.assertEqual(fact_identifiers('原文事实：采购人为马鞍山市住房和城乡建设局。'), ['马鞍山市住房和城乡建设局'])
        self.assertEqual(fact_identifiers('原文称：中规院（北京）规划设计有限公司以1986万元中标。'),
                         ['中规院（北京）规划设计有限公司', '1986万元'])

    def test_key_facts_come_from_summary_facts_without_repeats(self):
        analysis = [{'summary': '摘要提到2200万元', 'facts': [
            {'claim': CLAIM, 'quote': '预算2200万元'}, {'claim': '没有标识的说法', 'quote': '普通句子'}],
            'uncertainties': []}]
        facts = key_facts(analysis)
        self.assertEqual(facts[0]['claim'], CLAIM)
        self.assertEqual(facts[0]['identifiers'], ['2200万元', '2090万元', '马鞍山市城市规划设计研究院',
                                                   '自然资发〔2025〕226号'])
        self.assertEqual(len(facts), 1)          # the quote and summary repeat 2200万元
        self.assertEqual(key_facts(None), [])

    def test_identifier_status(self):
        notes = '中标公告显示预算2200万元[W1]。未找到官方出处：2090万元。'
        self.assertEqual(identifier_status('2200万元', notes), 'found')
        self.assertEqual(identifier_status('2090万元', notes), 'not_found')
        self.assertIsNone(identifier_status('1986万元', notes))


class SearchPromptTest(unittest.TestCase):
    def test_key_facts_go_into_the_first_search(self):
        done = {'status': 'completed', 'output': [{'type': 'web_search_call', 'status': 'completed'},
                {'type': 'message', 'content': [{'type': 'output_text', 'text': '公告原文', 'annotations': [
                    {'type': 'url_citation', 'start_index': 0, 'end_index': 4, 'url': 'https://ccgp.example.gov.cn/a'}]}]}]}
        facts = [{'claim': CLAIM, 'identifiers': ['2200万元']}]
        transport = test_core.FakeTransport(done)
        WebResearch('k', defaults(), transport).search({'title': '标题', 'analysis': [], 'key_facts': facts})
        payload = transport.payloads[0][1]
        self.assertEqual(json.loads(payload['input'])['key_facts'], facts)
        self.assertIn('优先检索这些标识的官方原件', payload['instructions'])
        transport = test_core.FakeTransport(done)
        WebResearch('k', defaults(), transport).search({'title': '标题', 'analysis': []})
        self.assertNotIn('key_facts', json.loads(transport.payloads[0][1]['input']))
        self.assertNotIn('优先检索这些标识', transport.payloads[0][1]['instructions'])


class FactWriter(Writer):
    def summary(self, article, content):
        return {'summary': '项目采购', 'facts': [{'claim': CLAIM, 'quote': '文中明确指出资料应当保留来源。'}],
                'uncertainties': []}


class CoveringResearch(Research):
    notes = '中标公告显示预算2200万元[W1]。'
    seen = []

    def search(self, article):
        type(self).seen.append(article)
        return {'notes': self.notes, 'retrieved_at': '2026-10-26', 'sources': [
            {'source_id': 'W1', 'title': '公告', 'url': 'https://ccgp.example.gov.cn/a', 'kind': 'web'}]}


class FallbackTest(VerifyFixture):
    def setUp(self):
        super().setUp()
        CoveringResearch.seen = []

    def run_with(self, notes):
        CoveringResearch.notes = notes
        Research.verify_result = FOUND
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=HotSource, model_factory=FactWriter,
                        research_factory=CoveringResearch)
        engine.run('facts:1', self.f.config)
        return self.f.db.rows('SELECT * FROM drafts')[0]

    def test_identifiers_reach_the_first_search_but_not_the_writer(self):
        draft = self.run_with('中标公告显示预算2200万元[W1]。')
        sent = CoveringResearch.seen[0]['key_facts']
        self.assertEqual(sent[0]['identifiers'][:2], ['2200万元', '2090万元'])
        self.assertTrue(all('key_facts' not in s for s in json.loads(draft['sources'])))
        cached = json.loads(self.f.db.rows('SELECT result FROM research_cache')[0]['result'])
        self.assertEqual(cached['key_facts'], sent)
        self.assertIn('## 关键标识（写作前联网核对）\n- 2200万元：已找到官方原件\n- 2090万元：研究未涉及', draft['report'])

    def test_fully_covered_items_trigger_no_second_search(self):
        draft = self.run_with('中标公告显示预算2200万元[W1]。')
        self.assertEqual(Research.verify_calls, [])
        self.assertEqual(Writer.revisions, [])
        self.assertIn('结果：已由写作前联网研究覆盖，未触发兜底搜索。', draft['report'])

    def test_explicit_not_found_also_counts_as_covered(self):
        draft = self.run_with('联网事实[W1]。未找到官方出处：2200万元。')
        self.assertEqual(Research.verify_calls, [])
        self.assertIn('2200万元：研究写明未找到官方出处', draft['report'])

    def test_only_uncovered_items_are_searched(self):
        draft = self.run_with('联网事实[W1]。')
        self.assertEqual(len(Research.verify_calls), 1)
        self.assertEqual([i['claim'] for i in Research.verify_calls[0]], ['预算2200万元'])
        self.assertIn('已找到官方来源并补入引用', draft['report'])

    def test_old_cache_without_key_facts_still_works(self):
        job = self.f.db.job('facts:1', self.f.config)
        self.f.db.execute('INSERT INTO research_cache VALUES(?,?)', (job['id'], json.dumps(
            {'notes': '联网事实[W1]', 'retrieved_at': '2026-10-20', 'provider': 'openai', 'sources': [
                {'source_id': 'W1', 'title': '来源', 'url': 'https://example.org/r', 'kind': 'web'}]})))
        draft = self.run_with('不会用到')
        self.assertEqual(CoveringResearch.seen, [])          # the cached search is reused
        self.assertNotIn('## 关键标识', draft['report'])
        self.assertEqual(len(Research.verify_calls), 1)       # nothing was covered, so the fallback runs


if __name__ == '__main__':
    unittest.main()
