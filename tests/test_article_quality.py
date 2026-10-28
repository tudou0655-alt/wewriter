"""T-008 article-quality regressions. Offline only: fake transports, fictional materials.
These tests prove what the prompts receive and that export stays clean; they do not prove
that real model output got better."""
import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import test_core
from wewriter.api import (ARTICLE_VOICE, FACTS_FIRST, FIDELITY_RULES, ApiError, Model, changed_sentences,
                          style_context, unchecked_additions, unsupported_attributions, unverified_quotes)
from wewriter.config import defaults
from wewriter.engine import Engine
from wewriter.ui import export_document, source_website_text

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'tests' / 'fixtures' / 'article_quality' / 'package.json'
REPORT_PACKAGE = ROOT / 'tests' / 'fixtures' / 'article_quality' / 'package_report_style.json'
CHECKS = [{'sentence': '有考生反映计量时间紧', 'evidence': 'S1：最后半小时普遍来不及做完', 'action': '保留', 'note': ''}]
_spec = importlib.util.spec_from_file_location('evaluate_article_quality', ROOT / 'scripts' / 'evaluate_article_quality.py')
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)

# Shaped like the 稿件-10 baseline (fictional wording): evidence classes used as structure.
REPORT_STYLE = """# 示例考试观察：可证事实、争议与建议

核心变化与本文价值点

- 要点：本文把可直接核实的事实与考生反馈分开呈现。

可核实的事实（时间与程序）

- 考试时间：示例考试在某日举行。

可能解释（基于证据的合理推断）

- 解释：原因可能有多重。

（文中区分了可核实事实与经验性观察，并在每一相关句后标注了现有证据来源。）

## 参考资料网站

- [示例](https://example.org/a)
"""
NATURAL = """# 考完这场试，大家最想知道的三件事

考场外排起长队的时候，很多人心里想的不是题目，而是这张证书还值不值得。[S1]

据示例省人事考试网的安排，成绩预计在考后两个月内公布。[W1]这段时间不妨先把计量科目的错题整理出来。

有企业负责人说，现在更看重实际项目经验。[S1]这未必代表所有地区，但值得提前准备作品集。
"""


def chat(payload_json):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload_json, ensure_ascii=False)}}]}


class SequenceTransport:
    """Returns the queued responses in order and records every request payload."""

    def __init__(self, *responses):
        self.responses, self.payloads = list(responses), []

    def request(self, url, key, payload=None, *, timeout=100):
        self.payloads.append(payload)
        return self.responses.pop(0)


def package():
    return json.loads(PACKAGE.read_text(encoding='utf-8'))


def answers(draft, revised, basis='S1', **by_id):
    """The second review call's reply: the basis of every change the program numbers between
    draft and revised (by_id overrides single answers, e.g. C2='无依据')."""
    changes, _ = changed_sentences(draft, revised)
    return chat({'answers': [{'id': c['id'], 'basis': by_id.get(c['id'], [basis]), 'note': '改'} for c in changes]})


def user_json(payload):
    return json.loads(payload['messages'][1]['content'])


def system_text(payload):
    return payload['messages'][0]['content']


