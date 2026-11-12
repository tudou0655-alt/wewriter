"""MVP delivery regressions using fictional evidence and offline models only."""
import json
import unittest
from types import SimpleNamespace

from wewriter.api import ApiError, Model, QUOTE_NOTE, undo_retelling, unverified_quotes
from wewriter.pipeline.composition import compose, length_warning, report
from wewriter.style import fact_sheet
from test_editorial_plan import ARGUMENT, SOURCES


class MvpQualityTest(unittest.TestCase):
    def model(self, response):
        model = Model.__new__(Model)
        model.complete = lambda *_args, **_kwargs: response
        return model

    def test_length_boundaries_include_both_ends(self):
        for size, flagged in ((1199, True), (1200, False), (1800, False), (1801, True)):
            with self.subTest(size=size):
                self.assertEqual(bool(length_warning({'body': '甲' * size + '[S1]'},
                                                     {'target_words': 1500})), flagged)

    def test_fact_fix_is_not_reverted_for_retelling_wording(self):
        old = {'title': '虚构补贴', 'body': '甲区每家补贴120万元。[S1]'}
        new = {'title': '虚构补贴', 'body': '行业材料称，甲区每家补贴100万元。[S1]'}
        output, notes = undo_retelling(old, new)
        self.assertIn('100万元', output['body'])
        self.assertNotIn('120万元', output['body'])
        self.assertTrue(notes)

    def test_quote_found_in_original_excerpt_is_not_a_warning(self):
        quote = '企业先完成线上填报后再提交'
        sources = [{'source_id': 'S1', 'excerpt': quote}]
        self.assertEqual(unverified_quotes(f'“{quote}”', sources), [])
        self.assertTrue(unverified_quotes('“企业必须先取得特别许可”', sources))

    def test_prefixed_search_result_keeps_all_sentences_without_crossing_sources(self):
        sources = [dict(SOURCES[0], web_research='[W1] 甲类仅限试点区。乙类需提交申请。（另有网页正文节选）\n'
                                              '[W2] 丙类采用另一流程。'), SOURCES[1],
                   {'source_id': 'W2', 'url': 'https://example.org/other', 'title': '虚构另一页'}]
        facts = fact_sheet(sources)
        second = next(f for f in facts if '乙类需提交' in f['fact'])
        third = next(f for f in facts if '丙类采用' in f['fact'])
        self.assertEqual(second['sources'], ['W1'])
        self.assertEqual(third['sources'], ['W2'])
        self.assertFalse(any('网页正文节选' in fact['fact'] for fact in facts))
        self.assertNotIn('只能写', facts[0]['scope'])

    def test_mixed_source_line_does_not_guess_uncited_sentence_source(self):
        sources = [dict(SOURCES[0], web_research='[W1] 甲类限试点区。乙类另有条件。[W2] 无归属句。'),
                   SOURCES[1], {'source_id': 'W2', 'url': 'https://example.org/other'}]
        self.assertFalse(any('无归属句' in fact['fact'] for fact in fact_sheet(sources)))

    def test_planning_keeps_valid_steps_but_does_not_invent_missing_premises(self):
        response = {'status': 'ready', 'question': '虚构问题', 'angle': '虚构主线',
                    'fact_ids': ['F1', 'F3'], 'sections': {},
                    'argument': [ARGUMENT[0], {**ARGUMENT[0], 'premises': ['F99', 'F1']}]}
        plan = self.model(response).plan_article(SOURCES, {})
        self.assertEqual(plan['argument'], ARGUMENT)
        self.assertTrue(plan['normalization_notes'])
        with self.assertRaises(ApiError):
            self.model({**response, 'fact_ids': ['F3']}).plan_article(SOURCES, {})

    def test_theory_keeps_valid_boundaries_with_incomplete_warning(self):
        result = self.model({'status': 'ready', 'boundaries': [
            {'text': '虚构限定', 'fact_ids': ['F3']}, {'text': '无据限定', 'fact_ids': ['F99']}, None
        ]}).review_theory({'fact_ids': ['F1', 'F3'], 'argument': ARGUMENT}, SOURCES)
        self.assertEqual(result['boundaries'], [{'text': '虚构限定', 'fact_ids': ['F3']}])
        self.assertTrue(result['normalization_notes'])

    def test_partial_claim_audit_preserves_findings_and_marks_incomplete(self):
        draft = {'title': '虚构标题', 'body': '甲类可以申报。[W1]'}
        issues = self.model({'unsupported': [
            {'sentence': '甲类可以申报。 [W1]', 'reason': '只支持乙类'},
            {'sentence': '不存在的句子', 'reason': '无法定位'}, {}
        ]}).audit_claims(draft, SOURCES)
        self.assertEqual(issues[0]['sentence'], draft['body'])
        self.assertTrue(any('不完整' in item['reason'] for item in issues))

    def test_claim_location_does_not_equate_different_decimal_numbers(self):
        with self.assertRaisesRegex(ApiError, '可定位'):
            self.model({'unsupported': [{'sentence': '虚构补贴为12万元。[W1]', 'reason': '金额有误'}]}).audit_claims(
                {'title': '虚构标题', 'body': '虚构补贴为1.2万元。[W1]'}, SOURCES)

    def test_expansion_keeps_good_paragraph_when_other_paragraph_is_invalid(self):
        draft = {'title': '虚构标题', 'body': '原有正文。[S1]', 'outline': [], 'review_notes': []}
        response = {'additions': [
            {'after_paragraph': 0, 'fact_ids': ['F1'], 'text': '补充虚构说明。'},
            {'after_paragraph': 0, 'fact_ids': [{}], 'text': '无效段落。'},
            {'after_paragraph': 0, 'fact_ids': ['F1'], 'text': '乙' * 351}]}
        output = self.model(response).expand(draft, SOURCES, {'target_words': 1500})
        self.assertIn('原有正文。[S1]', output['body'])
        self.assertIn('补充虚构说明。[S1]', output['body'])
        self.assertNotIn('无效段落', output['body'])
        self.assertTrue(any('2 个' in note for note in output['review_notes']))
        self.assertIs(self.model({'additions': []}).expand(draft, SOURCES, {'target_words': 1500}), draft)

    def test_writer_gets_all_original_facts_and_only_selected_web_facts(self):
        captured = []
        model = self.model(None)
        def complete(_system, user, **_kwargs):
            captured.append(json.loads(user))
            raise ApiError('capture', kind='validation')
        model.complete = complete
        with self.assertRaises(ApiError):
            model._write_once(SOURCES, {'writing_mode': 'single_web', 'topic': '', 'target_words': 1500,
                                       'draft_first_mode': True, 'editorial_plan': {'fact_ids': ['F1', 'F3']}})
        self.assertEqual([f['id'] for f in captured[0]['facts']], ['F1', 'F2', 'F3'])

    def run_composition(self, failure=None, body=None, sources=None):
        class Writer(Model):
            def __init__(self):
                pass

            def plan_article(self, *_args):
                return {'status': 'ready', 'question': '虚构问题', 'angle': '虚构角度',
                        'fact_ids': ['F1', 'F3'], 'argument': ARGUMENT}

            def review_logic(self, *_args):
                if failure == 'logic':
                    raise ApiError('格式不完整', kind='validation')
                return {'status': 'ready'}

            def review_theory(self, *_args):
                if failure == 'domain':
                    raise ApiError('格式不完整', kind='validation')
                return {'status': 'ready', 'boundaries': []}

            def write(self, *_args):
                return {'title': '虚构标题', 'body': body or ('甲' * 1300 + '[S1][W1]'),
                        'outline': [], 'review_notes': [QUOTE_NOTE + '旧提示']}

            def expand(self, *_args):
                raise AssertionError('adequate or long draft must not expand')

            def audit_claims(self, *_args):
                return []

        warnings = []
        run = SimpleNamespace(writer=Writer(), job={'id': 1},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True,
                                      'target_words': 1500}, stage=lambda *_args: None,
                              cached=lambda _id, _key, compute: compute(), warn=warnings.append)
        engine = SimpleNamespace(check=lambda: None)
        output, _ = compose(engine, run, sources or SOURCES)
        return output, warnings

    def test_invalid_optional_review_does_not_discard_draft(self):
        for failure in ('logic', 'domain'):
            with self.subTest(failure=failure):
                output, warnings = self.run_composition(failure)
                self.assertEqual(output['title'], '虚构标题')
                self.assertTrue(any('待审核草稿' in n for n in warnings))

    def test_final_quote_warnings_are_recomputed_and_long_drafts_not_expanded(self):
        sources = [dict(SOURCES[0], excerpt='企业先完成线上填报后再提交'), SOURCES[1]]
        output, warnings = self.run_composition(body='甲' * 1850 + '“企业先完成线上填报后再提交”[S1][W1]',
                                               sources=sources)
        self.assertFalse(any(note.startswith(QUOTE_NOTE) for note in output['review_notes']))
        self.assertFalse(warnings)
        self.assertTrue(length_warning(output, {'target_words': 1500}))

    def test_missing_original_evidence_is_visible_even_when_audit_reports_no_issue(self):
        _, warnings = self.run_composition()
        self.assertTrue(any('缺少原文片段：S1' in n for n in warnings))

    def test_cleanup_cannot_save_an_empty_or_citation_only_draft(self):
        for body in ('[F1]', '[S99]', '[S1]', '## 参考资料\n来源列表'):
            with self.subTest(body=body), self.assertRaisesRegex(ApiError, '缺少正文'):
                Model._finish_draft({'title': '虚构标题', 'body': body, 'outline': [], 'review_notes': []},
                                    SOURCES, {'draft_first_mode': True}, soft=True)

    def test_default_report_retains_verification_todos(self):
        output = {'outline': [], 'review_notes': [], 'verify_items': [{'claim': '虚构金额需核对'}]}
        source = {'source_id': 'S1', 'title': '虚构资料', 'account_name': '虚构机构',
                  'url': 'https://example.org', 'analysis': []}
        text = report({'draft_first_mode': True}, [source], output, [], None, False)
        self.assertIn('## 待核实事项\n- 虚构金额需核对', text)


if __name__ == '__main__':
    unittest.main()
