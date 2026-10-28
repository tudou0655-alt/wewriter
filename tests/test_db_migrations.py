"""Versioned SQLite schema migration tests."""
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from wewriter.db import MIGRATIONS, SCHEMA, SCHEMA_VERSION, Database, NewerDatabaseError

STAMP = '2026-01-01T00:00:00+00:00'


def make_v1(path, **extra_columns):
    """Create a database as written by a v1 release: no cleared/error_code columns."""
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        for table, ddl in extra_columns.items():
            db.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + ddl)
        db.execute("INSERT INTO accounts(name,identifier,created_at) VALUES('旧账号','legacy',?)", (STAMP,))
        db.execute("INSERT INTO articles(source_key,account_name,title,url,published_at,content,fetched_at) "
                   "VALUES('k1','旧账号','旧文章','https://example.test/a',?,'正文',?)", (STAMP, STAMP))
        db.execute("INSERT INTO jobs(run_key,status,stage,config,created_at,updated_at) "
                   "VALUES('run-1','已完成','done','{}',?,?)", (STAMP, STAMP))
        db.execute('PRAGMA user_version=1')
        db.commit()
    finally:
        db.close()


def columns(path, table):
    db = sqlite3.connect(path)
    try:
        return [r[1] for r in db.execute('PRAGMA table_info(' + table + ')')]
    finally:
        db.close()


def version(path):
    db = sqlite3.connect(path)
    try:
        return db.execute('PRAGMA user_version').fetchone()[0]
    finally:
        db.close()