class StyleToReviewTest(unittest.TestCase):
    def setUp(self):
        data = package()
        self.sources, self.config = data['sources'], defaults()
        self.config.update(data['config'], style='语气克制，多用短段落', draft_first_mode=False)
        self.draft = {'title': '考完这场试', 'outline': ['场景'], 'review_notes': [],
                      'body': '正文[S1]，补充[W1]'}

    def model(self, *responses):
        transport = SequenceTransport(*responses)
        return Model('deepseek', defaults()['providers']['deepseek'], 'test-only-not-a-key', transport), transport

    def test_style_goes_to_the_writer_only(self):
        # T-029: the writer gets the style card and the user's extra style; the fact review gets none.
        reviewed = {**self.draft, 'quality_notes': ['已对照风格'], 'claim_checks': CHECKS}
        model, transport = self.model(chat(self.draft), chat(reviewed))
        draft = model.write(self.sources, self.config)
        model.review(draft, self.sources, self.config)
        written, review = transport.payloads
        self.assertIn('【风格卡】', system_text(written))
        self.assertEqual(user_json(written)['user_style'], '语气克制，多用短段落')
        self.assertNotIn('【风格卡】', system_text(review))
        for key in ('style', 'style_reference', 'recent_drafts', 'structure'):
            self.assertNotIn(key, user_json(review))

    def test_both_passes_put_facts_before_style_and_forbid_report_structure(self):
        reviewed = {**self.draft, 'quality_notes': ['ok'], 'claim_checks': CHECKS}
        model, transport = self.model(chat(self.draft), chat(reviewed))
        model.review(model.write(self.sources, self.config), self.sources, self.config)
        for payload in transport.payloads:
            text = system_text(payload)
            self.assertIn(FACTS_FIRST, text)
        review = system_text(transport.payloads[1])
        self.assertIn('不是正文小标题', review)
        # The old wording that drafts echoed as headings is gone.
        self.assertNotIn('核心变化及读者价值', review)
        self.assertNotIn('将原文观点、已核实事实和编辑推断清楚区分', review)
        self.assertNotIn('事实、来源观点与编辑判断要明确区分', system_text(transport.payloads[0]))

    def test_review_keeps_the_draft_organisation(self):
        sources = [dict(s) for s in self.sources]
        sources[0].pop('style_reference')
        self.assertIsNone(style_context(sources))
        model, transport = self.model(chat({**self.draft, 'quality_notes': ['ok'], 'claim_checks': CHECKS}))
        model.review(self.draft, sources, self.config)
        self.assertIn('不调整标题写法、结构和口吻', system_text(transport.payloads[0]))

    def test_review_cannot_add_sources_to_follow_a_style(self):
        invented = {**self.draft, 'body': '为了更像参考稿，加了一段采访[S9]', 'quality_notes': ['ok'], 'claim_checks': CHECKS}
        # An invalid revision gets one repair attempt; both attempts invent a source here.
        model, _ = self.model(chat(invented), chat(invented))
        with self.assertRaises(ApiError):
            model.review(self.draft, self.sources, self.config)


