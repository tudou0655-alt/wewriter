"""Offline regressions for the T-040 source and rewrite trust boundaries."""
import unittest
import tempfile
from pathlib import Path

from wewriter.config import defaults
from wewriter.costs import CostMeter, UtilityFunnel, summary, utility_summary
from wewriter.db import Database
from wewriter.pipeline.collect import Funnel
from wewriter.pipeline.composition import compose
from wewriter.api import ApiError
from wewriter.api import Transport
from wewriter.engine import Engine, _Run
from wewriter.api import digest

from wewriter.style import same_facts, source_level


class TrustBoundaryTest(unittest.TestCase):
    def test_only_chinese_government_domain_is_official(self):
        for url in ('https://ministry.gov.example.com/a',
                    'https://gov.cn.example.com/a',
                    'https://example.com/gov.cn/a'):
            with self.subTest(url=url):
                self.assertEqual(source_level({'source_id': 'W1', 'url': url}), 'reported')
        self.assertEqual(source_level({'source_id': 'W1', 'url': 'https://www.ndrc.gov.cn/a'}),
                         'official')

    def test_swapped_amounts_are_not_a_style_only_rewrite(self):
        before = {'title': '通知', 'body': '甲公司获补贴100万元，乙公司获补贴200万元。[W1]'}
        after = {'title': '通知', 'body': '甲公司获补贴200万元，乙公司获补贴100万元。[W1]'}
        self.assertIn('数字与对应事实的关系变了，需人工复核', same_facts(before, after))

    def test_permission_flip_is_not_a_style_only_rewrite(self):
        before = {'title': '通知', 'body': '甲公司可以申报。[W1]'}
        after = {'title': '通知', 'body': '甲公司不可以申报。[W1]'}
        self.assertIn('否定或许可表述变了，需人工复核', same_facts(before, after))


