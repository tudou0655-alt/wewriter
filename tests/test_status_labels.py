"""T-026: run-history wording: drafts produced vs approved, and "too few new articles" is not a fault."""
import json
import tempfile
import unittest
from pathlib import Path

import test_core
from main import demo_data
from test_hotspots import HotSource
from test_independent import IndependentModel, ResearchFake
from wewriter.anonymity import block_list, hits
from wewriter.db import Database
from wewriter.engine import Engine
from wewriter.pipeline.composition import REVIEW_FAILED_NOTE
from wewriter.ui import job_status_label
from wewriter.webapp import Api, Backend

SHORTAGE = '已生成 1/2 篇独立主题文章，每篇分别联网研究和写作；符合条件的新素材不足，可在运行记录中“按当前配置新建采集”或扩大时间范围'


class StatusLabelTest(unittest.TestCase):
    def test_partial_runs_say_why(self):
        self.assertEqual(job_status_label({'status': '部分完成', 'message': SHORTAGE, 'warnings': '[]'}), '素材不足，完成 1/2')
        other = {'status': '部分完成', 'message': '已生成 2/3 篇独立主题文章；1 篇有处理或质量提示', 'warnings': '[]'}
        self.assertEqual(job_status_label(other), '部分完成 2/3')
        screening = {'status': '部分完成', 'message': '已生成 2 篇草稿，程序初筛通过 0/2 篇', 'warnings': '[]'}
        self.assertEqual(job_status_label(screening), '待复核，初筛通过 0/2')
        self.assertEqual(job_status_label({'status': '部分完成', 'message': '已生成 1 篇草稿', 'warnings': '[]'}), '部分完成')
        # Other states are unchanged.
        self.assertEqual(job_status_label({'status': '已完成', 'message': '', 'warnings': '["提示"]'}), '已完成（有提示）')
        self.assertEqual(job_status_label({'status': '已完成', 'message': '已保存 2/2 篇草稿，需复核',
                                          'warnings': '["待核实"]'}), '已保存，需复核')
        self.assertEqual(job_status_label({'status': '已停止', 'message': '任务已停止', 'warnings': '[]'}), '已停止')


class HistoryRowTest(unittest.TestCase):
    def test_history_counts_produced_and_approved_drafts_of_a_batch(self):
        f = test_core.CoreTest()
        f.setUp()
        try:
            f.config.update(writing_mode='separate_web', draft_count=3)      # HotSource offers only two articles
            IndependentModel.fail_title = None
            Engine(f.db, test_core.FakeVault(), source_factory=HotSource, model_factory=IndependentModel,
                   research_factory=ResearchFake).run('history:1', f.config)
            drafts = f.db.rows('SELECT id FROM drafts ORDER BY id')
            self.assertEqual(len(drafts), 2)
            f.db.set_draft_status(drafts[0]['id'], '已通过')
            api = Api(Backend(f.db, test_core.FakeVault(), Path(tempfile.mkdtemp(dir=f.path))))
            row = api.wb_history(0)['rows'][0]
            self.assertEqual((row['made'], row['target'], row['ok']), (2, 3, 1))
            self.assertEqual(row['status'], '素材不足，完成 2/3')
            parent = f.db.rows("SELECT * FROM jobs WHERE run_key='history:1'")[0]
            self.assertEqual(parent['status'], '部分完成')             # the stored state is unchanged
            detail = api.wb_detail(parent['id'])
            self.assertIn('素材不足，完成 2/3', json.dumps(detail, ensure_ascii=False))
        finally:
            f.tearDown()


class DemoReviewStateTest(unittest.TestCase):
    def test_demo_does_not_claim_ai_review_or_trigger_source_leak(self):
        with tempfile.TemporaryDirectory(dir=Path('.local-data').resolve()) as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(Path.cwd().resolve()))
            db = Database(Path(folder) / 'workspace.sqlite')
            demo_data(db)
            api = Api(Backend(db, test_core.FakeVault(), Path(folder), demo=True))
            self.assertEqual(api.wb_state()['job']['review_gap'], '二审未执行')
            draft = db.rows('SELECT id,title,body,sources,report FROM drafts LIMIT 1')[0]
            sources = json.loads(draft['sources'])
            self.assertEqual(hits(draft['title'] + '\n' + draft['body'], block_list(sources)), [])
            db.execute('UPDATE drafts SET report=? WHERE id=?',
                       (draft['report'] + '\n' + REVIEW_FAILED_NOTE + '模拟中断', draft['id']))
            self.assertEqual(api.wb_state()['job']['review_gap'], '二审未完成')
            db.execute('UPDATE drafts SET report=? WHERE id=?',
                       (draft['report'] + '\n## AI 二审\n- 已核对', draft['id']))
            self.assertEqual(api.wb_state()['job']['review_gap'], '')


if __name__ == '__main__':
    unittest.main()