class FidelityTest(unittest.TestCase):
    """Rules and checks for the error types found in the 2026-10-19 blind comparison."""

    setUp = StyleToReviewTest.setUp
    model = StyleToReviewTest.model

    def test_both_passes_carry_the_three_fidelity_rules(self):
        model, transport = self.model(chat(self.draft), chat({**self.draft, 'quality_notes': [], 'claim_checks': CHECKS}))
        model.review(model.write(self.sources, self.config), self.sources, self.config)
        for payload in transport.payloads:
            text = system_text(payload)
            self.assertIn(FIDELITY_RULES, text)
        for phrase in ('只提供表达方式，不提供事实', '明确标注的假设', '加引号的直接引语必须能在资料中逐字核对',
                       '比例、比率的变化不等于人数的变化', '缺少基数时不写绝对人数'):
            self.assertIn(phrase, FIDELITY_RULES)
        review = system_text(transport.payloads[1])
        self.assertNotIn('claim_checks', review)
        self.assertIn('程序会自动比对初稿和修订稿', review)

    def test_the_change_table_is_made_by_the_program_and_stays_out_of_the_body(self):
        draft = {**self.draft, 'body': '考场外很多人在讨论这张证书。[S1]\n\n补充成绩公布的时间安排。[W1]'}
        revised = {**draft, 'body': '考场外不少考生在讨论这张证书。[S1]\n\n补充成绩公布的时间安排。[W1]', 'quality_notes': ['已压缩结尾'],
                   'claim_checks': [{'sentence': '模型自己写的表', 'evidence': '无', 'action': '删除', 'note': '不再使用'}]}
        model, transport = self.model(chat(revised), answers(draft, revised))
        result = model.review(draft, self.sources, self.config)
        self.assertNotIn('claim_checks', result)
        self.assertNotIn('逐句核对', result['body'])
        self.assertTrue(result['quality_notes'][0].startswith('逐句核对｜修正｜考场外不少考生在讨论这张证书。[S1]｜证据：S1'))
        self.assertIn('｜初稿：考场外很多人在讨论这张证书。[S1]', result['quality_notes'][0])
        self.assertFalse(any('模型自己写的表' in n for n in result['quality_notes']))
        self.assertEqual(result['quality_notes'][-1], '已压缩结尾')
        self.assertEqual(len(transport.payloads), 2)           # revision + basis answers
        self.assertEqual(user_json(transport.payloads[1])['changes'][0]['id'], 'C1')
        self.assertNotIn('draft', user_json(transport.payloads[1]))          # the basis call is small

    def test_an_unchanged_revision_needs_no_basis_call(self):
        model, transport = self.model(chat({**self.draft, 'quality_notes': ['ok']}))
        result = model.review(self.draft, self.sources, self.config)
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual(result['quality_notes'], ['ok'])

    def test_a_missing_or_invalid_answer_gets_one_fix_and_then_goes_back_to_the_draft(self):
        draft = {**self.draft, 'body': '示例省今年报名人数减少约两成。[S1]\n\n补充成绩公布的时间安排。[W1]'}
        revised = {**draft, 'body': '今年全国报名人数翻了一倍。[S1]\n\n补充成绩公布的时间安排。[W1]', 'quality_notes': ['ok']}
        # missing, invalid number, 无依据, 无需依据 on a sentence with a number, no answers at all
        for reply in ({'answers': []}, {'answers': [{'id': 'C1', 'basis': ['S9']}]}, {'answers': [{'id': 'C1', 'basis': '无依据'}]},
                      {'answers': [{'id': 'C1', 'basis': '无需依据'}]}, {'nothing': True}):
            model, transport = self.model(chat(revised), chat(reply), chat({'fixes': []}))
            with self.subTest(reply=reply):
                result = model.review(draft, self.sources, self.config)
                self.assertEqual(result['body'], draft['body'])               # back to the draft sentence
                self.assertEqual(len(transport.payloads), 3)
                self.assertEqual(user_json(transport.payloads[2])['sentences'][0]['id'], 'C1')
                self.assertTrue(any(n.startswith('二审改动无依据，已回退为初稿原句') for n in result['quality_notes']))

    def test_answers_may_come_in_any_order_and_a_valid_fix_is_used(self):
        draft = {**self.draft, 'body': '第一句原文内容在这里。[S1]\n\n第二句原文内容在这里。[W1]'}
        revised = {**draft, 'body': '第一句改过的内容在这里。[S1]\n\n第二句改过的内容在这里。[W1]', 'quality_notes': ['ok']}
        reply = {'answers': [{'id': 'C2', 'basis': ['W1']}, {'id': 'c1', 'basis': '无依据'}]}
        fix = {'fixes': [{'id': 'C1', 'sentence': '第一句按资料改写在这里。[S1]', 'basis': ['S1']}]}
        model, transport = self.model(chat(revised), chat(reply), chat(fix))
        result = model.review(draft, self.sources, self.config)
        self.assertEqual(result['body'], '第一句按资料改写在这里。[S1]\n\n第二句改过的内容在这里。[W1]')
        self.assertEqual([s['id'] for s in user_json(transport.payloads[2])['sentences']], ['C1'])
        lines = [n for n in result['quality_notes'] if n.startswith('逐句核对｜')]
        self.assertEqual(len(lines), 2)
        self.assertIn('定向修改后有依据', lines[0])

    def test_an_added_sentence_without_basis_is_removed(self):
        revised = {**self.draft, 'body': '正文[S1]，补充[W1]\n\n据说今年考生人数翻了一倍。[S1]', 'quality_notes': ['ok']}
        model, _ = self.model(chat(revised), answers(self.draft, revised, '无依据'), chat({'fixes': [{'id': 'C1', 'sentence': ''}]}))
        result = model.review(self.draft, self.sources, self.config)
        self.assertNotIn('翻了一倍', result['body'])
        self.assertTrue(any(n.startswith('逐句核对｜删除｜') for n in result['quality_notes']))

    def test_unverifiable_quotes_are_flagged_and_faithful_quotes_pass(self):
        faithful = '有考生说：“最后半小时普遍来不及做完”。[S1]'
        strengthened = '有考生说：“最后半小时基本靠蒙”。[S1]'
        self.assertEqual(unverified_quotes(faithful, self.sources), [])
        self.assertEqual(unverified_quotes(strengthened, self.sources), ['最后半小时基本靠蒙'])
        self.assertEqual(unverified_quotes('这张证的“行情”变了[S1]', self.sources), [])   # short term, not a quote
        model, _ = self.model(chat({**self.draft, 'body': strengthened + '补充[W1]'}))
        draft = model.write(self.sources, self.config)
        self.assertTrue(any('基本靠蒙' in n and '逐字核对' in n for n in draft['review_notes']))
        revised = {**self.draft, 'body': strengthened + '补充[W1]', 'quality_notes': []}
        model, _ = self.model(chat(revised), answers(self.draft, revised))
        reviewed = model.review(self.draft, self.sources, self.config)
        self.assertTrue(any('基本靠蒙' in n for n in reviewed['review_notes']))
        self.assertIn('基本靠蒙', reviewed['body'])   # flagged for a human, not silently rewritten


