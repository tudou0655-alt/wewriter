"""Evidence planning stays separate from prose generation (fictional offline data)."""
import unittest
from types import SimpleNamespace

from wewriter.api import ApiError, Model
from wewriter.pipeline.common import ErrorCode, TaskError
from wewriter.pipeline.composition import compose


SOURCES = [
    {'article_id': 1, 'source_id': 'S1', 'title': '虚构行业材料',
     'analysis': [{'facts': [{'claim': '行业材料提到示例计划', 'quote': '示例计划'},
                             {'claim': '材料列出两个对象', 'quote': '两个对象'}], 'uncertainties': []}],
     'web_research': '虚构部门文件列出甲类对象。[W1] 虚构部门文件说明乙类条件。[W1]'},
    {'source_id': 'W1', 'title': '虚构官方文件', 'url': 'https://example.gov.cn/fiction',
     'excerpt': '虚构部门文件列出甲类对象。虚构部门文件说明乙类条件。'},
]
ARGUMENT = [{'premises': ['F1', 'F3'], 'conclusion': '先核对虚构条件',
             'link': '由两项事实推出核对顺序'}]


class EditorialPlanTest(unittest.TestCase):
    def model(self, response):
        model = Model.__new__(Model)
        model.complete = lambda *_args, **_kwargs: response
        return model

    def test_ready_plan_uses_known_original_and_official_facts(self):
        result = self.model({'status': 'ready', 'question': '甲类企业该看什么？', 'angle': '核对条件',
                             'fact_ids': ['F1', 'F2', 'F3', 'F4'], 'sections': ['条件', '行动'],
                             'argument': ARGUMENT}).plan_article(
                                 SOURCES, {})
        self.assertEqual(result['fact_ids'], ['F1', 'F2', 'F3', 'F4'])
        self.assertEqual(result['argument'], ARGUMENT)

    def test_unambiguous_web_source_id_maps_to_its_fact(self):
        sources = [dict(SOURCES[0], web_research='虚构部门文件说明一项条件。[W1]'), SOURCES[1]]
        response = {'status': 'ready', 'question': '甲类企业该看什么？', 'angle': '核对条件',
                    'fact_ids': ['F1', 'F2', 'W1'], 'sections': ['条件'],
                    'argument': [{**ARGUMENT[0], 'premises': ['F1', 'W1']}]}
        plan = self.model(response).plan_article(sources, {})
        self.assertEqual(plan['fact_ids'], ['F1', 'F2', 'F3'])
        self.assertEqual(plan['argument'][0]['premises'], ['F1', 'F3'])

    def test_plan_preserves_valid_facts_and_ignores_unused_sections(self):
        result = self.model({'status': 'ready', 'question': '问题', 'angle': '主线',
                             'fact_ids': ['F1', 'F2', 'F3', 'F99', 'F1', {}],
                             'sections': None, 'argument': ARGUMENT}).plan_article(SOURCES, {})
        self.assertEqual(result['fact_ids'], ['F1', 'F2', 'F3'])
        self.assertEqual(result['sections'], [])
        self.assertTrue(result['normalization_notes'])

    def test_normal_web_plan_needs_a_selected_web_fact(self):
        response = {'status': 'ready', 'question': '验收条件是什么？', 'angle': '核对条件',
                    'fact_ids': ['F1', 'F2', 'F3'], 'sections': ['条件'], 'argument': ARGUMENT}
        only_original = [dict(SOURCES[0], web_research='', analysis=[{
            'facts': [*SOURCES[0]['analysis'][0]['facts'],
                      {'claim': '第三条虚构事实', 'quote': '第三条原文'}], 'uncertainties': []}])]
        with self.assertRaisesRegex(ApiError, '可核验的事实主线'):
            self.model(response).plan_article(only_original, {})
        self.assertEqual(self.model(response).plan_article(
            only_original, {'research_degraded': True})['status'], 'ready')

    def test_plan_can_compare_web_page_text_with_claim(self):
        import json
        model = Model.__new__(Model)
        captured = []
        def capture(_system, user, **_kwargs):
            captured.append(json.loads(user))
            raise ApiError('stop after request', kind='validation')
        model.complete = capture
        with self.assertRaises(ApiError):
            model.plan_article([dict(SOURCES[0], excerpt='公众号原文'),
                                dict(SOURCES[1], excerpt='同地区官方原件')], {'topic': '虚构指定问题'})
        self.assertEqual(captured[0]['sources'][1]['excerpt'], '同地区官方原件')
        self.assertEqual(captured[0]['topic'], '虚构指定问题')

    def test_logic_plan_rejects_broken_argument(self):
        base = {'status': 'ready', 'question': '问题', 'angle': '主线',
                'fact_ids': ['F1', 'F2', 'F3'], 'sections': ['推进']}
        for argument in (None, [], [{'premises': ['F99'], 'conclusion': '结论', 'link': '依据'}],
                         [{'premises': ['F1'], 'conclusion': '', 'link': '依据'}]):
            with self.subTest(argument=argument), self.assertRaisesRegex(ApiError, '事实主线'):
                self.model({**base, 'argument': argument}).plan_article(SOURCES, {})

    def test_theory_review_uses_selected_facts_and_located_boundaries(self):
        import json
        captured = []
        model = Model.__new__(Model)
        def complete(_system, user, **_kwargs):
            captured.append(json.loads(user))
            return {'status': 'ready', 'boundaries': [
                {'text': '甲类仅限试点区', 'fact_ids': ['F3']}]}
        model.complete = complete
        plan = {'fact_ids': ['F1', 'F3'], 'argument': ARGUMENT}
        sources = [dict(SOURCES[0], excerpt='虚构原文'),
                   dict(SOURCES[1], excerpt='甲类仅限试点区'),
                   {'source_id': 'W2', 'excerpt': '无关网页'}]
        self.assertEqual(model.review_theory(plan, sources)['boundaries'][0]['fact_ids'], ['F3'])
        model.complete = lambda *_args, **_kwargs: {'status': 'ready', 'boundaries': [
            {'text': f'限定{i}', 'fact_ids': ['F3']} for i in range(8)]}
        self.assertEqual(len(model.review_theory(plan, sources)['boundaries']), 8)
        self.assertEqual([s['source_id'] for s in captured[0]['sources']], ['S1', 'W1'])
        model.complete = lambda *_args, **_kwargs: {'status': 'ready', 'boundaries': [
            {'text': '没有依据', 'fact_ids': ['F99']}]}
        result = model.review_theory(plan, sources)
        self.assertEqual(result['boundaries'], [])
        self.assertTrue(result['normalization_notes'])

    def test_logic_review_sees_argument_without_source_text(self):
        import json
        captured = []
        model = Model.__new__(Model)
        def complete(_system, user, **_kwargs):
            captured.append(json.loads(user))
            return {'status': 'skip', 'reason': '结论不能由前提推出'}
        model.complete = complete
        plan = {'question': '问题', 'angle': '主线', 'fact_ids': ['F1', 'F3'],
                'argument': ARGUMENT}
        self.assertEqual(model.review_logic(plan, SOURCES)['status'], 'skip')
        self.assertNotIn('sources', captured[0])
        self.assertNotIn('excerpt', json.dumps(captured[0], ensure_ascii=False))
        model.complete = lambda *_args, **_kwargs: {'status': 'unknown'}
        with self.assertRaisesRegex(ApiError, '逻辑核对'):
            model.review_logic(plan, SOURCES)

    def test_logic_then_theory_then_language_and_retry_reuses_stages(self):
        calls, cache = [], {}
        class Writer:
            def plan_article(self, *_args):
                calls.append('logic')
                return {'status': 'ready', 'question': '问题', 'angle': '主线',
                        'fact_ids': ['F1', 'F3'], 'sections': ['推进'], 'argument': ARGUMENT}
            def review_logic(self, *_args):
                calls.append('logic_review')
                return {'status': 'ready', 'reason': ''}
            def review_theory(self, *_args):
                calls.append('theory')
                return {'status': 'ready', 'boundaries': [{'text': '甲类有限定', 'fact_ids': ['F3']}]}
            def write(self, _sources, config):
                calls.append('language')
                self.assert_plan = config['editorial_plan']
                raise RuntimeError('stop at language')
        writer = Writer()
        def cached(_article_id, key, compute):
            if key not in cache:
                cache[key] = compute()
            return cache[key]
        run = SimpleNamespace(writer=writer, job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': False},
                              stage=lambda *_args: None, cached=cached)
        engine = SimpleNamespace(check=lambda: None)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'stop at language'):
                compose(engine, run, SOURCES)
        self.assertEqual(calls, ['logic', 'logic_review', 'theory', 'language', 'language'])
        self.assertEqual(writer.assert_plan['domain']['boundaries'][0]['text'], '甲类有限定')

    def test_logic_skip_never_calls_theory_or_language(self):
        class Writer:
            def plan_article(self, *_args):
                return {'status': 'ready', 'question': '问题', 'angle': '主线',
                        'fact_ids': ['F1', 'F3'], 'sections': ['推进'], 'argument': ARGUMENT}
            def review_logic(self, *_args):
                return {'status': 'skip', 'reason': '前提推不出结论'}
            def review_theory(self, *_args):
                raise AssertionError('must not review theory after logic skip')
            def write(self, *_args):
                raise AssertionError('must not write after logic skip')
        run = SimpleNamespace(writer=Writer(), job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True},
                              stage=lambda *_args: None, cached=lambda _id, _key, compute: compute())
        with self.assertRaises(TaskError) as found:
            compose(SimpleNamespace(check=lambda: None), run, SOURCES)
        self.assertEqual(found.exception.code, ErrorCode.MATERIAL)

    def test_logic_gets_one_cached_replan_before_theory(self):
        calls, cache = [], {}
        class Writer:
            def plan_article(self, _sources, config):
                calls.append(('plan', config.get('planning_feedback')))
                argument = dict(ARGUMENT[0])
                if config.get('planning_feedback'):
                    argument['conclusion'] = '已修正'
                return {'status': 'ready', 'question': '问题', 'angle': '主线',
                        'fact_ids': ['F1', 'F3'], 'sections': ['推进'], 'argument': [argument]}
            def review_logic(self, plan, _sources):
                calls.append(('logic', plan['argument'][0]['conclusion']))
                return {'status': 'ready' if plan['argument'][0]['conclusion'] == '已修正' else 'skip',
                        'reason': '前提编号不对'}
            def review_theory(self, *_args):
                calls.append(('theory', None))
                return {'status': 'ready', 'boundaries': []}
            def write(self, *_args):
                calls.append(('write', None))
                raise RuntimeError('stop at language')
        def cached(_id, key, compute):
            if key not in cache:
                cache[key] = compute()
            return cache[key]
        run = SimpleNamespace(writer=Writer(), job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True},
                              stage=lambda *_args: None, cached=cached)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'stop at language'):
                compose(SimpleNamespace(check=lambda: None), run, SOURCES)
        self.assertEqual(calls, [('plan', None), ('logic', ARGUMENT[0]['conclusion']),
                                 ('plan', '前提编号不对'), ('logic', '已修正'),
                                 ('theory', None), ('write', None), ('write', None)])

    def test_theory_skip_never_calls_language(self):
        class Writer:
            def plan_article(self, *_args):
                return {'status': 'ready', 'question': '问题', 'angle': '主线',
                        'fact_ids': ['F1', 'F3'], 'sections': ['推进'], 'argument': ARGUMENT}
            def review_logic(self, *_args):
                return {'status': 'ready', 'reason': ''}
            def review_theory(self, *_args):
                return {'status': 'skip', 'reason': '概念适用对象不清'}
            def write(self, *_args):
                raise AssertionError('must not write after theory skip')
        run = SimpleNamespace(writer=Writer(), job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True},
                              stage=lambda *_args: None, cached=lambda _id, _key, compute: compute())
        with self.assertRaises(TaskError) as found:
            compose(SimpleNamespace(check=lambda: None), run, SOURCES)
        self.assertEqual(found.exception.code, ErrorCode.MATERIAL)

    def test_claim_audit_requires_locatable_sentences_and_cited_evidence(self):
        import json
        model = Model.__new__(Model)
        captured = []
        draft = {'title': '虚构标题', 'body': '甲类企业可以申报。[W1]'}
        sources = [dict(SOURCES[0], excerpt='不相关公众号原文'),
                   dict(SOURCES[1], excerpt='虚构文件只允许乙类申报'),
                   {'source_id': 'W2', 'excerpt': '无关网页'}]
        def complete(_system, user, **_kwargs):
            captured.append(json.loads(user))
            return {'unsupported': [{'sentence': '甲类企业可以申报。[W1]',
                                     'reason': '官方原件只支持乙类'}]}
        model.complete = complete
        self.assertEqual(len(model.audit_claims(draft, sources)), 1)
        self.assertEqual([s['source_id'] for s in captured[0]['evidence']], ['W1'])
        self.assertIn('today_china', captured[0])
        model.complete = lambda *_args, **_kwargs: {'unsupported': [{'sentence': '不存在的句子', 'reason': '无'}]}
        with self.assertRaisesRegex(ApiError, '可定位'):
            model.audit_claims(draft, sources)

    def test_claim_repair_changes_only_located_sentence_and_validates_model_output(self):
        model = Model.__new__(Model)
        sources = [dict(SOURCES[0], excerpt='虚构原文'),
                   dict(SOURCES[1], excerpt='甲类只允许在试点区申报，比例为10%')]
        draft = {'title': '虚构标题', 'body': '引言。[S1]\n\n甲类都可以申报。[W1]\n\n结尾不变。',
                 'outline': ['试点'], 'review_notes': []}
        issues = [{'sentence': '甲类都可以申报。[W1]', 'reason': '范围扩大'}]
        model.complete = lambda *_args, **_kwargs: {
            'replacements': [{'id': 1, 'replacement': '甲类只可在试点区申报。[W1]'}]}
        revised = model.repair_claims(draft, sources, issues, {'writing_mode': 'single_web'})
        self.assertEqual(revised['body'], '引言。[S1]\n\n甲类只可在试点区申报。[W1]\n\n结尾不变。')
        self.assertEqual(draft['body'], '引言。[S1]\n\n甲类都可以申报。[W1]\n\n结尾不变。')
        import json
        seen = []
        wrong_cite = {**draft, 'body': '甲类只在试点区申报。[W1]\n\n行业材料认为应先梳理数据。[W1]'}
        sources[0]['excerpt'] = '行业材料认为应先梳理数据。'
        def correct(_system, user, **_kwargs):
            seen.append(json.loads(user))
            return {'replacements': [{'id': 1, 'replacement': '行业材料认为应先梳理数据。[S1]'}]}
        model.complete = correct
        corrected = model.repair_claims(wrong_cite, sources,
                                        [{'sentence': '行业材料认为应先梳理数据。[W1]',
                                          'reason': '错把行业观点标成官方来源'}],
                                        {'writing_mode': 'single_web'})
        self.assertIn('行业材料认为应先梳理数据。[S1]', corrected['body'])
        self.assertEqual([item['source_id'] for item in seen[0]['evidence']], ['S1', 'W1'])
        self.assertTrue(any(note.startswith('【事实核查】定向修订更换了来源编号')
                            for note in corrected['review_notes']))
        self.assertEqual(wrong_cite['review_notes'], [])
        for bad in ({'replacements': [{'id': [], 'replacement': '无效'}]},
                    {'replacements': [{'id': 1, 'replacement': '甲类允许申报。[W2]'}]},
                    {'replacements': [{'id': 1, 'replacement': '甲类可在99个试点申报。[W1]'}]},
                    {'replacements': [{'id': 1, 'replacement': '甲类可以申报。\n新增段落。[W1]'}]}):
            with self.subTest(bad=bad), self.assertRaises(ApiError):
                model.complete = lambda *_args, value=bad, **_kwargs: value
                model.repair_claims(draft, sources, issues, {'writing_mode': 'single_web'})
        with self.assertRaisesRegex(ApiError, '唯一定位'):
            model.repair_claims(draft, sources, issues * 2, {'writing_mode': 'single_web'})
        model.complete = lambda *_args, **_kwargs: {'replacements': [
            {'id': 1, 'replacement': '甲类只可在试点区申报。[W1]'},
            {'id': 2, 'replacement': '甲类只可在试点区申报。'}]}
        with self.assertRaisesRegex(ApiError, '失去唯一位置'):
            model.repair_claims(draft, sources,
                                [*issues, {'sentence': '甲类都可以申报。', 'reason': '相同范围问题'}],
                                {'writing_mode': 'single_web'})

    def test_draft_first_claim_audit_flags_issue_without_losing_draft(self):
        warnings = []
        class Writer(Model):
            def __init__(self):
                pass
            def plan_article(self, *_args):
                return {'status': 'ready', 'question': '虚构问题', 'angle': '虚构角度',
                        'fact_ids': ['F1', 'F2', 'F3'], 'sections': ['一']}
            def write(self, *_args):
                return {'title': '虚构标题', 'body': '甲类企业可以申报。[W1]' + '甲' * 30,
                        'outline': ['一'], 'review_notes': []}
            def audit_claims(self, *_args):
                return [{'sentence': '甲类企业可以申报。[W1]', 'reason': '官方只允许乙类'}]
            def repair_claims(self, *_args):
                raise ApiError('离线模拟修订失败', kind='validation')
        run = SimpleNamespace(writer=Writer(), job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True,
                                      'target_words': 20}, stage=lambda *_args: None,
                              cached=lambda _id, _key, compute: compute(), warn=warnings.append)
        engine = SimpleNamespace(db=SimpleNamespace(rows=lambda *_args: [{'content': '虚构原文'}]),
                                 check=lambda: None)
        output, _notes = compose(engine, run, SOURCES)
        self.assertIn('甲类企业可以申报', output['body'])
        self.assertTrue(any(note.startswith('【事实核查】') for note in output['review_notes']))
        self.assertEqual(len(warnings), 1)
        self.assertIn('标出 1 处疑点', run.verified['report'])

    def test_claim_repair_accepts_only_second_audit_clearance(self):
        warnings = []
        class Writer(Model):
            def __init__(self):
                self.audits = 0
            def plan_article(self, *_args):
                return {'status': 'ready', 'question': '问题', 'angle': '角度',
                        'fact_ids': ['F1', 'F2', 'F3'], 'sections': ['一']}
            def write(self, *_args):
                return {'title': '虚构标题', 'body': '甲类企业可以申报。[W1]' + '甲' * 30,
                        'outline': ['一'], 'review_notes': []}
            def audit_claims(self, *_args):
                self.audits += 1
                return ([{'sentence': '甲类企业可以申报。[W1]', 'reason': '官方只允许乙类'}]
                        if self.audits == 1 else [])
            def repair_claims(self, draft, *_args):
                return {**draft, 'body': draft['body'].replace('甲类企业可以申报。', '乙类企业可以申报。')}
        writer = Writer()
        run = SimpleNamespace(writer=writer, job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True,
                                      'target_words': 20}, stage=lambda *_args: None,
                              cached=lambda _id, _key, compute: compute(), warn=warnings.append)
        engine = SimpleNamespace(db=SimpleNamespace(rows=lambda *_args: [{'content': '虚构原文'}]),
                                 check=lambda: None)
        output, _notes = compose(engine, run, SOURCES)
        self.assertIn('乙类企业', output['body'])
        self.assertEqual(writer.audits, 2)
        self.assertEqual(warnings, [])
        self.assertIn('定向修订后模型核查报 0 处疑点', run.verified['report'])

    def test_claim_repair_keeps_original_when_second_audit_still_finds_issue(self):
        warnings = []
        class Writer(Model):
            def __init__(self):
                pass
            def plan_article(self, *_args):
                return {'status': 'ready', 'question': '问题', 'angle': '角度',
                        'fact_ids': ['F1', 'F2', 'F3'], 'sections': ['一']}
            def write(self, *_args):
                return {'title': '虚构标题', 'body': '甲类企业可以申报。[W1]' + '甲' * 30,
                        'outline': ['一'], 'review_notes': []}
            def audit_claims(self, draft, *_args):
                return [{'sentence': '乙类企业可以申报。[W1]' if '乙类企业' in draft['body'] else
                         '甲类企业可以申报。[W1]', 'reason': '来源仍不支持'}]
            def repair_claims(self, draft, *_args):
                return {**draft, 'body': draft['body'].replace('甲类企业', '乙类企业')}
        run = SimpleNamespace(writer=Writer(), job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True,
                                      'target_words': 20}, stage=lambda *_args: None,
                              cached=lambda _id, _key, compute: compute(), warn=warnings.append)
        engine = SimpleNamespace(db=SimpleNamespace(rows=lambda *_args: [{'content': '虚构原文'}]),
                                 check=lambda: None)
        output, _notes = compose(engine, run, SOURCES)
        self.assertIn('甲类企业', output['body'])
        self.assertIn('定向修订后仍有事实疑点，保留初稿', run.verified['report'])
        self.assertEqual(len(warnings), 1)

    def test_skip_stops_before_writing_and_allows_next_candidate(self):
        class Writer:
            def plan_article(self, *_args):
                return {'status': 'skip', 'reason': '虚构政策缺少官方原件'}

            def write(self, *_args):
                raise AssertionError('must not pay to write unsupported topic')

        for draft_first in (False, True):
            with self.subTest(draft_first=draft_first):
                run = SimpleNamespace(writer=Writer(), job={'id': 3},
                                      config={'writing_mode': 'single_web', 'draft_first_mode': draft_first},
                                      stage=lambda *_args: None, cached=lambda _id, _key, compute: compute())
                with self.assertRaises(TaskError) as found:
                    compose(SimpleNamespace(check=lambda: None), run, SOURCES)
                self.assertEqual(found.exception.code, ErrorCode.MATERIAL)

    def test_invalid_plan_skips_candidate_without_draft_call(self):
        class Writer:
            def plan_article(self, *_args):
                raise ApiError('JSON 字段不完整', kind='validation')

            def write(self, *_args):
                raise AssertionError('must not draft after invalid plan')

        run = SimpleNamespace(writer=Writer(), job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': False},
                              stage=lambda *_args: None, cached=lambda _id, _key, compute: compute())
        with self.assertRaises(TaskError) as found:
            compose(SimpleNamespace(check=lambda: None), run, SOURCES)
        self.assertEqual(found.exception.code, ErrorCode.MATERIAL)

    def test_source_marks_stay_in_body_not_public_title(self):
        draft = {'title': '虚构申报通知 [W1]', 'body': '正文。[S1][W1]',
                 'outline': ['条件 [W1]'], 'review_notes': []}
        done = Model._finish_draft(draft, SOURCES, {'writing_mode': 'single_web'})
        self.assertEqual(done['title'], '虚构申报通知')
        self.assertEqual(done['outline'], ['条件'])
        self.assertIn('[W1]', done['body'])

    def test_fact_review_only_receives_web_pages_used_by_the_draft(self):
        sources = SOURCES + [{'source_id': 'W2', 'title': '不相关虚构网页',
                              'url': 'https://example.gov.cn/other', 'excerpt': '无关全文'}]
        captured = []
        model = Model.__new__(Model)
        def stop_after_capture(_system, user, **_kwargs):
            import json
            captured.append(json.loads(user)['evidence'])
            raise ApiError('停止测试', kind='validation')
        model.complete = stop_after_capture
        with self.assertRaises(ApiError):
            model.review({'title': '虚构标题', 'body': '正文[S1][W1]'}, sources, {'target_words': 1500})
        self.assertEqual([s['source_id'] for s in captured[0]], ['S1', 'W1'])

    def test_writer_gets_original_text_and_selected_web_evidence(self):
        class Writer(Model):
            def __init__(self):
                self.seen = None

            def plan_article(self, _sources, _config):
                return {'status': 'ready', 'question': '虚构问题', 'angle': '虚构角度',
                        'fact_ids': ['F1'], 'sections': ['第一节']}

            def write(self, sources, _config):
                self.seen = sources
                raise ApiError('stop after evidence handoff', kind='validation')

        writer = Writer()
        run = SimpleNamespace(writer=writer, job={'id': 3},
                              config={'writing_mode': 'single_web', 'draft_first_mode': True},
                              stage=lambda *_args: None, cached=lambda _id, _key, compute: compute())
        engine = SimpleNamespace(db=SimpleNamespace(rows=lambda *_args: [{'content': '虚构原文含完整例外条件'}]))
        with self.assertRaises(ApiError):
            compose(engine, run, SOURCES)
        self.assertEqual(writer.seen[0]['excerpt'], '虚构原文含完整例外条件')
        self.assertNotIn('excerpt', SOURCES[0])

    def test_writer_request_includes_only_selected_web_excerpts(self):
        import json
        sources = [dict(SOURCES[0], excerpt='虚构原文全文'),
                   dict(SOURCES[1], excerpt='虚构官方条款与例外'),
                   {'source_id': 'W2', 'title': '无关网页',
                    'url': 'https://example.gov.cn/other', 'excerpt': '无关页面全文'}]
        requests = []
        model = Model.__new__(Model)
        def capture(_system, user, **_kwargs):
            requests.append(json.loads(user))
            raise ApiError('stop after request', kind='validation')
        model.complete = capture
        with self.assertRaises(ApiError):
            model._write_once(sources, {
                'writing_mode': 'single_web', 'topic': '', 'target_words': 1000,
                'draft_first_mode': False,
                'editorial_plan': {'fact_ids': ['F1', 'F3'], 'question': '问题', 'angle': '角度',
                                   'sections': ['第一节', '第二节']}})
        briefs = {item['source_id']: item for item in requests[0]['sources']}
        self.assertEqual(briefs['S1']['excerpt'], '虚构原文全文')
        self.assertEqual(briefs['W1']['excerpt'], '虚构官方条款与例外')
        self.assertNotIn('excerpt', briefs['W2'])

    def test_staged_writer_uses_short_language_prompt(self):
        import json
        from wewriter.api import STAGED_WRITER_RULES, WRITER_RULES
        sources = [dict(SOURCES[0], excerpt='虚构原文全文'),
                   dict(SOURCES[1], excerpt='虚构官方条款')]
        model = Model.__new__(Model)
        captured = []
        def stop(system, user, **_kwargs):
            captured.append((system, json.loads(user)))
            raise ApiError('stop after request', kind='validation')
        model.complete = stop
        with self.assertRaises(ApiError):
            model._write_once(sources, {
                'writing_mode': 'single_web', 'topic': '', 'target_words': 1000,
                'draft_first_mode': True,
                'editorial_plan': {'fact_ids': ['F1', 'F3'], 'question': '问题', 'angle': '角度',
                                   'sections': ['第一节'], 'argument': ARGUMENT,
                                   'domain': {'status': 'ready', 'boundaries': [
                                       {'text': '甲类有限定', 'fact_ids': ['F3']}]}}})
        self.assertTrue(captured[0][0].startswith(STAGED_WRITER_RULES))
        self.assertIn('verify_items', captured[0][0])
        self.assertEqual(STAGED_WRITER_RULES, WRITER_RULES)
        self.assertIn('写作风格只按 user_style', STAGED_WRITER_RULES)
        self.assertEqual(captured[0][1]['editorial_plan']['domain']['boundaries'][0]['text'],
                         '虚构部门文件列出甲类对象。')
        self.assertNotIn('sections', captured[0][1]['editorial_plan'])
        self.assertNotIn('link', captured[0][1]['editorial_plan']['argument'][0])
        self.assertNotIn('conclusion', captured[0][1]['editorial_plan']['argument'][0])


if __name__ == '__main__':
    unittest.main()
