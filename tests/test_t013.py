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
        # T-029: who the reader is and what the article must get done now come from the style card.
        self.assertNotIn(EDITORIAL_BRIEF, system)
        for need in ('企业老板和项目负责人', '帮读者把一件具体的事办成', '只能使用 facts 中的事实'):
            self.assertIn(need, system)
        self.assertNotIn('优先补充原文之外的新信息', system)

    def test_review_checks_against_brief(self):
        revision = {'title': '标题', 'outline': ['a'], 'body': '原文。[S1] 通知。[W1]', 'review_notes': [], 'quality_notes': ['x'],
                    'claim_checks': [{'sentence': '标题：标题', 'evidence': 'S1：原文', 'action': '保留', 'note': ''}]}
        model, transport = self.model(revision)
        model.review({'title': '标题', 'body': '原文。[S1] 通知。[W1]', 'review_notes': []}, SOURCES, CONFIG)
        system = transport.payloads[0]['messages'][0]['content']
        # T-029: the review checks facts and wording strength only; style is the style review's job.
        self.assertNotIn(EDITORIAL_BRIEF, system)
        self.assertIn('这是事实审核', system)
        self.assertIn('说法强度不能超过来源可信等级', system)

    def test_facts_still_outrank_the_brief(self):
        model, transport = self.model({'title': '标题', 'outline': ['a'], 'body': '原文。[S1] 通知。[W1]', 'review_notes': []})
        model.write(SOURCES, CONFIG)
        system = transport.payloads[0]['messages'][0]['content']
        self.assertIn('不得为了模仿风格编造', system)
        # The fact rules are part of the fixed rules that come before the style card.
        self.assertLess(system.index('优先级：事实准确与证据支持高于用户 style'), system.index('【风格卡】'))

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


class RewriteDetailTest(unittest.TestCase):
    def test_rewrite_task_detail_shows_the_article_not_a_funnel(self):
        import tempfile
        from pathlib import Path
        import test_core
        from wewriter import webapp
        f = test_core.CoreTest(); f.setUp()
        try:
            from wewriter.api import digest
            url = 'https://mp.weixin.qq.com/s/hit'
            aid = f.db.save_article({'source_key': digest(url), 'account_name': '号', 'title': '爆款', 'url': url,
                                     'published_at': '2026-10-24T00:00:00+00:00'})
            backend = webapp.Backend(f.db, test_core.FakeVault(), Path(tempfile.mkdtemp()), demo=False)
            job = backend.engine.rewrite_job(aid, f.config)
            detail = webapp.Api(backend).wb_detail(job['id'])
            self.assertTrue(detail['rewrite'])
            self.assertFalse(detail['has_funnel'])
            self.assertEqual([p['title'] for p in detail['picked']], ['爆款'])
            self.assertTrue(detail['title'].startswith('再写一篇'))
        finally:
            f.tearDown()


class ThemePreferenceTest(unittest.TestCase):
    def test_theme_is_saved_beside_the_database_and_validated(self):
        import tempfile
        from pathlib import Path
        import test_core
        from wewriter import webapp
        f = test_core.CoreTest(); f.setUp()
        try:
            data = Path(tempfile.mkdtemp())
            api = webapp.Api(webapp.Backend(f.db, test_core.FakeVault(), data, demo=False))
            self.assertEqual(api.meta()['theme'], 'system')
            self.assertTrue(api.ui_theme('light')['ok'])
            self.assertEqual(api.meta()['theme'], 'light')
            self.assertFalse(api.ui_theme('pink')['ok'])
            self.assertEqual(json.loads((data / 'ui.json').read_text(encoding='utf-8')), {'theme': 'light'})
            self.assertNotIn('theme', f.db.config())
        finally:
            f.tearDown()


