"""Draft-first mode completes with review notes instead of failing on fixable output."""
import unittest

from wewriter.api import ApiError, DRAFT_FIRST_RULES, Model, WRITER_RULES
from wewriter.config import defaults
from wewriter.pipeline.composition import compose
from wewriter.ui import draft_hint


SOURCES = [{'source_id': 'S1', 'article_id': 12, 'title': '素材', 'analysis': []}]


class DraftFirstTest(unittest.TestCase):
    def test_draft_list_marks_skipped_automatic_review(self):
        self.assertEqual(draft_hint('## 审核状态\n优先成稿模式：未执行自动二审、联网查证和风格改写；稿件需人工审核。'),
                         '未执行自动二审')

    def test_default_mode_uses_shorter_prompt_and_preserves_draft(self):
        class Writer(Model):
            def __init__(self):
                self.requests = []

            def complete(self, system, user, **_kwargs):
                self.requests.append((system, user))
                return {'title': '测试稿', 'body': '一段需要人工核对的正文。[S10]',
                        'outline': [], 'review_notes': []}

        config = defaults()
        config.update(topic='', writing_mode='single_web')
        writer = Writer()
        result = writer.write(SOURCES, config)
        self.assertTrue(config['draft_first_mode'])
        self.assertEqual(DRAFT_FIRST_RULES, WRITER_RULES)
        self.assertIn('写作风格只按 user_style', DRAFT_FIRST_RULES)
        self.assertTrue(writer.requests[0][0].startswith(DRAFT_FIRST_RULES))
        self.assertEqual(len(writer.requests), 1)
        self.assertNotIn('[S10]', result['body'])
        self.assertTrue(any(note.startswith('【来源提示】') for note in result['review_notes']))

    def test_strict_validation_remains_available(self):
        draft = {'title': '测试稿', 'body': '正文。[S10]', 'outline': [], 'review_notes': []}
        with self.assertRaisesRegex(ApiError, '无效来源'):
            Model._finish_draft(draft, SOURCES, {'writing_mode': 'single_web'})

    def test_draft_first_skips_auto_review_verify_and_style(self):
        class Writer(Model):
            def __init__(self):
                pass

            def write(self, _sources, _config):
                return {'title': '测试稿', 'body': '甲' * 190 + '。[S1]',
                        'outline': [], 'review_notes': []}

            def review(self, *_args):
                raise AssertionError('draft-first should not review')

            def expand(self, *_args):
                raise AssertionError('adequate draft should not expand')

        class Run:
            job = {'id': 7}
            config = {'target_words': 200, 'draft_first_mode': True,
                      'quality_review_enabled': True, 'style_review_enabled': True,
                      'auto_verify': True}
            writer = Writer()
            review_state = 'off'

            def stage(self, *_args):
                pass

            def cached(self, _article_id, _key, compute):
                return compute()

        class Engine:
            def check(self):
                pass

        run = Run()
        output, quality_notes = compose(Engine(), run, SOURCES)
        self.assertEqual(output['title'], '测试稿')
        self.assertEqual(quality_notes, [])
        self.assertEqual(run.review_state, 'off')
        self.assertIn('未执行自动二审', run.verified['report'])

    def test_full_job_saves_a_draft_with_source_warning(self):
        import test_core
        from wewriter.engine import Engine

        class Writer(Model):
            def __init__(self, *_args):
                pass

            def summary(self, _article, _content):
                return {'summary': '资料说需要保留来源',
                        'facts': [{'claim': '需要保留来源', 'quote': '文中明确指出资料应当保留来源。'}],
                        'uncertainties': []}

            def write(self, sources, config):
                output = {'title': '测试草稿', 'body': '甲' * 210 + '。[S99]',
                          'outline': [], 'review_notes': []}
                return self._finish_draft(output, sources, config, soft=True)

            def review(self, *_args):
                raise AssertionError('draft-first should not review')

        fixture = test_core.CoreTest()
        fixture.setUp()
        try:
            fixture.config['target_words'] = 200
            engine = Engine(fixture.db, test_core.FakeVault(), source_factory=test_core.FakeSource,
                            model_factory=Writer)
            self.assertEqual(engine.run('draft-first:integration', fixture.config), '部分完成')
            rows = fixture.db.rows('SELECT body,report FROM drafts')
            self.assertEqual(len(rows), 1)
            self.assertNotIn('[S99]', rows[0]['body'])
            self.assertIn('来源提示', rows[0]['report'])
            self.assertIn('未执行自动二审', rows[0]['report'])
        finally:
            fixture.tearDown()

    def test_draft_first_without_findings_can_fill_batch_target(self):
        import test_core
        from test_batch_replacement import OneSource
        from test_independent import ResearchFake
        from wewriter.engine import Engine

        class Writer(Model):
            def __init__(self, *_args):
                pass

            def plan_article(self, _sources, _config):
                return {'status': 'ready', 'question': '虚构读者问题', 'angle': '虚构角度',
                        'fact_ids': ['F1', 'F2', 'F3'], 'sections': ['一']}

            def audit_claims(self, _draft, _sources):
                return []

            def summary(self, _article, _content):
                return {'summary': '材料摘要', 'facts': [], 'uncertainties': []}

            def write(self, sources, config):
                return {'title': '测试稿', 'body': '甲' * 210 + '。[S1]',
                        'outline': [], 'review_notes': []}

            def review(self, *_args):
                raise AssertionError('draft-first should not review')

        fixture = test_core.CoreTest()
        fixture.setUp()
        try:
            fixture.config.update(writing_mode='separate_web', draft_count=1,
                                  target_words=200, draft_first_mode=True)
            engine = Engine(fixture.db, test_core.FakeVault(), source_factory=OneSource,
                            model_factory=Writer, research_factory=ResearchFake)
            self.assertEqual(engine.run('draft-first:batch', fixture.config), '已完成')
            self.assertEqual(len(fixture.db.rows('SELECT child_job_id FROM batch_items')), 1)
            self.assertEqual(len(fixture.db.rows('SELECT id FROM drafts')), 1)
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
