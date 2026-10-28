"""Draft-first mode completes with review notes instead of failing on fixable output."""
import unittest

from wewriter.api import ApiError, DRAFT_FIRST_RULES, Model, WRITER_RULES
from wewriter.config import defaults
from wewriter.pipeline.composition import compose


SOURCES = [{'source_id': 'S1', 'article_id': 12, 'title': '素材', 'analysis': []}]


class DraftFirstTest(unittest.TestCase):
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
        self.assertLess(len(DRAFT_FIRST_RULES), len(WRITER_RULES) // 2)
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


if __name__ == '__main__':
    unittest.main()
