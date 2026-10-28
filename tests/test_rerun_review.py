"""T-021: re-run the AI review of a saved draft from the drafts page (offline)."""
import json
import threading
import time
import unittest
from pathlib import Path

import test_core
from test_hotspots import HotSource, HotModel
from test_independent import ResearchFake
from wewriter.api import ApiError
from wewriter.engine import Engine
from wewriter.pipeline.composition import REVIEW_MISSING, review_state
from wewriter.webapp import Api, Backend


class Model(HotModel):
    """Writes a valid draft; review behaviour is set per test."""
    review_mode = 'fail'
    review_calls = []

    def write(self, sources, config):
        marks = ''.join(f"[{s['source_id']}]" for s in sources)
        return {'title': '初稿标题', 'body': '初稿正文。' + marks, 'outline': ['先讲变化'],
                'review_notes': ['核对截止日期']}

    def review(self, draft, sources, config):
        type(self).review_calls.append((draft, sources, config))
        if self.review_mode == 'fail':
            raise ApiError('二审修改稿两次未通过校验（缺少联网来源 [W]），已弃用修改稿', kind='validation')
        if self.review_mode == 'block':
            Model.started.set()
            Model.release.wait(5)
        return {**draft, 'title': '二审标题', 'body': '二审正文。[S' + sources[0]['source_id'][1:] + '][W1]',
                'quality_notes': ['逐句核对｜保留｜二审正文']}


class RerunReviewTest(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreTest()
        self.f.setUp()
        self.f.config.update(writing_mode='single_web', selection_mode='hot', min_sources=1, article_limit=1,
                             draft_first_mode=False)
        Model.review_mode, Model.review_calls = 'fail', []
        self.engine = Engine(self.f.db, test_core.FakeVault(), source_factory=HotSource, model_factory=Model,
                             research_factory=ResearchFake)
        self.engine.run('rerun:1', self.f.config)
        self.draft = self.f.db.rows('SELECT * FROM drafts')[0]
        Model.review_calls = []

    def tearDown(self):
        Model.review_mode = 'fail'
        self.f.tearDown()

    def current(self):
        return self.f.db.rows('SELECT * FROM drafts WHERE id=?', (self.draft['id'],))[0]

    def test_failed_review_is_detected(self):
        self.assertEqual(review_state(self.draft['report']), 'failed')

    def test_rerun_review_uses_raised_request_cap(self):
        saved = json.loads(self.f.db.rows('SELECT config FROM jobs WHERE id=?',
                                         (self.draft['job_id'],))[0]['config'])
        saved['max_paid_requests'] = 1
        self.f.db.execute('UPDATE jobs SET config=? WHERE id=?',
                          (json.dumps(saved), self.draft['job_id']))
        current = self.f.db.config()
        current['max_paid_requests'] = 5
        self.f.db.save_config(current)
        Model.review_mode = 'ok'
        self.engine.rerun_review(self.draft['id'])
        self.assertEqual(Model.review_calls[0][2]['max_paid_requests'], 5)

    def test_success_saves_a_new_version_and_keeps_the_old_one(self):
        self.f.db.set_draft_status(self.draft['id'], '需修改', '先看数据')
        Model.review_mode = 'ok'
        self.assertEqual(self.engine.rerun_review(self.draft['id']), 2)
        self.assertEqual(len(Model.review_calls), 1)
        first, sources, config = Model.review_calls[0]
        self.assertEqual(first['body'], self.draft['body'])
        self.assertEqual(first['outline'], ['先讲变化'])
        self.assertEqual(first['review_notes'], ['核对截止日期'])           # the failure note is not passed on
        self.assertNotIn('style_reference', sources[0])
        self.assertTrue(any(s['source_id'] == 'W1' for s in sources))
        self.assertFalse(config['research_degraded'])
        now = self.current()
        self.assertEqual((now['revision'], now['title'], now['status']), (2, '二审标题', '待审核'))
        self.assertEqual(review_state(now['report']), 'done')
        self.assertIn('补跑二审', now['report'])
        self.assertIn('【人工审核记录】', now['report'])
        self.assertNotIn('AI 二审未完成', now['report'])
        versions = self.f.db.rows('SELECT revision, body FROM draft_versions WHERE draft_id=? ORDER BY revision',
                                  (self.draft['id'],))
        self.assertEqual([v['revision'] for v in versions], [1, 2])
        self.assertEqual(versions[0]['body'], self.draft['body'])
        # A reviewed draft can be checked again under the current rules.
        self.assertEqual(self.engine.rerun_review(self.draft['id']), 3)

    def test_failure_leaves_the_draft_unchanged(self):
        with self.assertRaises(ApiError):
            self.engine.rerun_review(self.draft['id'])
        self.assertEqual(dict(self.current()), dict(self.draft))
        self.assertEqual(len(self.f.db.rows('SELECT * FROM draft_versions')), 1)

    def test_missing_material_is_reported_before_any_model_call(self):
        sources = json.loads(self.draft['sources'])
        sources[0].pop('analysis')
        self.f.db.execute('UPDATE drafts SET sources=? WHERE id=?', (json.dumps(sources), self.draft['id']))
        with self.assertRaisesRegex(ValueError, REVIEW_MISSING):
            self.engine.rerun_review(self.draft['id'])
        self.f.db.execute("UPDATE drafts SET sources='[]' WHERE id=?", (self.draft['id'],))
        with self.assertRaisesRegex(ValueError, REVIEW_MISSING):
            self.engine.rerun_review(self.draft['id'])
        self.assertEqual(Model.review_calls, [])

    def test_never_alongside_a_running_task(self):
        self.engine.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, '已有任务运行中'):
                self.engine.rerun_review(self.draft['id'])
        finally:
            self.engine.lock.release()

    def test_edit_during_the_review_is_not_overwritten(self):
        Model.review_mode = 'ok'
        real_review = Model.review

        def edit_then_review(model, *args):
            self.f.db.edit_draft(self.draft['id'], '人工标题', '人工正文', '待审核')
            return real_review(model, *args)
        Model.review = edit_then_review
        try:
            with self.assertRaisesRegex(ValueError, '被修改过'):
                self.engine.rerun_review(self.draft['id'])
        finally:
            Model.review = real_review
        self.assertEqual(self.current()['title'], '人工标题')


