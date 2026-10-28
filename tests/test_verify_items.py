"""T-023: the review does not hand source checking to the reader; the program checks itself (offline)."""
import json
import unittest

import test_core
from test_article_quality import SequenceTransport, chat, package, system_text, user_json
from test_hotspots import HotSource, HotModel
from wewriter.api import (VERIFY_RULES, ApiError, Model, is_verification_todo, merge_similar, split_todos)
from wewriter.config import defaults
from wewriter.engine import Engine
from wewriter.research import WebResearch
from wewriter.ui import review_marks

# Notes from the 2026-10-26 Ma'anshan draft (id 12), verbatim in shape.
MAANSHAN_TODOS = [
    '发布前应继续检索并留存中国政府采购网安徽分网或马鞍山市公共资源交易平台原始中标公告，重点核实项目编号、预算2200万元、最高限价2090万元、1986万元成交金额、成交供应商、公告日期和驻点人员要求。',
    '自然资发〔2025〕226号所涉过渡期政策的具体适用条件，以及马鞍山当地执行口径，仍应结合完整文件和地方细则复核。',
    '如后续取得原始采购公告，应据公告原文更新S12所载采购人、采购方式、金额、供应商、驻点要求和公告日期等交易信息。',
    '建议发布前继续检索并留存中国政府采购网安徽分网或马鞍山市公共资源交易平台的原始中标公告，以核实项目编号、预算2200万元、最高限价2090万元、1986万元成交金额、成交供应商、公告日期及驻点人员要求。',
    '文中“对符合条件的存量土地和房产资源项目可适用过渡期政策，原则上不超过5年”的具体适用条件和当地执行口径，建议结合自然资发〔2025〕226号原文及马鞍山后续细则复核。',
]
JUDGEMENT = '标题中“不是企业补贴”的判断语气较强，请编辑决定是否保留。'


class TodoRuleTest(unittest.TestCase):
    def test_verification_todos_are_recognised(self):
        for note in MAANSHAN_TODOS:
            self.assertTrue(is_verification_todo(note), note)
        for note in (JUDGEMENT, '引号内容未能在资料中逐字核对，发布前请核实或改为转述：“基本靠蒙”',
                     '二审逐句核对没有包含标题，发布前请人工核对标题：标题', '核对 Bearer 示例'):
            self.assertFalse(is_verification_todo(note), note)

    def test_todos_become_deduplicated_verify_items(self):
        result = split_todos({'review_notes': [JUDGEMENT] + MAANSHAN_TODOS, 'verify_items': [
            {'claim': '预算2200万元', 'identifiers': ['2200万元'], 'queries': ['马鞍山 城市更新 中标公告']}, '无效项']})
        self.assertEqual(result['review_notes'], [JUDGEMENT])
        claims = [i['claim'] for i in result['verify_items']]
        self.assertEqual(claims[0], '预算2200万元')
        self.assertEqual(len(claims), 5)          # the two near-identical procurement notes merge into one
        self.assertTrue(claims[1].startswith('发布前应继续检索'))
        self.assertEqual(result['verify_items'][2]['identifiers'], ['自然资发〔2025〕226号'])
        self.assertIn('2200万元', result['verify_items'][1]['identifiers'])

    def test_similar_notes_merge_and_the_first_wording_is_kept(self):
        self.assertEqual(merge_similar(['需要人判断：标题语气偏强', '需要人判断：标题的语气偏强', '另一件事']),
                         ['需要人判断：标题语气偏强', '另一件事'])

    def test_prompts_carry_the_three_choices_and_verify_items(self):
        data = package()
        config = defaults()
        config.update(data['config'])
        config['draft_first_mode'] = False
        draft = {'title': '考完这场试', 'outline': [], 'review_notes': [], 'body': '正文[S1]，补充[W1]'}
        checks = [{'sentence': '标题：考完这场试', 'evidence': 'S1', 'action': '保留', 'note': ''}]
        transport = SequenceTransport(chat(draft), chat({**draft, 'quality_notes': [], 'claim_checks': checks}))
        model = Model('deepseek', defaults()['providers']['deepseek'], 'test-only-not-a-key', transport)
        model.review(model.write(data['sources'], config), data['sources'], config)
        for payload in transport.payloads:
            self.assertIn(VERIFY_RULES, system_text(payload))
            self.assertIn('"verify_items"', system_text(payload))

    def test_review_converts_its_own_and_carried_todos(self):
        data = package()
        config = defaults()
        config.update(data['config'])
        draft = {'title': '考完这场试', 'outline': [], 'body': '正文[S1]，补充[W1]',
                 'review_notes': [MAANSHAN_TODOS[3]], 'verify_items': [{'claim': '文号待查'}]}
        checks = [{'sentence': '标题：考完这场试', 'evidence': 'S1', 'action': '保留', 'note': ''}]
        reviewed = {**draft, 'review_notes': [MAANSHAN_TODOS[0], JUDGEMENT], 'quality_notes': [], 'claim_checks': checks}
        reviewed.pop('verify_items')
        model = Model('deepseek', defaults()['providers']['deepseek'], 'k', SequenceTransport(chat(reviewed)))
        result = model.review(draft, data['sources'], config)
        self.assertEqual(result['review_notes'], [JUDGEMENT])
        self.assertEqual([i['claim'][:6] for i in result['verify_items']], ['文号待查', '发布前应继续'])