class DialogFilterTest(unittest.TestCase):
    """2026-10-25: every export failed because pywebview rejected the dialog filter text."""

    def test_every_dialog_filter_is_accepted_by_pywebview(self):
        from webview.util import parse_file_type
        from wewriter import webapp
        from wewriter.ui import EXPORT_FORMATS
        self.assertEqual(set(webapp.DIALOG_FILTERS), set(EXPORT_FORMATS))
        for fmt, text in webapp.DIALOG_FILTERS.items():
            self.assertEqual(parse_file_type(text)[1], f'*.{fmt}')
        parse_file_type(webapp.BACKUP_FILTER)

    def test_export_passes_a_valid_filter_to_the_save_dialog(self):
        import tempfile
        from pathlib import Path
        import test_core
        from webview.util import parse_file_type
        from wewriter import webapp
        f = test_core.CoreTest(); f.setUp()
        try:
            out = Path(tempfile.mkdtemp())
            job = f.db.job('export-test', f.config)
            draft = f.db.save_draft(job['id'], '标题', '正文。[S1]', [], '# 报告')
            backend = webapp.Backend(f.db, test_core.FakeVault(), out, demo=False)
            seen = []

            class Window:
                def create_file_dialog(self, kind, **kw):
                    for item in kw.get('file_types', ()):
                        parse_file_type(item)        # raises exactly like the real dialog
                    seen.append(kw)
                    return str(out / kw['save_filename'])
            backend.window = Window()
            api = webapp.Api(backend)
            draft_id = draft if isinstance(draft, int) else draft['id']
            for fmt in ('docx', 'md', 'txt'):
                result = api.dr_export(draft_id, 'copy', fmt)
                self.assertTrue(result['ok'], result)
                self.assertTrue(Path(result['path']).exists())
            self.assertEqual(len(seen), 3)
        finally:
            f.tearDown()


class RewriteDirectionTest(unittest.TestCase):
    """Customer 2026-10-25: repeated 再写一篇 produced three near-identical drafts."""

    def _setup(self):
        import test_core
        from wewriter.api import digest
        from wewriter.engine import Engine
        f = test_core.CoreTest(); f.setUp()
        url = 'https://mp.weixin.qq.com/s/hit-dir'
        aid = f.db.save_article({'source_key': digest(url), 'account_name': '号', 'title': '单项冠军', 'url': url,
                                 'published_at': '2026-10-24T00:00:00+00:00'})
        job = f.db.job('old', f.config)
        f.db.save_draft(job['id'], '旧稿标题', '开头。[S1]\n\n## 申报条件\n\n内容。\n\n## 时间安排\n\n内容。',
                        [{'source_id': 'S1', 'article_id': aid}], '# 报告')
        return f, aid, Engine(f.db, test_core.FakeVault())

    def test_note_and_previous_structure_reach_the_writer_config(self):
        f, aid, engine = self._setup()
        try:
            stored = json.loads(engine.rewrite_job(aid, f.config, '从材料准备角度写')['config'])
            self.assertEqual(stored['topic'], '从材料准备角度写')
            self.assertEqual(stored['avoid_titles'], ['旧稿标题'])
            self.assertEqual(stored['avoid_angles'], ['旧稿标题｜小标题：申报条件 / 时间安排'])
        finally:
            f.tearDown()

    def test_second_rewrite_of_the_same_article_is_refused_while_one_is_pending(self):
        f, aid, engine = self._setup()
        try:
            engine.rewrite_job(aid, f.config)
            with self.assertRaises(ValueError):
                engine.rewrite_job(aid, f.config)
        finally:
            f.tearDown()

    def test_writer_is_told_to_change_angle_only_when_earlier_drafts_exist(self):
        model, transport = BriefTest.model(self, {'title': '新', 'outline': ['a'], 'body': '原文。[S1] 通知。[W1]', 'review_notes': []})
        model.write(SOURCES, dict(CONFIG, avoid_angles=['旧稿｜小标题：a / b'], topic='换个角度'))
        system = transport.payloads[0]['messages'][0]['content']
        user = json.loads(transport.payloads[0]['messages'][1]['content'])
        self.assertIn('必须换一个明显不同的切入角度', system)
        self.assertEqual(user['avoid_angles'], ['旧稿｜小标题：a / b'])
        model2, transport2 = BriefTest.model(self, {'title': '新', 'outline': ['a'], 'body': '原文。[S1] 通知。[W1]', 'review_notes': []})
        model2.write(SOURCES, CONFIG)
        self.assertNotIn('必须换一个明显不同的切入角度', transport2.payloads[0]['messages'][0]['content'])
