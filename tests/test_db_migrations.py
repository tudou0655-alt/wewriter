"""Versioned SQLite schema migration tests."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wewriter.db import SCHEMA, SCHEMA_VERSION, Database

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