class AttributionAndTitleTest(unittest.TestCase):
    """Round 3: title, lead and source attribution are facts too (2026-10-20 audit findings)."""

    setUp = StyleToReviewTest.setUp
    model = StyleToReviewTest.model
    TITLE_CHECK = {'sentence': '标题：考完这场试', 'evidence': 'S1：考生反馈', 'action': '保留', 'note': ''}

    def test_prompts_cover_title_lead_attribution_and_attributed_uncertainty(self):
        for phrase in ('标题、导语和正文同样受上述规则约束', '流传较广', '问得最多', '机构基于调查／学员反馈',
                       '不能把作者的话写成考生或机构的话', '“公开”“权威”'):
            self.assertIn(phrase, FIDELITY_RULES)
        for phrase in ('原文作者称', '不要逐段重复“没有统计”“证据不足”', '不影响主线的内容直接删去',
                       '仍要在正文中用一句话说清'):
            self.assertIn(phrase, ARTICLE_VOICE)
        model, transport = self.model(chat({**self.draft, 'quality_notes': [], 'claim_checks': [self.TITLE_CHECK]}))
        model.review(self.draft, self.sources, self.config)
        review = system_text(transport.payloads[0])
        self.assertIn('逐句核对标题、导语', review)
        self.assertIn('不要输出逐句核对表', review)
        self.assertIn('证据只证明来源说了什么，不证明来源被广泛传播或代表多数人', review)

    def test_spread_attention_timing_and_basis_claims_are_flagged(self):
        for sentence in ('这篇文章在备考圈流传，说缺考率很高。', '流传较广的几句是报名的人少了。',
                         '这是考生问得最多的问题之一，大家都关心。', '考试结束第二天，几种说法反复出现在讨论里。',
                         '这是机构根据教学和学员反馈形成的判断。', '只有互相转载的自媒体说法可供参考。',
                         '一篇备考号文章被反复转发，引发讨论。'):
            with self.subTest(sentence=sentence):
                self.assertEqual(unsupported_attributions(sentence + '[S1]', self.sources), [sentence + '[S1]'])
        self.assertEqual(unsupported_attributions('有考生表示，计量科目最后半小时普遍来不及做完。[S1]', self.sources), [])
        sources = [dict(s) for s in self.sources]
        sources[0]['web_research'] += '该文章在多个平台被转载[W2]。'
        self.assertEqual(unsupported_attributions('该文章被多个平台转载，仍需核实原始口径。', sources), [])

    def test_title_is_checked_by_rules_and_a_changed_title_is_numbered(self):
        draft = {**self.draft, 'title': '考生问得最多的三件事'}
        model, _ = self.model(chat({**draft, 'quality_notes': []}))
        result = model.review(draft, self.sources, self.config)
        self.assertTrue(any('传播范围、关注度' in n and '考生问得最多的三件事' in n for n in result['review_notes']))
        # The review renames the title to 考完这场试: the program lists it first, as C1.
        revised = {**self.draft, 'quality_notes': []}
        model, transport = self.model(chat(revised), answers(draft, revised))
        result = model.review(draft, self.sources, self.config)
        self.assertEqual(user_json(transport.payloads[1])['changes'][0], {
            'id': 'C1', 'sentence': '考完这场试', 'draft_sentence': '考生问得最多的三件事'})
        self.assertTrue(result['quality_notes'][0].startswith('逐句核对｜修正｜标题：考完这场试'))

    def test_a_replaced_title_without_basis_goes_back(self):
        draft = {**self.draft, 'title': '考完这场试'}
        revised = {**draft, 'title': '全国考生人数翻倍', 'quality_notes': []}
        model, transport = self.model(chat(revised), answers(draft, revised, '无依据'), chat({'fixes': []}))
        result = model.review(draft, self.sources, self.config)
        self.assertEqual(result['title'], '考完这场试')
        self.assertIn('二审改动无依据，已回退为初稿原句：全国考生人数翻倍', result['quality_notes'])
        self.assertEqual(len(transport.payloads), 3)

    def test_sentences_added_by_the_review_must_be_checked(self):
        draft = {**self.draft, 'body': '有考生反映计量时间紧。[S1]补充成绩时间。[W1]'}
        added = '这是考后讨论里最受关注的话题，几乎人人都在问。[S1]'
        rewritten = '据公开考试观察记录，报名人数减少约两成。[S1]'
        final_body = draft['body'] + rewritten + added
        checks = [self.TITLE_CHECK, {'sentence': '有考生反映计量时间紧。', 'evidence': 'S1：时间紧', 'action': '保留',
                                     'note': ''}]
        self.assertEqual(unchecked_additions(draft['body'], final_body, checks), [rewritten, added])
        covered = checks + [{'sentence': '报名人数减少约两成', 'evidence': 'S1', 'action': '修正',
                             'note': '改为据公开考试观察记录，报名人数减少约两成'}]
        self.assertEqual(unchecked_additions(draft['body'], final_body, covered), [added])
        # The review adds transitions (no facts) and one unsupported claim: the program numbers all
        # of them; transitions may be answered 无需依据, the claim is removed after one fix call.
        filler = ''.join(f'第{n}句新增的普通过渡文字在这里。' for n in '一二三四五六七八')
        revised = {**draft, 'body': draft['body'] + filler + added, 'quality_notes': []}
        changes, _ = changed_sentences(draft, revised)
        self.assertEqual(len(changes), 9)
        reply = {'answers': [{'id': c['id'], 'basis': '无需依据'} for c in changes[:8]] + [{'id': 'C9', 'basis': '无依据'}]}
        model, transport = self.model(chat(revised), chat(reply), chat({'fixes': [{'id': 'C9', 'sentence': ''}]}))
        result = model.review(draft, self.sources, self.config)
        self.assertNotIn('最受关注', result['body'])
        self.assertIn('第八句新增的普通过渡文字在这里。', result['body'])
        self.assertEqual([s['id'] for s in user_json(transport.payloads[2])['sentences']], ['C9'])


