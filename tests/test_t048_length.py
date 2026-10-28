"""T-048: target length stays meaningful after switching the writer to DeepSeek."""
import unittest

from wewriter.api import Model, WRITER_RULES
from wewriter.pipeline.composition import article_chars, compose, length_warning, style_pass, write_to_length


def draft(size):
    return {'title': '测试标题', 'body': '甲' * size + '[S1]', 'outline': [], 'review_notes': []}


class Writer:
    def __init__(self, sizes):
        self.sizes = sizes
        self.feedback = []

    def write(self, _sources, config):
        self.feedback.append(config.get('length_feedback'))
        return draft(self.sizes[len(self.feedback) - 1])


class LengthTest(unittest.TestCase):
    def test_display_count_ignores_citations_and_formatting(self):
        self.assertEqual(article_chars('## 标题\n甲乙[S1] > 丙[W2]'), 5)

    def test_writer_prompt_requires_realistic_target_without_fabrication(self):
        self.assertIn('90% 至 110%', WRITER_RULES)
        self.assertIn('绝不编造事实', WRITER_RULES)

    def test_short_first_draft_is_kept_for_targeted_expansion(self):
        writer = Writer([586, 1900])
        result = write_to_length(writer, [], {'target_words': 2000})
        self.assertEqual(article_chars(result['body']), 586)
        self.assertEqual(len(writer.feedback), 1)

    def test_adequate_draft_does_not_pay_for_retry(self):
        writer = Writer([1900])
        write_to_length(writer, [], {'target_words': 2000})
        self.assertEqual(len(writer.feedback), 1)

    def test_short_draft_gets_explicit_warning(self):
        writer = Writer([475, 586])
        output = write_to_length(writer, [], {'target_words': 2000})
        self.assertEqual(len(writer.feedback), 1)
        self.assertIn('至少 1800 字', length_warning(output, {'target_words': 2000}))

    def test_review_shortening_is_visible_without_discarding_draft(self):
        class ReviewWriter(Model):
            def __init__(self):
                self.calls = 0

            def write(self, _sources, _config):
                self.calls += 1
                return draft(1900)

            def review(self, output, _sources, _config):
                return {**output, 'body': '甲' * 586 + '[S1]', 'quality_notes': []}

        class Run:
            job = {'id': 1}
            config = {'target_words': 2000, 'quality_review_enabled': True, 'style_review_enabled': False}
            review_state = 'off'
            writer = ReviewWriter()

            def stage(self, *_args):
                pass

            def cached(self, _article_id, _key, compute):
                return compute()

        class Engine:
            def check(self):
                pass

        output, _notes = compose(Engine(), Run(), [{'article_id': 1, 'source_id': 'S1'}])
        self.assertIn('至少 1800 字', length_warning(output, Run.config))

    def test_style_rewrite_that_shrinks_full_article_is_rejected(self):
        class StyleWriter:
            def style_review(self, *_args):
                return {'scores': {key: 2 for key in
                                   ('title', 'opening', 'hierarchy', 'voice', 'usefulness', 'distinctness')},
                        'rewrite': draft(586)}

            def _finish_draft(self, output, sources, config):
                return Model._finish_draft(output, sources, config)

        class Run:
            config = {'target_words': 2000, 'style_review_enabled': True}
            writer = StyleWriter()
            styled = None

            def stage(self, *_args):
                pass

        class Engine:
            def check(self):
                pass

        original = draft(1900)
        output = style_pass(Engine(), Run(), original, [{'source_id': 'S1', 'title': '来源'}])
        self.assertEqual(output['body'], original['body'])


if __name__ == '__main__':
    unittest.main()
