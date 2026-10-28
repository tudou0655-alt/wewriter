"""T-050: expand cached short drafts and retain a usable draft on failure."""
import unittest

from wewriter.api import ApiError, Model
from wewriter.pipeline.composition import article_chars, compose, length_warning, write_one


SOURCES = [{'source_id': 'S1', 'article_id': 12, 'title': '素材',
            'analysis': [{'facts': [{'claim': '设备应按验收要求运行', 'quote': '设备应按验收要求运行'}],
                          'uncertainties': []}]}]
CONFIG = {'target_words': 2000, 'topic': '', 'style': '',
          'quality_review_enabled': False, 'style_review_enabled': False}


def draft(size):
    return {'title': '设备验收', 'body': '甲' * size + '。[S1]', 'outline': [], 'review_notes': []}


class ExpansionTest(unittest.TestCase):
    def test_expansion_inserts_cited_paragraphs_without_changing_original(self):
        class Writer(Model):
            def __init__(self):
                pass

            def complete(self, _system, _user, **_kwargs):
                return {'additions': [
                    {'after_paragraph': 0, 'fact_ids': ['F1'], 'text': '乙' * 180 + '。'},
                    {'after_paragraph': 0, 'fact_ids': ['F1'], 'text': '丙' * 180 + '。'}]}

        result = Writer().expand(draft(1465), SOURCES, CONFIG)
        self.assertTrue(result['body'].startswith(draft(1465)['body']))
        self.assertGreaterEqual(article_chars(result['body']), 1800)
        self.assertEqual(result['body'].count('[S1]'), 3)

    def test_unknown_fact_cannot_be_inserted(self):
        class Writer(Model):
            def __init__(self):
                pass

            def complete(self, _system, _user, **_kwargs):
                return {'additions': [{'after_paragraph': 0, 'fact_ids': ['F99'], 'text': '新增说法。'}]}

        with self.assertRaisesRegex(ApiError, '事实编号无效'):
            Writer().expand(draft(1465), SOURCES, CONFIG)

    def test_retry_reuses_cached_initial_draft_and_recovers_expansion(self):
        cache = {}

        class Writer(Model):
            writes = 0
            expansions = 0

            def __init__(self):
                pass

            def write(self, _sources, _config):
                self.writes += 1
                return draft(1465)

            def expand(self, output, _sources, _config):
                self.expansions += 1
                if self.expansions == 1:
                    raise ApiError('扩写返回格式无效', kind='validation')
                return {**output, 'body': output['body'] + '\n\n' + '乙' * 340 + '。[S1]'}

        class Run:
            job = {'id': 7}
            config = dict(CONFIG)
            writer = Writer()
            review_state = 'off'
            warnings = []

            def stage(self, *_args):
                pass

            def warn(self, message):
                self.warnings.append(message)

            def cached(self, _article_id, key, compute):
                if key not in cache:
                    cache[key] = compute()
                return cache[key]

        class Engine:
            def check(self):
                pass

        run = Run()
        first, _notes = compose(Engine(), run, SOURCES)
        self.assertIn('至少 1800 字', length_warning(first, run.config))
        second, _notes = compose(Engine(), run, SOURCES)
        self.assertEqual(run.writer.writes, 1)
        self.assertEqual(run.writer.expansions, 2)
        self.assertEqual(length_warning(second, run.config), '')

    def test_short_but_valid_draft_is_saved_with_visible_warning(self):
        class Writer(Model):
            def __init__(self):
                pass

        class DB:
            saved = None

            def rows(self, *_args):
                return []

            def config(self):
                return {}

            def accounts(self):
                return []

            def examples(self):
                return []

            def save_draft(self, _job_id, _title, body, _sources, report):
                self.saved = (body, report)

            def update_job(self, *_args, **_kwargs):
                pass

        class Funnel:
            data = {}

            def save(self):
                pass

        class Run:
            config = dict(CONFIG, min_sources=1)
            job = {'id': 7}
            writer = Writer()
            warnings = []
            review_state = 'off'
            funnel = Funnel()

            def warn(self, message):
                self.warnings.append(message)

        class Engine:
            db = DB()

            def _materials(self, *_args):
                return SOURCES

            def _compose(self, *_args):
                return draft(1465), []

            def _report(self, _config, _sources, output, *_args):
                return '\n'.join(output['review_notes'])

            def check(self):
                pass

            def notify(self, *_args):
                pass

        engine = Engine()
        run = Run()
        status = write_one(engine, run, [12])
        self.assertEqual(status, '部分完成')
        self.assertIn('至少 1800 字', run.warnings[0])
        self.assertEqual(article_chars(engine.db.saved[0]), 1466)
        self.assertIn('待修改稿', engine.db.saved[1])


if __name__ == '__main__':
    unittest.main()
