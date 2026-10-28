"""T-049: source citations use local IDs even when database article IDs are sparse."""
import json
import unittest

from wewriter.api import Model
from wewriter.pipeline.materials import materials


class MaterialTest(unittest.TestCase):
    def test_only_article_twelve_is_labeled_s1(self):
        class DB:
            def rows(self, sql, args):
                if 'FROM articles' in sql:
                    return [{'id': args[0], 'cleared': 0, 'title': '测试素材',
                             'url': 'https://mp.weixin.qq.com/s/test',
                             'account_name': '测试号', 'published_at': '2026-10-27'}]
                return []

            def execute(self, *_args):
                pass

        class Funnel:
            def material(self, *_args):
                pass

        class Context:
            config = {}
            job = {'id': 7}
            funnel = Funnel()

            def stage(self, *_args):
                pass

        class Engine:
            db = DB()

            def check(self):
                pass

            def notify(self, *_args):
                pass

            def _article_text(self, *_args):
                return '测试正文', None

            def _summaries(self, *_args):
                return [{'summary': '摘要', 'facts': [], 'uncertainties': []}]

        result = materials(Engine(), Context(), [12])
        self.assertEqual(result[0]['source_id'], 'S1')
        self.assertEqual(result[0]['article_id'], 12)

    def test_invalid_citation_retry_lists_exact_allowed_ids(self):
        class Writer(Model):
            def __init__(self):
                self.requests = []

            def complete(self, system, user, **_kwargs):
                payload = json.loads(user)
                self.requests.append((system, payload))
                marker = '[S10]' if len(self.requests) == 1 else '[S1]'
                return {'title': '测试稿', 'body': '有依据的表述。' + marker,
                        'outline': [], 'review_notes': []}

        writer = Writer()
        result = writer.write([{'source_id': 'S1', 'title': '测试素材'}],
                              {'topic': '', 'style': '', 'target_words': 2000})
        self.assertEqual(result['body'], '有依据的表述。[S1]')
        self.assertEqual(len(writer.requests), 2)
        self.assertEqual(writer.requests[0][1]['allowed_source_ids'], ['S1'])
        self.assertIn('本篇允许的来源编号仅有：S1', writer.requests[1][1]['validation_feedback'])
        self.assertIn('不能把 F10 错写成 S10', writer.requests[0][0])


if __name__ == '__main__':
    unittest.main()