class EnginePassesStyleTest(unittest.TestCase):
    def test_single_web_pipeline_uses_style_card_without_extra_style_analysis(self):
        seen = {}

        class Recorder(test_core.FakeModel):
            def analyze_style(self, content, title=''):
                return {'title_pattern': '问题式', 'opening': '场景', 'structure': '三问', 'tone': '克制',
                        'rhythm': '短段', 'ending': '建议'}

            def write(self, sources, config):
                seen['write'] = style_context(sources)
                return {'title': '标题', 'outline': [], 'review_notes': [],
                        'body': '正文' + ''.join(f"[{s['source_id']}]" for s in sources)}

            def review(self, draft, sources, config):
                seen['review'] = style_context(sources)
                return {**draft, 'quality_notes': ['ok'], 'claim_checks': CHECKS}

        class Research:
            def __init__(self, *args):
                pass

            def search(self, article):
                return {'notes': '联网资料[W1]', 'retrieved_at': '2026-10-19', 'sources': [
                    {'source_id': 'W1', 'title': '虚构来源', 'url': 'https://example.org/w1', 'kind': 'web'}]}

        fixture = test_core.CoreTest()
        fixture.setUp()
        try:
            fixture.config.update(writing_mode='single_web', min_sources=1, article_limit=1,
                                  draft_first_mode=False)
            engine = Engine(fixture.db, test_core.FakeVault(), source_factory=test_core.FakeSource,
                            model_factory=Recorder, research_factory=Research)
            self.assertIn(engine.run('quality:style', fixture.config), ('已完成', '部分完成'))
            self.assertIsNone(seen['write'])
            self.assertEqual(seen['review'], seen['write'])
        finally:
            fixture.tearDown()