class Writer(HotModel):
    """single_web writer whose review leaves one verify item."""
    revisions = []
    revise_error = None

    def write(self, sources, config):
        marks = ''.join(f"[{s['source_id']}]" for s in sources)
        return {'title': '马鞍山稿', 'body': '项目预算2200万元。' + marks, 'outline': [], 'review_notes': [JUDGEMENT]}

    def review(self, draft, sources, config):
        return {**draft, 'quality_notes': ['逐句核对｜保留｜项目预算2200万元。｜证据：S1'],
                'verify_items': [{'claim': '预算2200万元', 'identifiers': ['2200万元'], 'queries': []}]}

    def revise_for_verification(self, article, items, findings, sources, config):
        type(self).revisions.append((items, findings, [s['source_id'] for s in sources]))
        if self.revise_error:
            raise self.revise_error
        marks = ''.join(f"[{s['source_id']}]" for s in sources)
        if findings:
            body = '据中标公告，项目预算2200万元。' + marks + '[' + findings['sources'][0]['source_id'] + ']'
        else:
            body = '原始材料称项目预算2200万元，截至发稿未查到官方公告。' + marks
        return {**article, 'body': body, 'review_notes': list(article['review_notes']),
                'quality_notes': ['逐句核对｜修正｜' + body[:20] + '｜证据：S1｜改写｜初稿：项目预算2200万元。']}


class Research:
    verify_result = None
    verify_calls = []

    def __init__(self, *args):
        pass

    def search(self, article):
        return {'notes': '联网事实[W1]', 'retrieved_at': '2026-10-26', 'sources': [
            {'source_id': 'W1', 'title': '来源', 'url': 'https://example.org/research', 'kind': 'web'}]}

    def verify(self, title, items):
        type(self).verify_calls.append(items)
        if isinstance(self.verify_result, Exception):
            raise self.verify_result
        return self.verify_result


FOUND = {'notes': '中标公告显示预算2200万元[W1]', 'retrieved_at': '2026-10-26', 'provider': 'openai',
         'sources': [{'source_id': 'W1', 'title': '中标公告', 'url': 'https://ccgp.example.gov.cn/notice', 'kind': 'web'}]}


class VerifyFixture(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreTest()
        self.f.setUp()
        self.f.config.update(writing_mode='single_web', selection_mode='hot', min_sources=1, article_limit=1,
                             draft_first_mode=False)
        Writer.revisions, Writer.revise_error, Research.verify_calls = [], None, []

    def tearDown(self):
        Research.verify_result = None
        self.f.tearDown()

    def run_job(self, **config):
        self.f.config.update(config)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=HotSource, model_factory=Writer,
                        research_factory=Research)
        engine.run('verify:1', self.f.config)
        return self.f.db.rows('SELECT * FROM drafts')[0]


