"""T-027: plainer, less templated articles: prompt rules, program checks, structures, style migration (offline)."""
import json
import unittest

import test_core
from test_article_quality import SequenceTransport, answers, chat, package, system_text, user_json
from test_claim_checks import DRAFT, KEPT, TITLE, revision
from wewriter.api import (EDITORIAL_BRIEF, PLAIN_VOICE, STRUCTURE_RULES, STRUCTURES, TITLE_AND_OPENING, WRITER_RULES,
                          ApiError, Model, banned_phrases, chosen_structure, draft_shape, readability,
                          readability_problems)
from wewriter.config import DEFAULTS, PREVIOUS_POLICY_STYLE, defaults, migrate
from wewriter.pipeline.composition import recent_shapes, report

CLEAN = {'title': '设备更新验收：运行满30天再报', 'body': (
    '某省要求设备更新项目连续运行满1个月才能验收。[S1]\n\n'
    '这对正在收尾的企业影响最大。[S1]\n\n## 要准备哪些材料\n\n'
    '每台设备都要有采购合同和到货单。[S1]\n\n旧设备要真的停用。[S1]')}


class PromptTest(unittest.TestCase):
    def setUp(self):
        data = package()
        self.sources, self.config = data['sources'], defaults()
        self.config.update(data['config'], recent_drafts=[draft_shape('旧稿标题', '旧开头\n\n## 先看依据')])
        self.config['draft_first_mode'] = False

    def test_writer_gets_the_style_and_the_review_does_not(self):
        # T-029: the T-028 rules became reference signals; style reaches only the writer.
        draft = {'title': '考完这场试', 'outline': [], 'review_notes': [], 'body': '正文[S1]，补充[W1]'}
        checks = [{'sentence': '标题：考完这场试', 'evidence': 'S1', 'action': '保留', 'note': ''}]
        transport = SequenceTransport(chat(draft), chat({**draft, 'quality_notes': [], 'claim_checks': checks}))
        model = Model('deepseek', defaults()['providers']['deepseek'], 'k', transport)
        model.review(model.write(self.sources, self.config), self.sources, self.config)
        write, review = transport.payloads
        self.assertTrue(system_text(write).startswith(WRITER_RULES))
        self.assertIn('【风格卡】', system_text(write))
        for rule in (TITLE_AND_OPENING, PLAIN_VOICE, STRUCTURE_RULES):
            self.assertNotIn(rule, system_text(write))
            self.assertNotIn(rule, system_text(review))
        self.assertNotIn('【风格卡】', system_text(review))
        self.assertEqual(user_json(write)['recent_drafts'], self.config['recent_drafts'])
        self.assertNotIn('recent_drafts', user_json(review))

    def test_style_review_request_checks_reader_interest_without_new_evidence(self):
        scores = {key: 4 for key in ('title', 'opening', 'hierarchy', 'voice', 'usefulness', 'distinctness')}
        transport = SequenceTransport(chat({'scores': scores, 'notes': [], 'rewrite': None}))
        model = Model('deepseek', defaults()['providers']['deepseek'], 'test-only', transport)
        model.style_review(CLEAN, self.sources, self.config, signals=[])
        request = transport.payloads[0]
        sent = system_text(request)
        self.assertIn('前两三句是否自然讲清事件和读者关系', sent)
        self.assertIn('不能判定稿内论述是否真实', sent)
        self.assertIn('title、opening、hierarchy、voice 任一维度低于 4 分', sent)
        self.assertEqual(user_json(request)['article']['body'], CLEAN['body'])
        self.assertEqual(user_json(request)['signals'], [])

    def test_the_fixed_skeleton_is_gone_from_the_brief(self):
        for gone in ('关键词｜核心判断', '→申报或执行要点→建议与风险提示', '时间怎么倒排'):
            self.assertNotIn(gone, EDITORIAL_BRIEF)
        for kept in ('企业中高层管理者', '可执行的建议', '优先补充原文之外的新信息', '往年'):
            self.assertIn(kept, EDITORIAL_BRIEF)
        self.assertEqual(set(STRUCTURES), {'问答式', '清单式', '场景式', '对比式', '时间线式'})