class CleanExportTest(unittest.TestCase):
    def test_published_text_is_clean_while_internal_mapping_is_kept(self):
        sources = package()['sources']
        body = '据安排，成绩两个月内公布。[W1]有考生反映计量时间紧。[S1]'
        exported = export_document('考完这场试', body, sources)
        self.assertNotRegex(exported, r'\[[SW]\d+\]')
        self.assertIn('## 参考资料网站', exported)
        self.assertIn('https://example.org/fictional-exam-notice', exported)
        # The stored draft keeps the claim-to-source marks for internal review.
        self.assertRegex(body, r'\[W1\]')
        self.assertIn('https://example.org/fictional-exam-notice', source_website_text(body, sources))


class EvaluationScriptTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.local-data' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rules_flag_report_structure_and_not_natural_prose(self):
        bad = evaluate.rule_checks(REPORT_STYLE)
        self.assertGreaterEqual(len(bad['report_style_headings']), 3)
        self.assertTrue(bad['meta_statements'])
        self.assertGreater(bad['list_line_ratio'], 0.4)
        self.assertEqual(bad['internal_citation_marks'], 0)
        good = evaluate.rule_checks(NATURAL)
        self.assertEqual((good['report_style_headings'], good['meta_statements'], good['labelled_list_items']),
                         ([], [], 0))
        self.assertEqual(good['list_line_ratio'], 0)
        self.assertEqual(good['internal_citation_marks'], 3)

    def test_package_is_fictional_consistent_and_validated(self):
        data = evaluate.load_package(PACKAGE)
        self.assertTrue(data['fictional'])
        report = evaluate.load_package(REPORT_PACKAGE)
        self.assertTrue(report['fictional'])
        self.assertIn('不是《稿件-10》的历史输入', report['description'])
        self.assertGreaterEqual(len([s for s in report['sources'] if s['source_id'].startswith('W')]), 4)
        broken = package()
        broken['sources'][0]['web_research'] += '[W7]'
        target = self.path / 'broken.json'
        target.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'W7'):
            evaluate.load_package(target)

    def test_check_writes_rules_and_a_blank_human_sheet(self):
        baseline = self.path / 'baseline.md'
        baseline.write_text(REPORT_STYLE, encoding='utf-8')
        candidate = self.path / 'candidate.md'
        candidate.write_text(NATURAL, encoding='utf-8')
        out = self.path / 'out'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(evaluate.main(['check', '--baseline', str(baseline), '--candidate', str(candidate),
                                            '--package', str(PACKAGE), '--out', str(out)]), 0)
        report = json.loads((out / 'rules.json').read_text(encoding='utf-8'))
        self.assertEqual([f['role'] for f in report['files']], ['baseline', 'candidate'])
        sheet = (out / 'scoring.md').read_text(encoding='utf-8')
        self.assertIn('自然表达（无报告腔）（20）', sheet)
        self.assertIn('| baseline.md |', sheet)
        self.assertNotRegex(sheet, r'\| (?:7[5-9]|[89]\d|100) \|')   # no pre-filled scores
        with self.assertRaisesRegex(ValueError, '哈希'):
            evaluate.main(['check', '--baseline', str(baseline), '--baseline-sha256', '0' * 64,
                           '--out', str(out)])

    def test_baseline_can_be_a_saved_prompt_snapshot(self):
        snapshot = self.path / 'api-snapshot.py'
        snapshot.write_bytes((ROOT / 'wewriter' / 'api.py').read_bytes())
        module = evaluate._load_api('file:' + str(snapshot))
        self.assertTrue(hasattr(module, 'Model') and hasattr(module, 'Transport'))
        self.assertIn('FIDELITY_RULES', vars(module))

    def test_paid_generation_refuses_without_explicit_authorisation(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(evaluate.main(['generate', '--package', str(PACKAGE), '--baseline-rev', '2c94c2a']), 2)
        self.assertIn('--allow-paid-calls', output.getvalue())
        self.assertIn('正常约 12 次模型请求；HTTP 请求（含重试）硬上限 18 次', output.getvalue())
        for limit in ('0', '-1'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(evaluate.main(['generate', '--package', str(PACKAGE), '--max-calls', limit,
                                                '--allow-paid-calls']), 2)
            self.assertIn('必须是正整数', output.getvalue())


FAKE_BEARER = 'FAKE-T008-bearer-0000.dummy'


def fake_api(fail=None, voice=''):
    """A stand-in for wewriter.api: the real Model with a scripted transport, or scripted failures."""
    from wewriter import api as real

    class Transport:
        def request(self, url, key, payload=None, *, timeout=100):
            if fail == 'secret':
                raise real.ApiError('HTTP 500；Authorization: Bearer ' + FAKE_BEARER)
            if fail == 'fatal':
                raise real.ApiError('HTTP 402：API 余额不足', True)
            body = '正文[S1][W1]' + voice
            checks = [dict(CHECKS[0], note='核对 Bearer ' + FAKE_BEARER)]
            return chat({'title': '考完这场试' + voice, 'outline': [], 'review_notes': ['核对 Bearer ' + FAKE_BEARER],
                         'body': body, 'quality_notes': ['ok'], 'claim_checks': checks})

    class Model(real.Model):
        pass
    return type('FakeApi', (), {'Transport': Transport, 'Model': Model, 'ApiError': real.ApiError})


class BlindGenerationTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.local-data' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.out = Path(self.temp.name) / 'run'
        data = package()
        self.package = data
        self.config = defaults()
        self.config.update(data['config'])
        self.config['draft_first_mode'] = False

    def tearDown(self):
        self.temp.cleanup()

    def generate(self, apis, runs=2, max_calls=None):
        variants = [('current', None), ('baseline', 'rev')]
        return evaluate.generate_samples(self.package, self.config, 'deepseek', 'test-only-not-a-key', variants,
                                         runs, max_calls or runs * 2 * 3, self.out,
                                         load_api=lambda revision: apis[revision])

    def test_samples_are_neutral_and_the_mapping_is_sealed(self):
        summary = self.generate({None: fake_api(), 'rev': fake_api(voice='旧')})
        names = sorted(p.name for p in (self.out / 'samples').iterdir())
        self.assertEqual(len(names), 4)
        self.assertTrue(all(re.fullmatch(r'sample-[0-9a-f]{6}\.md', n) for n in names))
        sheet = (self.out / 'scoring.md').read_text(encoding='utf-8')
        public = sheet + (self.out / 'run.json').read_text(encoding='utf-8')
        for word in ('current', 'baseline', 'working-tree', '"rev"'):
            self.assertNotIn(word, public)
        key = json.loads((self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(r['variant'] for r in key.values()), ['baseline', 'baseline', 'current', 'current'])
        self.assertEqual(summary['calls_used'], 8)
        self.assertEqual(json.loads((self.out / 'run.json').read_text(encoding='utf-8'))['config'], self.config)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(evaluate.main(['reveal', '--run', str(self.out)]), 2)
        self.assertIn('还没有总分', output.getvalue())
        self.assertNotIn('current', output.getvalue())

    def test_prompt_hash_follows_the_prompt_actually_sent(self):
        from wewriter import api
        first = self.generate({None: fake_api(), 'rev': fake_api()}, runs=1)
        key1 = json.loads((self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8'))
        original = api.WRITER_RULES
        try:
            api.WRITER_RULES = original + '（改动）'
            self.out = self.out.parent / 'run2'
            self.generate({None: fake_api(), 'rev': fake_api()}, runs=1)
        finally:
            api.WRITER_RULES = original
        key2 = json.loads((self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8'))
        self.assertEqual(first['calls_used'], 4)
        self.assertNotEqual({r['prompt_sha256'] for r in key1.values()}, {r['prompt_sha256'] for r in key2.values()})

    def test_failures_are_redacted_and_consecutive_failures_stop_the_run(self):
        summary = self.generate({None: fake_api(fail='secret'), 'rev': fake_api(fail='secret')})
        sealed = (self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8')
        self.assertNotIn(FAKE_BEARER, sealed + (self.out / 'run.json').read_text(encoding='utf-8'))
        self.assertIn('HTTP 500', sealed)
        self.assertEqual(summary['failed_samples'], 2)
        self.assertIn('连续 2 个样本失败', summary['stop_reason'])

    def test_sealed_record_keeps_redacted_claim_checks_and_the_first_draft(self):
        self.generate({None: fake_api(), 'rev': fake_api(voice='旧')}, runs=1)
        sealed = json.loads((self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8'))
        for record in sealed.values():
            self.assertEqual(record['quality_notes'], ['ok'])       # nothing changed: no draft → revision lines
            self.assertIn('正文[S1][W1]', record['draft']['body'])
            self.assertIn('考完这场试', record['draft']['title'])
            self.assertTrue(record['draft']['review_notes'])
        text = json.dumps(sealed, ensure_ascii=False)
        self.assertNotIn(FAKE_BEARER, text)
        public = (self.out / 'scoring.md').read_text(encoding='utf-8') + (self.out / 'run.json').read_text(encoding='utf-8')
        public += ''.join(p.read_text(encoding='utf-8') for p in (self.out / 'samples').iterdir())
        self.assertNotIn('逐句核对', public)

    def test_notes_are_redacted_and_fatal_errors_and_call_limit_stop(self):
        self.generate({None: fake_api(), 'rev': fake_api()}, runs=1)
        self.assertNotIn(FAKE_BEARER, (self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8'))
        self.out = self.out.parent / 'fatal'
        summary = self.generate({None: fake_api(fail='fatal'), 'rev': fake_api(fail='fatal')})
        self.assertEqual(summary['failed_samples'], 1)
        self.assertIn('余额不足', summary['stop_reason'])
        self.out = self.out.parent / 'limit'
        summary = self.generate({None: fake_api(), 'rev': fake_api()}, runs=2, max_calls=3)
        self.assertEqual(summary['calls_used'], 3)
        self.assertEqual(len(summary['samples']), 1)
        self.assertIn('HTTP 请求上限 3 次', summary['stop_reason'])


def http_api(responses):
    """The real Transport and Model, with an opener that plays back HTTP results offline."""
    import email.message
    import urllib.error
    from wewriter import api as real
    attempts = []

    class Opener:
        def open(self, request, timeout=None):
            attempts.append(request.full_url)
            status = responses.pop(0) if responses else 200
            if status != 200:
                headers = email.message.Message()
                headers['Retry-After'] = '0'
                raise urllib.error.HTTPError(request.full_url, status, 'unavailable', headers, io.BytesIO(b''))
            body = {'title': '考完这场试', 'outline': [], 'review_notes': [], 'body': '正文[S1][W1]',
                    'quality_notes': ['ok'], 'claim_checks': CHECKS}
            return io.BytesIO(json.dumps(chat(body), ensure_ascii=False).encode('utf-8'))

    class Transport(real.Transport):
        def __init__(self):
            super().__init__()
            self.opener = Opener()
    return type('HttpApi', (), {'Transport': Transport, 'Model': real.Model}), attempts


class HttpBudgetTest(unittest.TestCase):
    setUp = BlindGenerationTest.setUp
    tearDown = BlindGenerationTest.tearDown

    def run_one(self, responses, max_calls, variants=(('current', None),)):
        api, attempts = http_api(responses)
        summary = evaluate.generate_samples(self.package, self.config, 'deepseek', 'test-only-not-a-key',
                                            list(variants), 1, max_calls, self.out,
                                            load_api=lambda revision: api)
        return summary, attempts

    def test_limit_of_one_blocks_the_retry_after_a_503(self):
        summary, attempts = self.run_one([503, 503], max_calls=1)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(summary['calls_used'], 1)
        self.assertIn('HTTP 请求上限 1 次', summary['stop_reason'])
        record = next(iter(json.loads((self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8')).values()))
        self.assertEqual(record['http_requests'], 1)

    def test_transport_retries_are_charged_for_every_variant(self):
        # Two 503s then success for the write, then the review: 4 HTTP requests for 2 model requests.
        summary, attempts = self.run_one([503, 503], max_calls=10,
                                         variants=(('current', None), ('baseline', 'rev')))
        record_list = list(json.loads((self.out / 'sealed' / 'blind-key.json').read_text(encoding='utf-8')).values())
        self.assertEqual(summary['calls_used'], len(attempts))
        self.assertEqual(sum(r['http_requests'] for r in record_list), len(attempts))
        self.assertEqual(sorted(r['http_requests'] for r in record_list), [2, 4])
        self.assertEqual([r['api_calls'] for r in record_list], [2, 2])

    def test_zero_or_negative_limits_are_rejected(self):
        for limit in (0, -3):
            with self.assertRaisesRegex(ValueError, '正整数'):
                self.run_one([], max_calls=limit)


if __name__ == '__main__':
    unittest.main()