class PipelineVerifyTest(VerifyFixture):
    def test_found_source_is_cited_under_the_next_number(self):
        Research.verify_result = FOUND
        draft = self.run_job()
        items, findings, ids = Writer.revisions[0]
        self.assertEqual(items[0]['claim'], '预算2200万元')
        self.assertEqual(findings['sources'][0]['source_id'], 'W2')          # after the research's W1
        self.assertEqual(findings['notes'], '中标公告显示预算2200万元[W2]')
        self.assertIn('[W2]', draft['body'])
        sources = json.loads(draft['sources'])
        self.assertEqual([s['source_id'] for s in sources if s['source_id'].startswith('W')], ['W1', 'W2'])
        self.assertIn('## 自动查证', draft['report'])
        self.assertIn('已找到官方来源并补入引用', draft['report'])
        self.assertIn('### 查证修订（初稿 → 修订稿）', draft['report'])
        self.assertNotIn('发布前', draft['report'].split('## 审核提示')[1].split('##')[0])

    def test_nothing_found_makes_the_claim_cautious(self):
        Research.verify_result = ApiError('没有有效的联网引用来源，已停止该篇写作')
        draft = self.run_job()
        self.assertIsNone(Writer.revisions[0][1])
        self.assertIn('截至发稿未查到官方公告', draft['body'])
        self.assertIn('未找到官方来源，相关说法已改为审慎表述或删除', draft['report'])

    def test_network_failure_is_skipped_silently(self):
        Research.verify_result = ApiError('网络连接失败或超时', kind='network')
        draft = self.run_job()
        self.assertEqual(Writer.revisions, [])
        self.assertTrue(draft['body'].startswith('项目预算2200万元。'))
        self.assertIn('联网查证失败或超时，已跳过，稿件未改动', draft['report'])
        job = self.f.db.rows("SELECT warnings FROM jobs WHERE run_key='verify:1'")[0]
        self.assertFalse(any('查证' in w for w in json.loads(job['warnings'])))

    def test_switch_off_means_no_search(self):
        Research.verify_result = FOUND
        draft = self.run_job(auto_verify=False)
        self.assertEqual(Research.verify_calls, [])
        self.assertIn('已关闭“二审后自动查证存疑事实”', draft['report'])

    def test_rejected_revision_leaves_the_draft(self):
        Research.verify_result = FOUND
        Writer.revise_error = ApiError('查证修订未通过逐句核对：……', kind='validation')
        draft = self.run_job()
        self.assertTrue(draft['body'].startswith('项目预算2200万元。'))
        self.assertNotIn('W2', draft['sources'])
        self.assertIn('查证后的修订未通过校验，未采用', draft['report'])

    def test_default_is_on(self):
        self.assertTrue(defaults()['auto_verify'])


class RerunVerifyTest(VerifyFixture):
    """补跑二审 checks an older draft's hidden source-check requests for real."""

    def old_draft(self):
        draft = self.run_job(auto_verify=False)
        report = draft['report'].replace('## 审核提示\n', '## 审核提示\n- ' + MAANSHAN_TODOS[0] + '\n- '
                                         + MAANSHAN_TODOS[3] + '\n- ' + MAANSHAN_TODOS[1] + '\n', 1)
        report = report[:report.index('\n\n## 自动查证')]           # as written before T-023
        self.f.db.execute('UPDATE drafts SET report=? WHERE id=?', (report, draft['id']))
        return self.f.db.rows('SELECT * FROM drafts WHERE id=?', (draft['id'],))[0]

    def engine(self):
        return Engine(self.f.db, test_core.FakeVault(), source_factory=HotSource, model_factory=Writer,
                      research_factory=Research)

    def test_old_todos_are_counted_then_checked_by_the_rerun(self):
        from wewriter.pipeline.composition import unverified_count
        draft = self.old_draft()
        self.assertEqual(unverified_count(draft['report']), 2)        # the two procurement notes are one fact
        Research.verify_result = FOUND
        self.assertEqual(self.engine().rerun_review(draft['id']), 2)
        now = self.f.db.rows('SELECT * FROM drafts WHERE id=?', (draft['id'],))[0]
        self.assertIn('[W2]', now['body'])
        self.assertIn('W2', [s['source_id'] for s in json.loads(now['sources'])])
        self.assertIn('已找到官方来源并补入引用', now['report'])
        self.assertEqual(unverified_count(now['report']), 0)

    def test_switch_off_at_rerun_keeps_the_count(self):
        from wewriter.pipeline.composition import unverified_count
        draft = self.old_draft()
        config = self.f.db.config()
        config['auto_verify'] = False
        self.f.db.save_config(config)
        Research.verify_result = FOUND
        self.engine().rerun_review(draft['id'])
        now = self.f.db.rows('SELECT * FROM drafts WHERE id=?', (draft['id'],))[0]
        self.assertEqual(Research.verify_calls, [])
        self.assertIn('已关闭“二审后自动查证存疑事实”', now['report'])
        # The review's own item plus the two old requests (procurement notices merged): all still unchecked.
        self.assertEqual(unverified_count(now['report']), 3)
        self.assertNotIn('发布前应继续检索', now['report'].split('## 审核提示')[1].split('##')[0])

    def test_draft_view_reports_the_count_not_the_items(self):
        from pathlib import Path
        from wewriter.webapp import Api, Backend
        draft = self.old_draft()
        detail = Api(Backend(self.f.db, test_core.FakeVault(), Path(self.f.temp.name))).dr_get(draft['id'])
        self.assertEqual(detail['unverified'], 2)
        self.assertFalse(any('检索' in h for h in detail['unplaced']))