class DraftsPageTest(RerunReviewTest.__base__):
    def setUp(self):
        RerunReviewTest.setUp(self)
        self.backend = Backend(self.f.db, test_core.FakeVault(), Path(self.f.temp.name))
        self.backend.engine = self.engine
        self.api = Api(self.backend)

    tearDown = RerunReviewTest.tearDown
    current = RerunReviewTest.current

    def wait_idle(self):
        for _ in range(200):
            if not self.backend.busy:
                return
            time.sleep(0.02)
        self.fail('worker did not finish')

    def test_detail_offers_rerun_and_reports_the_outcome_once(self):
        detail = self.api.dr_get(self.draft['id'])
        self.assertEqual((detail['review'], detail['review_running']), ('failed', False))
        Model.review_mode = 'ok'
        self.assertTrue(self.api.dr_rereview(self.draft['id'])['ok'])
        self.wait_idle()
        detail = self.api.dr_get(self.draft['id'])
        self.assertEqual(detail['review'], 'done')
        self.assertTrue(detail['review_result']['ok'])
        self.assertIn('第 2 版', detail['review_result']['text'])
        self.assertIsNone(self.api.dr_get(self.draft['id'])['review_result'])

    def test_failure_message_and_unchanged_draft(self):
        self.api.dr_rereview(self.draft['id'])
        self.wait_idle()
        result = self.api.dr_get(self.draft['id'])['review_result']
        self.assertFalse(result['ok'])
        self.assertIn('稿件未改动', result['text'])
        self.assertEqual(self.current()['revision'], 1)

    def test_repeated_click_and_running_task_are_refused(self):
        Model.review_mode = 'block'
        Model.started, Model.release = threading.Event(), threading.Event()
        try:
            self.assertTrue(self.api.dr_rereview(self.draft['id'])['ok'])
            self.assertTrue(Model.started.wait(5))
            self.assertTrue(self.api.dr_get(self.draft['id'])['review_running'])
            again = self.api.dr_rereview(self.draft['id'])
            self.assertFalse(again['ok'])
            self.assertIn('正在补跑二审', again['error'])
            self.assertEqual(self.api.wb_resume(1)['ok'], False)          # tasks cannot start meanwhile
        finally:
            Model.release.set()
        self.wait_idle()
        self.assertEqual(len(Model.review_calls), 1)
        self.backend.busy = True                                        # a collection task is running
        refused = self.api.dr_rereview(self.draft['id'])
        self.assertFalse(refused['ok'])
        self.backend.busy = False

    def test_a_task_still_stopping_gets_a_clear_message(self):
        self.backend.busy = True
        self.backend.engine.cancel.set()
        try:
            refused = self.api.dr_rereview(self.draft['id'])
        finally:
            self.backend.busy = False
            self.backend.engine.cancel.clear()
        self.assertFalse(refused['ok'])
        self.assertIn('上一个任务正在停止', refused['error'])
        self.assertIsNone(self.backend.review_draft_id)

    def test_demo_mode_does_not_call_models(self):
        self.backend.demo = True
        self.assertFalse(self.api.dr_rereview(self.draft['id'])['ok'])
        self.assertEqual(Model.review_calls, [])


if __name__ == '__main__':
    unittest.main()
