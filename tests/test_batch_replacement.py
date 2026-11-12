"""T-020: a batch article that failed on a timeout is replaced by the next hottest one (offline)."""
import json
import unittest

import test_core
from test_hotspots import HotModel
from test_independent import ResearchFake
from wewriter.api import ApiError, QUOTE_NOTE
from wewriter.engine import Engine


class ManySource(test_core.FakeSource):
    def articles(self, account, limit):
        when = (test_core.datetime.now(test_core.timezone.utc) - test_core.timedelta(hours=1)).isoformat()
        return [{'title': f'文章{i}', 'url': f'https://mp.weixin.qq.com/s/many{i}', 'posted_at': when,
                 'read_count': (6 - i) * 1000, 'like_count': 0} for i in range(6)]


class OneSource(ManySource):
    def articles(self, account, limit):
        return super().articles(account, limit)[:1]


class FailingWriter(HotModel):
    failures = {}
    calls = []

    def write(self, sources, config):
        title = next(s['title'] for s in sources if 'article_id' in s)
        type(self).calls.append(title)
        if title in self.failures:
            raise self.failures[title]()
        return {'title': title + '稿', 'body': '内容。[' + sources[0]['source_id'] + '][W1]',
                'outline': [], 'review_notes': []}


class WarningWriter(FailingWriter):
    def write(self, sources, config):
        draft = super().write(sources, config)
        if sources[0]['title'] == '文章0':
            draft['review_notes'].append(QUOTE_NOTE + '虚构引语')
        return draft


def timeout():
    return ApiError('网络连接失败或超时（请求超时）', kind='network')


def bad_key():
    return ApiError('HTTP 401：API Key 无效', True)


def invalid_output():
    return ApiError('正文来源编号无效', kind='validation')