class ReviseForVerificationTest(unittest.TestCase):
    def setUp(self):
        data = package()
        self.sources, self.config = data['sources'], defaults()
        self.config.update(data['config'])
        self.article = {'title': '考完这场试', 'outline': [], 'review_notes': [JUDGEMENT],
                        'body': '成绩预计两个月内公布。[S1]\n\n其他内容保持不变。[W1]'}
        self.new = {'source_id': 'W9', 'title': '官方通知', 'url': 'https://gov.example/notice', 'kind': 'web'}

    def model(self, *replies):
        return Model('deepseek', defaults()['providers']['deepseek'], 'k', SequenceTransport(*[chat(r) for r in replies]))

    def test_cited_revision_passes_the_sentence_checks(self):
        body = '据官方通知，成绩预计两个月内公布。[S1][W9]\n\n其他内容保持不变。[W1]'
        reply = {**self.article, 'body': body, 'review_notes': [MAANSHAN_TODOS[0]], 'claim_checks': [
            {'final_sentence': '据官方通知，成绩预计两个月内公布。', 'draft_sentence': '成绩预计两个月内公布。',
             'evidence': 'W9：两个月内', 'verdict': '修正', 'note': '补官方来源'}]}
        revised = self.model(reply).revise_for_verification(
            self.article, [{'claim': '两个月'}], {'notes': '通知[W9]', 'sources': [self.new]}, self.sources, self.config)
        self.assertEqual(revised['body'], body)
        self.assertEqual(revised['review_notes'], [JUDGEMENT])        # the todo is dropped, the judgement kept
        self.assertEqual(revised['verify_items'], [])

    def test_unlisted_change_is_not_adopted(self):
        reply = {**self.article, 'body': '成绩据说两个月内公布，另有新消息。[S1]\n\n其他内容保持不变。[W1]',
                 'claim_checks': []}
        with self.assertRaisesRegex(ApiError, '查证修订未通过逐句核对'):
            self.model(reply).revise_for_verification(self.article, [{'claim': 'x'}], None, self.sources, self.config)


class SearchPayloadTest(unittest.TestCase):
    def test_verify_searches_official_originals_in_background_mode(self):
        done = {'id': 'resp_v', 'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed'},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '中标公告原文', 'annotations': [
                {'type': 'url_citation', 'start_index': 0, 'end_index': 4, 'url': 'https://ccgp.example.gov.cn/a',
                 'title': '公告'}]}]}]}
        transport = test_core.FakeTransport(done)
        out = WebResearch('k', defaults(), transport).verify('标题', [{'claim': '预算2200万元'}])
        payload = transport.payloads[0][1]
        self.assertTrue(payload['background'] and payload['store'])
        self.assertIn('政府采购网', payload['instructions'])
        self.assertEqual(json.loads(payload['input'])['items'], [{'claim': '预算2200万元'}])
        self.assertEqual(out['sources'][0]['source_id'], 'W1')


class DisplayTest(unittest.TestCase):
    def test_old_todos_are_hidden_duplicates_merged_and_reworded_quote_placed(self):
        body = ('2026年度中央财政支持范围包括城市基础设施品质提升。[W2]\n\n'
                '自然资发〔2025〕226号提出，符合条件的存量土地和房产资源项目可适用过渡期政策，原则上不超过5年；'
                '是否适用仍须以主管部门认定为准。[W2]')
        quoted = '文中“对符合条件的存量土地和房产资源项目可适用过渡期政策，原则上不超过5年”的表述较原文更肯定，请编辑判断是否保留。'
        report = ('## 审核提示\n' + '\n'.join('- ' + n for n in MAANSHAN_TODOS + [JUDGEMENT, JUDGEMENT.replace('较强', '偏强'),
                                                                              quoted]) + '\n\n来源')
        marks, unplaced = review_marks('马鞍山稿', body, report)
        self.assertEqual(marks, {1: [quoted]})
        self.assertEqual(unplaced, [JUDGEMENT])

    def test_stale_findings_in_older_reports_are_not_shown(self):
        body = ('在官方公告未出现前，不应将392万元成交信息当作确定事实传播。[S1]\n\n'
                '有考生说：“最后半小时基本靠蒙”。[S1]')
        report = ('## 审核提示\n'
                  '- 可能新增了资料没有的传播范围、关注度、时间或判断依据，发布前请核实或删除：'
                  '在官方公告未出现前，不应将392万元成交信息当作确定事实传播。[S1]\n'
                  '- 引号内容未能在资料中逐字核对，发布前请核实或改为转述：“有400万元专项资金可申报”\n'
                  '- 引号内容未能在资料中逐字核对，发布前请核实或改为转述：“最后半小时基本靠蒙”\n\n来源')
        marks, unplaced = review_marks('标题', body, report)
        self.assertEqual(marks, {1: ['引号内容未能在资料中逐字核对，发布前请核实或改为转述：“最后半小时基本靠蒙”']})
        self.assertEqual(unplaced, [])

if __name__ == '__main__':
    unittest.main()
