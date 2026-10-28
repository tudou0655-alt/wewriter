"""T-026: run-history wording: drafts produced vs approved, and "too few new articles" is not a fault."""
import json
import tempfile
import unittest
from pathlib import Path

import test_core
from test_hotspots import HotSource
from test_independent import IndependentModel, ResearchFake
from wewriter.engine import Engine
from wewriter.ui import job_status_label
from wewriter.webapp import Api, Backend

SHORTAGE = '已生成 1/2 篇独立主题文章，每篇分别联网研究和写作；符合条件的新素材不足，可在运行记录中“按当前配置新建采集”或扩大时间范围'


class StatusLabelTest(unittest.TestCase):
    def test_partial_runs_say_why(self):
        self.assertEqual(job_status_label({'status': '部分完成', 'message': SHORTAGE, 'warnings': '[]'}), '素材不足，完成 1/2')
        other = {'status': '部分完成', 'message': '已生成 2/3 篇独立主题文章；1 篇有处理或质量提示', 'warnings': '[]'}
        self.assertEqual(job_status_label(other), '部分完成 2/3')
        self.assertEqual(job_status_label({'status': '部分完成', 'message': '已生成 1 篇草稿', 'warnings': '[]'}), '部分完成')
        # Other states are unchanged.
        self.assertEqual(job_status_label({'status': '已完成', 'message': '', 'warnings': '["提示"]'}), '已完成（有提示）')
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


if __name__ == '__main__':
    unittest.main()