class CheckTest(unittest.TestCase):
    def test_a_plain_draft_passes(self):
        self.assertEqual(readability_problems(CLEAN), [])

    def test_title_rules(self):
        problems = readability_problems({**CLEAN, 'title': '设备更新验收：稳定运行满1个月可能是地方条件，先查项目依据'})
        self.assertTrue(any(p.startswith('标题 ') and '超过 22 字' in p for p in problems))
        self.assertTrue(any('含糊词“可能、先查”' in p for p in problems))

    def test_source_caveats_stay_out_of_the_opening(self):
        body = '现有材料称，遵义市拟采购方案编制服务。[S1]\n\n这对企业意味着什么。[S1]'
        problems = readability_problems({'title': '遵义400万元方案编制', 'body': body})
        self.assertIn('第一段出现来源说明“现有材料称”，请移到正文对应位置，开头先讲事件', problems)
        moved = '遵义市拟采购方案编制服务，预算400万元。[S1]\n\n现有材料称成交价为392万元。[S1]'
        self.assertEqual(readability_problems({'title': '遵义400万元方案编制', 'body': moved}), [])

    def test_long_sentences_and_banned_words(self):
        long_body = ('国家层面文件提供的是支持方向而非统一验收细则因此企业不宜将三十天作为所有项目的刚性验收门槛更不能据此替代属地主管部门的书面要求。[S1]\n\n'
                     * 3)
        figures = readability({'body': long_body})
        self.assertGreater(figures['mean'], 40)
        self.assertTrue(any(p.startswith('句子偏长') for p in readability_problems({'title': '短标题', 'body': long_body})))
        self.assertEqual(banned_phrases('对企业而言，这不是补贴而是采购；要形成闭环，按四步倒排。'),
                         ['闭环', '倒排', '对……而言', '不是……而是……'])
        self.assertEqual(banned_phrases('以正式文件定边界、以一机一档留痕、以对照表堵口子'), ['留痕', '以……以……以……排比'])

    def test_too_close_to_recent_drafts(self):
        recent = [draft_shape('设备更新验收：运行满30天再报', '某省要求设备更新项目连续运行满一个月才能验收\n\n## 要准备哪些材料\n\n## 旧设备要真的停用')]
        own = {**CLEAN, 'body': CLEAN['body'] + '\n\n## 旧设备要真的停用\n\n记得留存处置凭证。[S1]'}
        problems = readability_problems(own, recent)
        self.assertTrue(any(p.startswith('标题和近期稿件') for p in problems))
        self.assertTrue(any(p.startswith('开头和近期稿件') for p in problems))
        self.assertTrue(any(p.startswith('小标题和近期稿件') for p in problems))

    def test_structure_parsing(self):
        self.assertEqual(chosen_structure({'structure': '对比式', 'structure_reason': '新旧政策变化明显'}), ('对比式', '新旧政策变化明显'))
        self.assertEqual(chosen_structure({'structure': '论文式'}), ('', ''))
        self.assertEqual(chosen_structure({}), ('', ''))


class FactReviewTest(unittest.TestCase):
    def test_the_fact_review_no_longer_rewrites_for_style(self):
        data = package()
        config = defaults()
        config.update(data['config'], recent_drafts=[{'title': '考完这场试', 'opening': '', 'headings': []}])
        transport = SequenceTransport(chat(revision()), answers(DRAFT, revision()))
        model = Model('deepseek', defaults()['providers']['deepseek'], 'k', transport)
        result = model.review(dict(DRAFT), data['sources'], config)
        self.assertEqual(len(transport.payloads), 2)         # a title close to a recent one is not its business
        self.assertFalse(any(n.startswith('可读性｜') for n in result['quality_notes']))

    def test_unsupported_rewrites_still_do_not_survive(self):
        data = package()
        config = defaults()
        config.update(data['config'])
        transport = SequenceTransport(chat(revision()), chat({'answers': [{'id': 'C1', 'basis': '无依据'}]}), chat({'fixes': []}))
        result = Model('deepseek', defaults()['providers']['deepseek'], 'k', transport).review(dict(DRAFT), data['sources'], config)
        self.assertEqual(result['body'], DRAFT['body'])


class StyleAndShapesTest(unittest.TestCase):
    def test_old_policy_preset_text_moves_to_the_new_one(self):
        self.assertNotEqual(DEFAULTS['style'], PREVIOUS_POLICY_STYLE)
        self.assertEqual(migrate({'style': PREVIOUS_POLICY_STYLE, 'style_preset': '政策申报实务'})['style'], DEFAULTS['style'])
        extra = migrate({'style': PREVIOUS_POLICY_STYLE + '；补充要求：多用表格', 'style_preset': '政策申报实务'})
        self.assertEqual(extra['style'], DEFAULTS['style'] + '；补充要求：多用表格')
        self.assertEqual(migrate({'style': '我的风格', 'style_preset': ''})['style'], '我的风格')

    def test_recent_shapes_come_from_the_latest_kept_drafts(self):
        f = test_core.CoreTest()
        f.setUp()
        try:
            for n in range(3):
                job = f.db.job(f'shape:{n}', f.config)
                f.db.save_draft(job['id'], f'标题{n}', f'开头{n}。[S1]\n\n## 小标题{n}\n\n正文。[S1]', [], '报告')
            shapes = recent_shapes(f.db, limit=2)
            self.assertEqual([s['title'] for s in shapes], ['标题2', '标题1'])
            self.assertEqual(shapes[0]['opening'], '开头2。')
            self.assertEqual(shapes[0]['headings'], ['小标题2'])
            self.assertEqual([s['title'] for s in recent_shapes(f.db, exclude=3)], ['标题1', '标题0'])
        finally:
            f.tearDown()

    def test_the_report_names_the_chosen_structure(self):
        config = {**defaults(), 'selection_mode': 'recent'}
        source = {'source_id': 'S1', 'title': '原文', 'account_name': '号', 'url': 'https://mp.weixin.qq.com/s/a',
                  'analysis': [{'summary': '摘要', 'facts': [], 'uncertainties': []}]}
        text = report(config, [source], {'outline': [], 'review_notes': [], 'structure': '时间线式',
                                         'structure_reason': '有截止日期'}, [], None, False)
        self.assertIn('## 写作结构\n时间线式：有截止日期', text)


if __name__ == '__main__':
    unittest.main()