class BatchReplacementTest(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreTest()
        self.f.setUp()
        FailingWriter.calls = []

    def tearDown(self):
        FailingWriter.failures = {}
        self.f.tearDown()

    def run_batch(self, count, failures):
        FailingWriter.failures = failures
        self.f.config.update(writing_mode='separate_web', draft_count=count)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=ManySource,
                        model_factory=FailingWriter, research_factory=ResearchFake)
        status = engine.run('batch-replace', self.f.config)
        return status, self.f.db.rows("SELECT * FROM jobs WHERE run_key='batch-replace'")[0]

    def test_timeout_is_replaced_and_explained(self):
        status, job = self.run_batch(2, {'文章0': timeout})
        self.assertEqual(status, '已完成')
        self.assertEqual(len(self.f.db.rows('SELECT * FROM drafts')), 2)
        self.assertIn('热度第 1 篇因超时或网络中断失败，已由热度第 3 篇补位', job['message'])
        child = self.f.db.rows("SELECT error_code FROM jobs WHERE run_key LIKE 'batch:%' AND status='失败'")
        self.assertEqual([c['error_code'] for c in child], ['network'])

    def test_short_queue_keeps_the_one_available_draft(self):
        self.f.config.update(writing_mode='separate_web', draft_count=2)
        research_calls = []

        def research_factory(*args, **kwargs):
            research_calls.append(1)
            return ResearchFake(*args, **kwargs)

        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=OneSource,
                        model_factory=FailingWriter, research_factory=research_factory)
        self.assertEqual(engine.run('batch-too-short', self.f.config), '部分完成')
        job = self.f.db.rows("SELECT message,error_code FROM jobs WHERE run_key='batch-too-short'")[0]
        self.assertIn('已生成 1/2 篇', job['message'])
        self.assertIn('新素材不足', job['message'])
        self.assertEqual(len(research_calls), 1)
        self.assertEqual(FailingWriter.calls, ['文章0'])
        self.assertEqual(len(self.f.db.rows('SELECT * FROM drafts')), 1)

    def test_one_candidate_can_fill_one_requested_draft(self):
        self.f.config.update(writing_mode='separate_web', draft_count=1)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=OneSource,
                        model_factory=FailingWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('batch-one', self.f.config), '已完成')
        self.assertEqual(len(self.f.db.rows('SELECT * FROM drafts')), 1)

    def test_key_error_is_not_replaced(self):
        status, job = self.run_batch(2, {'文章0': bad_key})
        self.assertEqual(status, '失败')
        self.assertNotIn('补位', job['message'])
        self.assertIn('共同故障', job['message'])
        self.assertEqual(FailingWriter.calls, ['文章0'])
        self.assertEqual(len(self.f.db.rows('SELECT * FROM batch_items')), 1)

    def test_consecutive_network_failures_stop_the_batch(self):
        status, job = self.run_batch(3, {'文章0': timeout, '文章1': timeout, '文章2': timeout})
        self.assertEqual(status, '失败')
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1'])
        self.assertIn('连续网络故障', job['message'])

    def test_network_outage_does_not_walk_the_whole_queue(self):
        status, _ = self.run_batch(1, {f'文章{i}': timeout for i in range(6)})
        self.assertEqual(status, '失败')
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1'])

    def test_invalid_single_article_output_uses_next_hottest_candidate(self):
        status, job = self.run_batch(2, {'文章0': invalid_output})
        self.assertEqual(status, '已完成')
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1', '文章2'])
        self.assertIn('热度第 1 篇模型输出未通过校验，已由热度第 3 篇补位', job['message'])

    def test_resume_advances_past_exhausted_candidates(self):
        FailingWriter.failures = {f'文章{i}': invalid_output for i in range(3)}
        self.f.config.update(writing_mode='separate_web', draft_count=1)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=ManySource,
                        model_factory=FailingWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('resume-batch', self.f.config), '失败')
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1', '文章2'])
        self.assertIn('继续原任务', self.f.db.rows("SELECT message FROM jobs WHERE run_key='resume-batch'")[0]['message'])
        self.assertEqual(engine.run('resume-batch', self.f.config), '已完成')
        self.assertEqual(FailingWriter.calls, ['文章0', '文章1', '文章2', '文章3'])
        self.assertEqual(len(self.f.db.rows('SELECT * FROM drafts')), 1)

    def test_material_and_timeout_replacements_together(self):
        class GappySource(ManySource):
            def content(self, url):
                if url.endswith('many0'):
                    raise ApiError('微信读书没有返回正文，可能要求验证或文章已删除')
                return super().content(url)
        FailingWriter.failures = {'文章1': timeout}
        self.f.config.update(writing_mode='separate_web', draft_count=2)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=GappySource,
                        model_factory=FailingWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('batch-mixed', self.f.config), '已完成')
        message = self.f.db.rows("SELECT message FROM jobs WHERE run_key='batch-mixed'")[0]['message']
        self.assertIn('热度第 1 篇素材提取失败，已由热度第 3 篇补位', message)
        self.assertIn('热度第 2 篇因超时或网络中断失败，已由热度第 4 篇补位', message)
        warnings = json.loads(self.f.db.rows("SELECT warnings FROM jobs WHERE run_key='batch-mixed'")[0]['warnings'])
        self.assertTrue(any(w.startswith('未完成：热度第 2 篇') for w in warnings))

    def test_flagged_draft_is_kept_but_next_candidate_fills_machine_screening_target(self):
        self.f.config.update(writing_mode='separate_web', draft_count=2, draft_first_mode=False)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=ManySource,
                        model_factory=WarningWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('batch-screening', self.f.config), '已完成')
        self.assertEqual(len(self.f.db.rows('SELECT id FROM drafts')), 3)
        parent = self.f.db.rows("SELECT message,funnel FROM jobs WHERE run_key='batch-screening'")[0]
        self.assertIn('程序初筛通过 2/2 篇', parent['message'])
        self.assertIn('热度第 1 篇程序初筛未通过，已由热度第 3 篇补位', parent['message'])
        self.assertEqual(json.loads(parent['funnel'])['quality']['drafts'], 3)

    def test_default_flagged_draft_finishes_and_resume_does_not_rewrite_it(self):
        self.f.config.update(writing_mode='separate_web', draft_count=1)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=OneSource,
                        model_factory=WarningWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('batch-only-flagged', self.f.config), '已完成')
        parent = self.f.db.rows("SELECT message FROM jobs WHERE run_key='batch-only-flagged'")[0]
        self.assertIn('程序初筛通过 0/1 篇', parent['message'])
        self.assertEqual(engine.run('batch-only-flagged', self.f.config), '已完成')
        self.assertEqual(WarningWriter.calls, ['文章0'])

    def test_default_batch_stops_at_saved_count_and_keeps_quality_counts_separate(self):
        self.f.config.update(writing_mode='separate_web', draft_count=2, draft_first_mode=True)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=ManySource,
                        model_factory=WarningWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('mvp-saved-count', self.f.config), '已完成')
        parent = self.f.db.rows("SELECT * FROM jobs WHERE run_key='mvp-saved-count'")[0]
        quality = json.loads(parent['funnel'])['quality']
        self.assertEqual((quality['drafts'], quality['machine_ready']), (2, 1))
        self.assertEqual(self.f.db.qualified_drafts(parent['id']), 0)
        self.assertIn('已保存 2/2 篇草稿，需复核', parent['message'])
        self.assertTrue(json.loads(parent['warnings']))
        engine.run('mvp-saved-count', self.f.config)
        self.assertEqual(WarningWriter.calls, ['文章0', '文章1'])
        self.assertEqual(len(self.f.db.rows('SELECT id FROM drafts')), 2)

    def test_style_rejection_is_reported_without_failing_machine_screening(self):
        class StyleWarningWriter(FailingWriter):
            def style_review(self, *_args):
                return {'scores': dict.fromkeys(('title', 'opening', 'hierarchy', 'voice',
                                                'usefulness', 'distinctness'), 2), 'rewrite': None}

        self.f.config.update(writing_mode='separate_web', draft_count=1, draft_first_mode=False)
        engine = Engine(self.f.db, test_core.FakeVault(), source_factory=ManySource,
                        model_factory=StyleWarningWriter, research_factory=ResearchFake)
        self.assertEqual(engine.run('mvp-style-hint', self.f.config), '已完成')
        self.assertEqual(StyleWarningWriter.calls, ['文章0'])
        draft = self.f.db.rows('SELECT report FROM drafts')[0]
        self.assertIn('未返回有效改写', draft['report'])


if __name__ == '__main__':
    unittest.main()
