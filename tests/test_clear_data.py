from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wewriter.db import Database
from wewriter.webapp import Api, Backend


class ClearDataTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.db = Database(self.path / 'workspace.sqlite')
        self.db.add_account('虚构公众号', 'fake-account')
        config = self.db.config()
        config['schedule_enabled'] = True
        self.db.save_config(config)
        self.article = self.db.save_article(dict(source_key='fake', title='虚构文章',
            url='https://example.test/article', account_name='虚构公众号', published_at='2026-10-25'))
        self.job = self.db.job('fake-job', config)
        self.db.save_draft(self.job['id'], '虚构稿件', '测试正文', [], '测试报告')
        self.vault = Mock()
        self.backend = Backend(self.db, self.vault, self.path)
        self.api = Api(self.backend)

    def test_confirmed_clear_preserves_accounts_config_and_restorable_backup(self):
        before = self.db.config()
        self.backend.session_queue = [self.job['id']]
        self.backend.current_job_id = self.job['id']
        result = self.api.set_clear_data(True)
        self.assertTrue(result['ok'], result)
        for table in ('articles', 'drafts', 'jobs', 'draft_versions', 'analyses', 'batch_items'):
            self.assertEqual(self.db.rows('SELECT * FROM ' + table), [])
        self.assertEqual(len(self.db.accounts()), 1)
        before['schedule_enabled'] = False
        self.assertEqual(self.db.config(), before)
        self.assertEqual(self.backend.session_queue, [])
        self.assertIsNone(self.backend.current_job_id)
        self.vault.assert_not_called()
        self.assertEqual(self.vault.mock_calls, [])
        with closing(sqlite3.connect(result['path'])) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM drafts').fetchone()[0], 1)
        self.db.restore(result['path'])
        self.assertEqual(len(self.db.rows('SELECT * FROM drafts')), 1)
        self.assertEqual(len(self.db.rows('SELECT * FROM articles')), 1)

    def test_requires_explicit_confirmation(self):
        for value in (False, None, 'true', 1):
            self.assertFalse(self.api.set_clear_data(value)['ok'])
        self.assertEqual(len(self.db.rows('SELECT * FROM drafts')), 1)
        self.assertEqual(list(self.path.glob('before-clear-*')), [])

    def test_backup_failure_never_deletes(self):
        with patch.object(self.db, 'backup', side_effect=ValueError('虚构备份失败')):
            self.assertFalse(self.api.set_clear_data(True)['ok'])
        self.assertEqual(len(self.db.rows('SELECT * FROM articles')), 1)
        self.assertEqual(len(self.db.rows('SELECT * FROM drafts')), 1)

    def test_running_worker_or_job_blocks_cleanup(self):
        self.backend.busy = True
        self.assertFalse(self.api.set_clear_data(True)['ok'])
        self.backend.busy = False
        self.db.update_job(self.job['id'], status='运行中')
        self.assertFalse(self.api.set_clear_data(True)['ok'])
        self.assertEqual(len(self.db.rows('SELECT * FROM drafts')), 1)
        self.assertEqual(list(self.path.glob('before-clear-*')), [])