class PaidRequestLimitTest(unittest.TestCase):
    def test_parent_child_and_unknown_attempts_share_limit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            parent = db.job('limit-parent', defaults())
            child = db.job('limit-child', defaults())
            db.execute('INSERT INTO batch_items VALUES(?,?,?)', (parent['id'], 1, child['id']))
            parent_meter = CostMeter(Funnel.for_attempt(db, parent, False), max_requests=2)
            child_meter = CostMeter(Funnel.for_attempt(db, child, False), max_requests=2)
            url = 'https://www.dajiala.com/fbmain/monitor/v3/post_history'
            parent_meter.begin(url, {'nickname': '虚构甲'})  # Response unknown after timeout.
            child_meter.begin(url, {'nickname': '虚构乙'})
            with self.assertRaisesRegex(ValueError, '付费请求次数上限'):
                child_meter.begin(url, {'nickname': '虚构乙'})  # Retried POST is blocked.
            self.assertEqual(summary(db, parent['id'])['unknown_requests'], 2)

    def test_settings_actions_have_separate_persistent_ledger(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            meter = CostMeter(UtilityFunnel(db))
            meter.begin('https://api.deepseek.com/chat/completions', {'model': 'deepseek-flash'})
            self.assertEqual(utility_summary(db)['unknown_requests'], 1)
            self.assertEqual(utility_summary(db)['requests'], 1)

    def test_everyinfra_request_uses_a_slot_with_unknown_price(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            job = db.job('everyinfra-limit', defaults())
            meter = CostMeter(Funnel.for_attempt(db, job, False), max_requests=1)
            meter.begin('https://api.everyinfra.com/api/v1/social',
                        {'action': 'account_articles', 'platform': 'wechat_oa'})
            with self.assertRaisesRegex(ValueError, '付费请求次数上限'):
                meter.begin('https://api.everyinfra.com/api/v1/search',
                            {'tool': 'read'})
            self.assertEqual(summary(db, job['id'])['unknown_requests'], 1)


class DraftCheckpointTest(unittest.TestCase):
    def test_review_failure_resumes_without_rewriting_and_input_change_invalidates(self):
        cache = {}

        class Writer:
            writes = 0
            fail_review = True

            def write(self, _sources, _config):
                self.writes += 1
                return {'title': '虚构稿', 'body': '虚构正文。[S1]', 'outline': [], 'review_notes': []}

            def review(self, article, _sources, _config):
                if self.fail_review:
                    raise ApiError('虚构二审失败')
                return {**article, 'quality_notes': []}

        class Run:
            job = {'id': 9}
            config = {'topic': '虚构主题', 'quality_review_enabled': True,
                      'style_review_enabled': False}
            review_state = 'off'
            warnings = []
            writer = Writer()

            def stage(self, *_):
                pass

            def warn(self, message):
                self.warnings.append(message)

            def cached(self, _article_id, key, compute):
                if key not in cache:
                    cache[key] = compute()
                return cache[key]

        class Engine:
            @staticmethod
            def check():
                pass

        run = Run()
        sources = [{'source_id': 'S1', 'article_id': 1, 'analysis': []}]
        compose(Engine(), run, sources)
        self.assertEqual(run.writer.writes, 1)
        run.writer.fail_review = False
        compose(Engine(), run, sources)
        self.assertEqual(run.writer.writes, 1)
        run.config = {**run.config, 'topic': '修改后的虚构主题'}
        compose(Engine(), run, sources)
        self.assertEqual(run.writer.writes, 2)

    def test_sqlite_checkpoint_survives_new_run_and_is_removed_on_clear(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            aid = db.save_article({'source_key': 'fictional', 'account_name': '虚构号',
                                   'title': '虚构素材', 'url': 'https://mp.weixin.qq.com/s/fictional',
                                   'published_at': '2026-10-27T00:00:00+08:00'})
            config = {'writer_provider': 'deepseek', 'quality_review_enabled': False,
                      'style_review_enabled': False, 'topic': '虚构主题'}
            job = db.job('checkpoint-fixture', config)
            engine = Engine(db, object())

            class Writer:
                calls = 0

                def write(self, _sources, _config):
                    self.calls += 1
                    return {'title': '虚构稿', 'body': '虚构正文。[S1]', 'outline': [], 'review_notes': []}

            writer = Writer()
            sources = [{'source_id': 'S1', 'article_id': aid, 'analysis': []}]

            def new_run(settings):
                run = _Run(engine, job, settings, Transport(), None)
                run._models['deepseek'] = writer
                return run

            compose(engine, new_run(config), sources)
            self.assertEqual(writer.calls, 1)
            self.assertTrue(db.rows('SELECT 1 FROM analyses WHERE article_id=?', (aid,)))
            compose(engine, new_run(dict(config)), sources)
            self.assertEqual(writer.calls, 1)
            compose(engine, new_run({**config, 'topic': '变更主题'}), sources)
            self.assertEqual(writer.calls, 2)
            draft_id = db.save_draft(job['id'], '虚构稿', '虚构正文。[S1]', sources, '虚构报告')
            db.clear_records('drafts', [draft_id])
            self.assertFalse(db.rows('SELECT 1 FROM analyses WHERE article_id=? '
                                     "AND cache_key LIKE 't040-%'", (aid,)))
            db.clear_records('articles', [aid])
            self.assertFalse(db.rows('SELECT 1 FROM analyses WHERE article_id=?', (aid,)))

    def test_failed_style_review_is_not_checkpointed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            aid = db.save_article({'source_key': 'fictional-style', 'account_name': '虚构号',
                                   'title': '虚构素材', 'url': 'https://mp.weixin.qq.com/s/fictional-style',
                                   'published_at': '2026-10-27T00:00:00+08:00'})
            config = {'writer_provider': 'deepseek', 'quality_review_enabled': False,
                      'style_review_enabled': True, 'topic': '虚构主题'}
            job = db.job('checkpoint-style', config)
            engine = Engine(db, object())

            class Writer:
                style_calls = 0

                def write(self, _sources, _config):
                    return {'title': '虚构稿', 'body': '虚构正文。[S1]', 'outline': [], 'review_notes': []}

                def style_review(self, *_args):
                    self.style_calls += 1
                    raise ApiError('虚构暂时失败')

            writer = Writer()
            for _ in range(2):
                run = _Run(engine, job, dict(config), Transport(), None)
                run._models['deepseek'] = writer
                compose(engine, run, [{'source_id': 'S1', 'article_id': aid, 'analysis': []}])
            self.assertEqual(writer.style_calls, 2)


class SummaryReuseTest(unittest.TestCase):
    def test_only_matching_active_article_reuses_valid_chunk(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            body = '虚构内容甲乙丙丁戊己庚辛壬癸。' * 10
            engine = Engine(db, object())
            calls = []

            def make_summary(_ctx, article, _chunk, _part):
                calls.append(article['id'])
                return {'summary': '虚构摘要', 'facts': [{'claim': '虚构事实',
                        'quote': '虚构内容甲乙丙丁戊己庚辛壬癸'}], 'uncertainties': []}

            engine._summary_attempts = make_summary
            config = defaults()
            job = db.job('summary-fixture', config)
            ctx = _Run(engine, job, config, Transport(), None)

            def article(key, title, text):
                aid = db.save_article({'source_key': key, 'account_name': '虚构号',
                                       'title': title, 'url': 'https://mp.weixin.qq.com/s/' + key,
                                       'published_at': '2026-10-27T00:00:00+08:00'})
                db.execute('UPDATE articles SET content=?,content_hash=? WHERE id=?',
                           (text, digest(text), aid))
                return {'id': aid, 'title': title}

            first = article('one', '同名标题', body)
            second = article('two', '同名标题', body)
            different_title = article('three', '不同标题', body)
            different_body = article('four', '同名标题', body + '改变')
            engine._summaries(ctx, first, body)
            engine._summaries(ctx, second, body)
            self.assertEqual(calls, [first['id']])
            engine._summaries(ctx, different_title, body)
            engine._summaries(ctx, different_body, body + '改变')
            self.assertEqual(len(calls), 3)
            other_config = defaults()
            other_config['providers'][other_config['summary_provider']]['model'] = 'fictional-other-model'
            other_config['summary_model'] = 'fictional-other-model'
            other_ctx = _Run(engine, job, other_config, Transport(), None)
            engine._summaries(other_ctx, second, body)
            self.assertEqual(len(calls), 4)
            db.clear_records('articles', [first['id'], second['id']])
            after_clear = article('five', '同名标题', body)
            engine._summaries(ctx, after_clear, body)
            self.assertEqual(calls[-1], after_clear['id'])

    def test_unlocatable_quote_is_not_shared(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            body = '虚构可定位原句。' * 30
            ids = []
            for key in ('bad-a', 'bad-b'):
                aid = db.save_article({'source_key': key, 'account_name': '虚构号',
                                       'title': '相同标题', 'url': 'https://mp.weixin.qq.com/s/' + key,
                                       'published_at': '2026-10-27T00:00:00+08:00'})
                db.execute('UPDATE articles SET content=?,content_hash=? WHERE id=?',
                           (body, digest(body), aid))
                ids.append(aid)
            config = defaults()
            job = db.job('quote-fixture', config)
            engine = Engine(db, object())
            calls = []

            def make_summary(_ctx, article, _chunk, _part):
                calls.append(article['id'])
                return {'summary': '虚构摘要', 'facts': [{'claim': '虚构事实',
                        'quote': '完全不存在的引语'}], 'uncertainties': []}

            engine._summary_attempts = make_summary
            ctx = _Run(engine, job, config, Transport(), None)
            for aid in ids:
                engine._summaries(ctx, {'id': aid, 'title': '相同标题'}, body)
            self.assertEqual(calls, ids)

    def test_empty_facts_are_not_shared(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            db = Database(Path(folder) / 'test.sqlite')
            body = '虚构正文。' * 30
            ids = []
            for key in ('empty-a', 'empty-b'):
                aid = db.save_article({'source_key': key, 'account_name': '虚构号',
                                       'title': '同题', 'url': 'https://mp.weixin.qq.com/s/' + key,
                                       'published_at': '2026-10-27T00:00:00+08:00'})
                db.execute('UPDATE articles SET content=?,content_hash=? WHERE id=?',
                           (body, digest(body), aid))
                ids.append(aid)
            config = defaults()
            job = db.job('empty-facts', config)
            engine = Engine(db, object())
            calls = []

            def make_summary(_ctx, article, _chunk, _part):
                calls.append(article['id'])
                return {'summary': '虚构摘要', 'facts': [], 'uncertainties': []}

            engine._summary_attempts = make_summary
            ctx = _Run(engine, job, config, Transport(), None)
            for aid in ids:
                engine._summaries(ctx, {'id': aid, 'title': '同题'}, body)
            self.assertEqual(calls, ids)


if __name__ == '__main__':
    unittest.main()
