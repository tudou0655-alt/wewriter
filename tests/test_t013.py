"""T-013: the customer's article brief reaches research, writing and review; default style migration."""
import json
import unittest

from wewriter.api import EDITORIAL_BRIEF, Model
from wewriter.config import LEGACY_DEFAULT_STYLE, defaults, migrate, validate
from wewriter.research import WebResearch


class Capture:
    def __init__(self, reply):
        self.reply, self.payloads = reply, []

    def request(self, url, key, payload, timeout=100):
        self.payloads.append(payload)
        return self.reply


def chat(content):
    return {'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}, 'finish_reason': 'stop'}]}


SOURCES = [{'source_id': 'S1', 'title': '原文'}, {'source_id': 'W1', 'title': '官方通知'}]
CONFIG = {'topic': '', 'style': '', 'target_words': 1500, 'writing_mode': 'single_web'}


class BriefTest(unittest.TestCase):
    def model(self, reply):
        transport = Capture(chat(reply))
        return Model('deepseek', {'model': 'deepseek-v4-pro', 'base_url': 'https://api.deepseek.com'}, 'k', transport), transport

    def test_writing_prompt_carries_reader_goal_and_source_relation(self):
        model, transport = self.model({'title': '标题', 'outline': ['a'], 'body': '原文。[S1] 通知。[W1]', 'review_notes': []})
        model.write(SOURCES, CONFIG)
        system = transport.payloads[0]['messages'][0]['content']
        self.assertIn(EDITORIAL_BRIEF, system)
        for need in ('企业中高层管理者', '可执行的建议', '优先补充原文之外的新信息', '往年'):
            self.assertIn(need, system)

    def test_review_checks_against_brief(self):
        revision = {'title': '标题', 'outline': ['a'], 'body': '原文。[S1] 通知。[W1]', 'review_notes': [], 'quality_notes': ['x'],
                    'claim_checks': [{'sentence': '标题：标题', 'evidence': 'S1：原文', 'action': '保留', 'note': ''}]}
        model, transport = self.model(revision)
        model.review({'title': '标题', 'body': '原文。[S1] 通知。[W1]', 'review_notes': []}, SOURCES, CONFIG)
        system = transport.payloads[0]['messages'][0]['content']
        self.assertIn(EDITORIAL_BRIEF, system)
        self.assertIn('（9）对照上述读者与目标', system)

    def test_facts_still_outrank_the_brief(self):
        model, transport = self.model({'title': '标题', 'outline': ['a'], 'body': '原文。[S1] 通知。[W1]', 'review_notes': []})
        model.write(SOURCES, CONFIG)
        system = transport.payloads[0]['messages'][0]['content']
        self.assertIn('不得编造案例、企业、地区或数据', system)
        self.assertLess(system.index(EDITORIAL_BRIEF), system.index('优先级：事实准确与证据支持高于用户 style'))

    def test_research_looks_for_official_documents_and_prior_years(self):
        transport = Capture({})
        try:
            WebResearch('k', {'search_model': 'gpt-5.6-terra', 'search_url': 'https://api.openai.com/v1/responses'},
                        transport).search({'title': '原文', 'analysis': {}})
        except Exception:
            pass  # the empty reply is rejected after the request is captured
        instructions = transport.payloads[0]['instructions']
        for need in ('官方通知或文件原文', '往年或上一批同类政策', '截止时间', '地方执行口径'):
            self.assertIn(need, instructions)


class StyleMigrationTest(unittest.TestCase):
    def test_new_default_is_policy_preset(self):
        config = defaults()
        validate(config)
        self.assertEqual(config['style_preset'], '政策申报实务')

    def test_untouched_old_default_moves_to_new_preset(self):
        config = migrate({'style': LEGACY_DEFAULT_STYLE})
        self.assertEqual(config['style_preset'], '政策申报实务')
        self.assertEqual(config['style'], defaults()['style'])

    def test_user_choices_are_kept(self):
        self.assertEqual(migrate({'style': '我的风格'})['style'], '我的风格')
        self.assertEqual(migrate({'style': '我的风格'})['style_preset'], '')
        kept = migrate({'style': '专业简洁，结论先行', 'style_preset': '专业简洁'})
        self.assertEqual((kept['style'], kept['style_preset']), ('专业简洁，结论先行', '专业简洁'))


class ReviewRepairTest(unittest.TestCase):
    def test_revision_missing_citations_gets_one_repair(self):
        replies = [
            {'title': '标题', 'outline': ['a'], 'body': '只有新信息。[W1]', 'review_notes': [], 'quality_notes': ['x'],
             'claim_checks': [{'sentence': '标题：标题', 'evidence': 'W1：通知', 'action': '保留', 'note': ''}]},
            {'title': '标题', 'outline': ['a'], 'body': '原文说。[S1] 新信息。[W1]', 'review_notes': [], 'quality_notes': ['x'],
             'claim_checks': [{'sentence': '标题：标题', 'evidence': 'W1：通知', 'action': '保留', 'note': ''}]}]

        class Seq:
            payloads = []

            def request(self, url, key, payload, timeout=100):
                self.payloads.append(payload)
                return chat(replies[len(self.payloads) - 1])
        transport = Seq()
        model = Model('deepseek', {'model': 'deepseek-v4-pro', 'base_url': 'https://api.deepseek.com'}, 'k', transport)
        result = model.review({'title': '标题', 'body': '原文。[S1] 通知。[W1]', 'review_notes': []}, SOURCES, CONFIG)
        self.assertEqual(result['body'], '原文说。[S1] 新信息。[W1]')
        self.assertEqual(len(transport.payloads), 2)
        retry = json.loads(transport.payloads[1]['messages'][1]['content'])
        self.assertIn('缺少公众号原文 [S]', retry['validation_feedback'])
        self.assertIn('至少保留一处 [S数字]', transport.payloads[0]['messages'][0]['content'])


if __name__ == '__main__':
    unittest.main()


class RewriteHitTest(unittest.TestCase):
    """Customer 2026-10-25: an article that already produced a draft can be written again on demand."""

    def test_used_article_gets_a_second_draft_with_other_titles_avoided(self):
        import test_core
        from test_hotspots import HotSource
        from test_independent import IndependentModel, ResearchFake
        from wewriter.engine import Engine
        f = test_core.CoreTest(); f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=1)
            IndependentModel.calls = []; IndependentModel.fail_title = None
            e = Engine(f.db, test_core.FakeVault(), source_factory=HotSource,
                       model_factory=IndependentModel, research_factory=ResearchFake)
            self.assertEqual(e.run('batch:first', f.config), '已完成')
            first = f.db.rows('SELECT title,sources FROM drafts')[0]
            article_id = next(s['article_id'] for s in json.loads(first['sources']) if 'article_id' in s)
            self.assertIn(article_id, e._used_articles())

            job = e.rewrite_job(article_id, f.config)
            stored = json.loads(job['config'])
            self.assertEqual(json.loads(job['selected']), [article_id])
            self.assertEqual(stored['avoid_titles'], [first['title']])
            self.assertEqual((stored['writing_mode'], stored['draft_count']), ('single_web', 1))
            self.assertEqual(e.run(job['run_key'], stored), '已完成')

            drafts = f.db.rows('SELECT job_id,sources FROM drafts ORDER BY id')
            self.assertEqual(len(drafts), 2)
            self.assertEqual(drafts[1]['job_id'], job['id'])
            self.assertIn(article_id, [s.get('article_id') for s in json.loads(drafts[1]['sources'])])
        finally:
            f.tearDown()

    def test_missing_article_is_refused(self):
        import test_core
        from wewriter.engine import Engine
        f = test_core.CoreTest(); f.setUp()
        try:
            with self.assertRaises(ValueError):
                Engine(f.db, test_core.FakeVault()).rewrite_job(99999, f.config)
        finally:
            f.tearDown()
