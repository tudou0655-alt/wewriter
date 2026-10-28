"""T-030: no other WeChat account may appear in an article (customer's iron rule), offline."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_core
from test_article_quality import SequenceTransport, chat, system_text, user_json
from wewriter.anonymity import RULE, block_list, hits, scrub
from wewriter.api import STYLE_REVIEW_RULES, WRITER_RULES, ApiError, Model
from wewriter.config import defaults
from wewriter.pipeline.common import TaskError
from wewriter.pipeline.composition import leak_guard
from wewriter.style import fact_sheet
from wewriter.ui import clipboard_text, export_document
from wewriter.webapp import Api, Backend

SOURCES = [
    {'source_id': 'S1', 'article_id': 1, 'title': '15亿补贴，这类咨询服务项目已经开始了！', 'account_name': '咨询日记',
     'url': 'https://mp.weixin.qq.com/s/abc',
     'analysis': [{'summary': '市场出现咨询需求', 'facts': [{'claim': '有15亿元补贴相关咨询需求', 'quote': '15亿补贴'}],
                   'uncertainties': []}],
     'web_research': '国家文件取消了1亿元门槛[W1]。'},
    {'source_id': 'W1', 'title': '国家发展改革委通知', 'url': 'https://www.ndrc.gov.cn/a', 'kind': 'web'},
    {'source_id': 'W2', 'title': '另一篇公众号文章：设备更新怎么报', 'url': 'https://mp.weixin.qq.com/s/def', 'kind': 'web'},
]
CONFIG = {'topic': '', 'style': '', 'target_words': 1500, 'writing_mode': 'single_web', 'account_names': ['惠企策论', '咨询日记']}
LEAKY = ('有公众号“咨询日记”在一篇题为《15亿补贴，这类咨询服务项目已经开始了！》的文章中提到，市场已有15亿元补贴的咨询需求。[S1]\n\n'
         '国家已取消1亿元门槛。[W1]')
CLEAN = '市场已有15亿元补贴的咨询需求。[S1]\n\n国家已取消1亿元门槛。[W1]'


class BlockListTest(unittest.TestCase):
    def test_names_titles_and_wording(self):
        blocked = block_list(SOURCES, ['惠企策论'])
        self.assertEqual(blocked['names'], ['惠企策论', '咨询日记'])
        self.assertEqual(blocked['titles'], ['15亿补贴，这类咨询服务项目已经开始了！', '另一篇公众号文章：设备更新怎么报'])
        found = hits(LEAKY, blocked)
        self.assertIn(('名称', '咨询日记'), found)
        self.assertIn(('说法', '公众号'), found)
        self.assertTrue(any(kind == '标题' for kind, _ in found))
        self.assertEqual(hits('这类咨询服务项目已经开始了，值得关注。', blocked), [])
        self.assertTrue(any(kind == '标题' for kind, _ in hits('15亿补贴，这类咨询服务项目已经开始了！', blocked)))
        self.assertTrue(any(kind == '标题' for kind, _ in hits('这篇文章题为《这类咨询服务项目已经开始了》', blocked)))
        for fine in ('按文件原文办理。', '《美丽河湖保护与建设行动方案》提出……', CLEAN):
            self.assertEqual(hits(fine, blocked), [], fine)
        for word in ('有推文称', '原文作者认为', '据某号报道', '小编整理'):
            self.assertTrue(hits(word, blocked), word)

    def test_scrub_keeps_facts_and_document_names(self):
        blocked = block_list(SOURCES, ['惠企策论'])
        text, fixes = scrub(LEAKY + '\n\n惠企策论也做过分析。[S1]\n\n《美丽河湖保护与建设行动方案》要求自评估。[W1]', blocked)
        self.assertEqual(hits(text, blocked), [])
        self.assertIn('有行业消息称，市场已有15亿元补贴的咨询需求。[S1]', text)
        self.assertIn('《美丽河湖保护与建设行动方案》要求自评估。[W1]', text)
        self.assertNotIn('也做过分析', text)
        self.assertEqual([k for k, _ in fixes], ['删除标题', '改写', '删除'])

    def test_policy_name_shared_with_post_title_is_allowed(self):
        title = '2027年中央预算内投资资金申请报告提纲有哪些新变化？一文看懂2026与2027差异'
        blocked = block_list([{'source_id': 'S1', 'title': title, 'account_name': '虚构来源号'}])
        policy = '2027年中央预算内投资资金申请报告提纲'
        self.assertEqual(hits(policy, blocked), [])
        self.assertEqual(hits(f'文件《{policy}》列出申报材料。', blocked), [])
        self.assertTrue(any(kind == '标题' for kind, _ in hits(title, blocked)))
        self.assertTrue(any(kind == '标题' for kind, _ in hits(f'一篇题为《{policy}》的文章提到……', blocked)))


class PromptTest(unittest.TestCase):
    def test_every_model_step_carries_the_rule(self):
        self.assertIn(RULE, WRITER_RULES)
        self.assertIn(RULE, STYLE_REVIEW_RULES)
        checks = [{'sentence': '标题：咨询需求起来了', 'evidence': 'S1', 'action': '保留', 'note': ''}]
        draft = {'title': '咨询需求起来了', 'outline': [], 'review_notes': [], 'body': CLEAN}
        transport = SequenceTransport(chat({**draft, 'quality_notes': [], 'claim_checks': checks}),
                                      chat({**draft, 'claim_checks': []}))
        model = Model('deepseek', defaults()['providers']['deepseek'], 'k', transport)
        model.review(draft, SOURCES, CONFIG)
        model.revise_for_verification(draft, [{'claim': 'x'}], None, SOURCES, CONFIG)
        for payload in transport.payloads:
            self.assertIn(RULE, system_text(payload))

    def test_the_writer_never_sees_account_names_or_post_titles(self):
        transport = SequenceTransport(chat({'title': '咨询需求起来了', 'outline': [], 'review_notes': [], 'body': CLEAN}))
        Model('deepseek', defaults()['providers']['deepseek'], 'k', transport).write(SOURCES, CONFIG)
        sent = json.dumps(user_json(transport.payloads[0]), ensure_ascii=False)
        for secret in ('咨询日记', '这类咨询服务项目', '设备更新怎么报'):
            self.assertNotIn(secret, sent)
        self.assertIn('国家发展改革委通知', sent)              # official web pages keep their title
        self.assertNotIn('咨询日记', json.dumps(fact_sheet(SOURCES), ensure_ascii=False))


class EnforcementTest(unittest.TestCase):
    def model(self, *replies):
        transport = SequenceTransport(*[chat(r) for r in replies])
        return Model('deepseek', defaults()['providers']['deepseek'], 'k', transport), transport

    def test_writer_gets_one_rewrite_then_the_program_scrubs(self):
        leaky = {'title': '咨询需求起来了', 'outline': [], 'review_notes': [], 'body': LEAKY}
        model, transport = self.model(leaky, leaky)
        result = model.write(SOURCES, CONFIG)
        self.assertIn('稿件出现了其他公众号或其文章', user_json(transport.payloads[1])['validation_feedback'])
        self.assertEqual(hits(result['title'] + result['body'], block_list(SOURCES, CONFIG['account_names'])), [])
        self.assertIn('15亿元', result['body'])                 # facts and marks survive
        self.assertIn('[S1]', result['body'])
        self.assertTrue(result['leak_fixes'])

    def test_a_title_that_names_an_account_is_never_scrubbed_into_a_draft(self):
        leaky = {'title': '咨询日记说：15亿补贴来了', 'outline': [], 'review_notes': [], 'body': CLEAN}
        model, _ = self.model(leaky, leaky)
        with self.assertRaisesRegex(ApiError, '稿件出现了其他公众号'):
            model.write(SOURCES, CONFIG)

    def test_shared_policy_topic_does_not_trigger_a_second_paid_call(self):
        sources = [{**SOURCES[0], 'title': '2027年中央预算内投资资金申请报告提纲有哪些新变化？一文看懂差异'}, SOURCES[1]]
        body = '报告提纲列出了申请材料。[S1]\n\n官方文件说明了适用范围。[W1]'
        first = {'title': '2027年中央预算内投资资金申请报告提纲',
                 'outline': [], 'review_notes': [], 'body': body}
        model, transport = self.model(first)
        result = model.write(sources, CONFIG)
        self.assertEqual(result['title'], first['title'])
        self.assertEqual(result['body'], body)
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual(hits(result['title'], block_list(sources, CONFIG['account_names'])), [])

    def test_near_complete_source_headline_repairs_only_title(self):
        source = {**SOURCES[0], 'title': '2027年中央预算内投资资金申请报告提纲有哪些新变化？'}
        first = {'title': source['title'],
                 'outline': [], 'review_notes': [], 'body': CLEAN}
        model, transport = self.model(first, {'title': '申报前先看材料要求'})
        result = model.write([source, SOURCES[1]], CONFIG)
        self.assertEqual(result['title'], '申报前先看材料要求')
        self.assertEqual(len(transport.payloads), 2)

    def test_a_leaking_review_is_rewritten_once_then_discarded(self):
        draft = {'title': '咨询需求起来了', 'outline': [], 'review_notes': [], 'body': CLEAN}
        leaky = {**draft, 'body': '据公众号“咨询日记”，' + CLEAN, 'quality_notes': [], 'claim_checks': [
            {'sentence': '标题：咨询需求起来了', 'evidence': 'S1', 'action': '保留', 'note': ''}]}
        model, transport = self.model(leaky, leaky)
        with self.assertRaisesRegex(ApiError, '已弃用修改稿'):
            model.review(draft, SOURCES, CONFIG)
        self.assertIn('其他公众号', user_json(transport.payloads[1])['validation_feedback'])

    def test_last_guard_before_saving(self):
        output, report = leak_guard({'title': '咨询需求起来了', 'outline': ['咨询日记的观点'], 'body': LEAKY,
                                     'review_notes': []}, SOURCES, CONFIG)
        self.assertEqual(hits(output['body'], block_list(SOURCES, CONFIG['account_names'])), [])
        self.assertEqual(output['outline'], [])
        self.assertIn('## 来源保密检查', report)
        self.assertIn('咨询日记', report)                         # the internal report may name the source
        with self.assertRaises(TaskError):
            leak_guard({'title': '咨询日记：15亿补贴', 'outline': [], 'body': CLEAN}, SOURCES, CONFIG)
        _, clean = leak_guard({'title': '咨询需求起来了', 'outline': [], 'body': CLEAN}, SOURCES, CONFIG)
        self.assertIn('未发现其他公众号名称', clean)


class ExportTest(unittest.TestCase):
    def test_exports_and_copies_list_no_wechat_posts(self):
        body = CLEAN + '\n\n另一条。[W2]'
        for text in (export_document('标题', body, SOURCES), export_document('标题', body, SOURCES, markdown=False),
                     clipboard_text('标题', body, SOURCES)[0], clipboard_text('标题', body, SOURCES)[1]):
            self.assertNotIn('mp.weixin.qq.com', text)
            self.assertNotIn('咨询日记', text)
            self.assertNotIn('设备更新怎么报', text)
            self.assertIn('ndrc.gov.cn', text)


class OldDraftTest(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreTest()
        self.f.setUp()
        self.f.db.add_account('咨询日记', 'MP_WXS_1')
        job = self.f.db.job('old:1', self.f.config)
        self.draft_id = self.f.db.save_draft(job['id'], '咨询需求起来了', LEAKY, SOURCES, '报告')
        self.api = Api(Backend(self.f.db, test_core.FakeVault(), Path(tempfile.mkdtemp(dir=self.f.path))))

    def tearDown(self):
        self.f.tearDown()

    def test_old_drafts_are_flagged_and_cannot_be_copied_or_exported(self):
        detail = self.api.dr_get(self.draft_id)
        self.assertTrue(any('咨询日记' in leak for leak in detail['leaks']))
        copied = self.api.dr_copy(self.draft_id)
        self.assertFalse(copied['ok'])
        self.assertIn('稿件中出现了其他公众号名称，导出前请修改或点补跑二审', copied['error'])
        exported = self.api.dr_export(self.draft_id, 'copy', 'md')
        self.assertFalse(exported['ok'])
        self.assertIn('稿件中出现了其他公众号名称', exported['error'])
        self.f.db.edit_draft(self.draft_id, '咨询需求起来了', CLEAN, '待审核')
        self.assertEqual(self.api.dr_get(self.draft_id)['leaks'], [])
        with mock.patch('wewriter.webapp.copy_to_clipboard', return_value=True):
            self.assertTrue(self.api.dr_copy(self.draft_id)['ok'])


if __name__ == '__main__':
    unittest.main()