class MigrationTest(unittest.TestCase):
    def setUp(self):
        workspace_temp = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def assert_current_schema(self, path):
        self.assertEqual(version(path), SCHEMA_VERSION)
        self.assertIn('cleared', columns(path, 'articles'))
        self.assertIn('cleared', columns(path, 'drafts'))
        self.assertIn('error_code', columns(path, 'jobs'))
        self.assertIn('funnel', columns(path, 'jobs'))

    def test_v2_database_gains_funnel_column(self):
        path = self.path / 'v2.sqlite'
        make_v1(path, articles='cleared INTEGER NOT NULL DEFAULT 0', drafts='cleared INTEGER NOT NULL DEFAULT 0',
                jobs="error_code TEXT NOT NULL DEFAULT ''")
        with closing(sqlite3.connect(path)) as raw:
            raw.execute('PRAGMA user_version=2')
            raw.commit()
        db = Database(path)
        self.assertEqual(SCHEMA_VERSION, 6)
        self.assert_current_schema(path)
        self.assertEqual(db.rows('SELECT run_key, funnel FROM jobs'), [{'run_key': 'run-1', 'funnel': ''}])
        db.update_job(db.rows('SELECT id FROM jobs')[0]['id'], funnel='{"counts": {}}')
        Database(path)
        self.assertEqual(db.rows('SELECT funnel FROM jobs')[0]['funnel'], '{"counts": {}}')

    def test_new_database_is_current(self):
        path = self.path / 'new.sqlite'
        Database(path)
        self.assert_current_schema(path)

    def test_v1_database_upgrades_and_keeps_data(self):
        path = self.path / 'v1.sqlite'
        make_v1(path)
        db = Database(path)
        self.assert_current_schema(path)
        self.assertEqual([a['identifier'] for a in db.accounts()], ['legacy'])
        article = db.rows('SELECT title, content, cleared FROM articles')[0]
        self.assertEqual((article['title'], article['content'], article['cleared']), ('旧文章', '正文', 0))
        self.assertEqual(db.rows('SELECT error_code FROM jobs')[0]['error_code'], '')

    def test_partially_upgraded_v1_database(self):
        path = self.path / 'partial.sqlite'
        make_v1(path, articles='cleared INTEGER NOT NULL DEFAULT 0')
        Database(path)
        self.assert_current_schema(path)

    def test_repeated_startup_is_idempotent(self):
        path = self.path / 'repeat.sqlite'
        make_v1(path)
        Database(path)
        before = {t: columns(path, t) for t in ('articles', 'drafts', 'jobs')}
        Database(path)
        Database(path)
        self.assertEqual({t: columns(path, t) for t in before}, before)
        self.assertEqual(version(path), SCHEMA_VERSION)
        self.assertEqual(len(Database(path).accounts()), 1)

    def test_newer_database_is_refused_without_changes(self):
        path = self.path / 'newer.sqlite'
        make_v1(path)
        raw = sqlite3.connect(path)
        raw.execute('PRAGMA user_version=%d' % (SCHEMA_VERSION + 1))
        raw.commit()
        raw.close()

        def snapshot():
            db = sqlite3.connect(path)
            try:
                return (db.execute('PRAGMA user_version').fetchone()[0],
                        db.execute('PRAGMA journal_mode').fetchone()[0],
                        db.execute('SELECT type, name, sql FROM sqlite_master ORDER BY name').fetchall(),
                        db.execute('SELECT * FROM accounts').fetchall(),
                        db.execute('SELECT * FROM articles').fetchall(),
                        db.execute('SELECT * FROM jobs').fetchall())
            finally:
                db.close()

        before = snapshot()
        with self.assertRaisesRegex(ValueError, '升级软件'):
            Database(path)
        self.assertEqual(snapshot(), before)
        self.assertNotIn('cleared', columns(path, 'articles'))

    def test_restore_v1_backup_upgrades_it(self):
        current = Database(self.path / 'current.sqlite')
        current.add_account('当前账号', 'current')
        backup = self.path / 'v1-backup.sqlite'
        make_v1(backup)
        current.restore(backup)
        self.assert_current_schema(current.path)
        self.assertEqual([a['identifier'] for a in current.accounts()], ['legacy'])
        self.assertEqual(version(backup), 1)

    def test_restore_rejects_newer_backup_without_damage(self):
        current = Database(self.path / 'current.sqlite')
        current.add_account('当前账号', 'current')
        backup = self.path / 'newer-backup.sqlite'
        Database(backup)
        raw = sqlite3.connect(backup)
        raw.execute('PRAGMA user_version=%d' % (SCHEMA_VERSION + 1))
        raw.commit()
        raw.close()
        with self.assertRaisesRegex(ValueError, '更新版本'):
            current.restore(backup)
        self.assertEqual([a['identifier'] for a in current.accounts()], ['current'])
        self.assertEqual(version(current.path), SCHEMA_VERSION)

    def make_current(self):
        current = Database(self.path / 'current.sqlite')
        current.add_account('当前账号', 'current')
        current.save_config({**current.config(), 'schedule_enabled': True})
        return current

    def snapshot(self, db):
        with closing(sqlite3.connect(db.path)) as raw:
            return (raw.execute('PRAGMA user_version').fetchone()[0],
                    raw.execute('SELECT identifier FROM accounts ORDER BY id').fetchall(),
                    raw.execute('SELECT value FROM settings ORDER BY key').fetchall(),
                    raw.execute('SELECT type, name, sql FROM sqlite_master ORDER BY name').fetchall())

    def assert_no_leftovers(self):
        self.assertEqual(list(self.path.glob('*.restore-*')), [])

    def assert_failed_restore_keeps_current(self, backup, error, fault=nullcontext):
        current = self.make_current()
        before = self.snapshot(current)
        with fault(), self.assertRaises(error):
            current.restore(backup)
        self.assertEqual(self.snapshot(current), before)
        self.assertTrue(current.config()['schedule_enabled'])
        with closing(sqlite3.connect(current.path)) as raw:
            self.assertEqual(raw.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(raw.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assert_no_leftovers()

    def test_restore_success_swaps_atomically_and_cleans_up(self):
        current = self.make_current()
        backup = self.path / 'v1-backup.sqlite'
        make_v1(backup)
        original = backup.read_bytes()
        current.restore(backup)
        self.assertEqual([a['identifier'] for a in current.accounts()], ['legacy'])
        self.assertFalse(current.config()['schedule_enabled'])
        self.assertEqual(backup.read_bytes(), original)
        with closing(sqlite3.connect(current.path)) as raw:
            self.assertEqual(raw.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assert_no_leftovers()

    def test_restore_rejects_corrupt_backup(self):
        backup = self.path / 'corrupt.sqlite'
        backup.write_bytes(b'SQLite format 3\x00' + b'\xff' * 4096)
        self.assert_failed_restore_keeps_current(backup, ValueError)

    def test_restore_rejects_incomplete_backup(self):
        backup = self.path / 'incomplete.sqlite'
        with closing(sqlite3.connect(backup)) as raw:
            raw.execute('CREATE TABLE accounts (id INTEGER PRIMARY KEY)')
            raw.execute('PRAGMA user_version=1')
            raw.commit()
        self.assert_failed_restore_keeps_current(backup, ValueError)

    def test_restore_future_backup_keeps_current(self):
        backup = self.path / 'future.sqlite'
        make_v1(backup)
        with closing(sqlite3.connect(backup)) as raw:
            raw.execute('PRAGMA user_version=%d' % (SCHEMA_VERSION + 1))
            raw.commit()
        self.assert_failed_restore_keeps_current(backup, NewerDatabaseError)

    def test_restore_migration_failure_keeps_current(self):
        backup = self.path / 'v1-backup.sqlite'
        make_v1(backup)
        failing = Mock(side_effect=sqlite3.OperationalError('injected migration failure'))
        self.assert_failed_restore_keeps_current(
            backup, ValueError, lambda: patch.dict(MIGRATIONS, {2: failing}))
        failing.assert_called_once()

    def test_restore_replace_failure_keeps_current(self):
        backup = self.path / 'v1-backup.sqlite'
        make_v1(backup)
        replace = Mock(side_effect=PermissionError(13, 'injected replace failure'))
        self.assert_failed_restore_keeps_current(
            backup, ValueError, lambda: patch('wewriter.db.os.replace', replace))
        replace.assert_called_once()

    def test_restore_succeeds_when_wal_fails_after_replace(self):
        current = self.make_current()
        backup = self.path / 'v1-backup.sqlite'
        make_v1(backup)
        wal = Mock(side_effect=sqlite3.OperationalError('injected WAL failure'))
        with patch.object(Database, '_enable_wal', wal), self.assertLogs('wewriter', 'WARNING') as logs:
            current.restore(backup)
        wal.assert_called_once()
        self.assertIn('恢复完成后未能切换回 WAL 模式', logs.output[0])
        self.assertEqual([a['identifier'] for a in current.accounts()], ['legacy'])
        self.assertFalse(current.config()['schedule_enabled'])
        self.assertEqual(version(current.path), SCHEMA_VERSION)
        self.assert_no_leftovers()
        with closing(sqlite3.connect(current.path)) as raw:
            self.assertEqual(raw.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(raw.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        Database(current.path)
        with closing(sqlite3.connect(current.path)) as raw:
            self.assertEqual(raw.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_wal_failure_does_not_mask_replace_failure(self):
        current = self.make_current()
        before = self.snapshot(current)
        backup = self.path / 'v1-backup.sqlite'
        make_v1(backup)
        with patch('wewriter.db.os.replace', side_effect=PermissionError(13, 'injected replace failure')), \
                patch.object(Database, '_enable_wal', side_effect=sqlite3.OperationalError('injected WAL failure')), \
                self.assertLogs('wewriter', 'WARNING') as logs, \
                self.assertRaisesRegex(ValueError, '无法替换当前数据库'):
            current.restore(backup)
        self.assertIn('恢复中止后未能切换回 WAL 模式', logs.output[0])
        self.assertEqual(self.snapshot(current), before)
        self.assert_no_leftovers()

    def test_restore_rejects_unversioned_backup(self):
        current = Database(self.path / 'current.sqlite')
        backup = self.path / 'unversioned.sqlite'
        raw = sqlite3.connect(backup)
        raw.executescript(SCHEMA)
        raw.close()
        with self.assertRaisesRegex(ValueError, '备份版本不匹配'):
            current.restore(backup)


if __name__ == '__main__':
    unittest.main()
