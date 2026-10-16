"""Startup database failure handling. No window is ever shown."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from wewriter.db import SCHEMA_VERSION, Database, NewerDatabaseError

FAKE_SECRET = 'sk-FAKE0000STARTUP0000DUMMY'


class StartupErrorTest(unittest.TestCase):
    def setUp(self):
        workspace_temp = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def open_with_failure(self, error):
        root = Mock()
        with patch('main.Database', side_effect=error), patch('main.messagebox') as box, \
                self.assertLogs('wewriter', 'ERROR'):
            result = main.open_database(self.path / 'workspace.sqlite', root)
        self.assertIsNone(result)
        root.destroy.assert_called_once()
        box.showerror.assert_called_once()
        return box.showerror.call_args.args[1]

    def assert_safe(self, message):
        self.assertNotIn(str(self.path), message)
        self.assertNotIn(FAKE_SECRET, message)
        self.assertNotIn('Traceback', message)
        self.assertNotIn(':\\', message)

    def test_newer_database_message(self):
        message = self.open_with_failure(NewerDatabaseError('版本 %d %s' % (SCHEMA_VERSION + 1, self.path)))
        self.assertIn('最新版本', message)
        self.assert_safe(message)

    def test_corrupt_database_message(self):
        message = self.open_with_failure(sqlite3.DatabaseError('file is not a database ' + FAKE_SECRET))
        self.assertIn('损坏', message)
        self.assert_safe(message)

    def test_permission_or_lock_message(self):
        for error in (PermissionError(13, 'denied', str(self.path)), sqlite3.OperationalError('database is locked')):
            with self.subTest(type(error).__name__):
                message = self.open_with_failure(error)
                self.assertIn('权限', message)
                self.assert_safe(message)

    def test_real_newer_database_is_reported(self):
        path = self.path / 'workspace.sqlite'
        Database(path)
        raw = sqlite3.connect(path)
        raw.execute('PRAGMA user_version=%d' % (SCHEMA_VERSION + 1))
        raw.commit()
        raw.close()
        root = Mock()
        with patch('main.messagebox') as box, self.assertLogs('wewriter', 'ERROR'):
            self.assertIsNone(main.open_database(path, root))
        self.assertIn('最新版本', box.showerror.call_args.args[1])

    def test_success_returns_recovered_database(self):
        root = Mock()
        with patch('main.messagebox') as box:
            db = main.open_database(self.path / 'workspace.sqlite', root)
        self.assertIsInstance(db, Database)
        box.showerror.assert_not_called()
        root.destroy.assert_not_called()

    def test_main_stops_before_building_ui(self):
        argv = ['main.py', '--data-dir', str(self.path)]
        with patch('sys.argv', argv), patch('main.set_app_id'), patch('main.single_instance', return_value=True), \
                patch('main.tk.Tk') as tk_root, patch('main.App') as app, patch('main.Vault') as vault, \
                patch('main.Database', side_effect=sqlite3.DatabaseError('broken')), \
                patch('main.messagebox') as box, patch('wewriter.logs.setup'), \
                self.assertLogs('wewriter', 'ERROR'):
            self.assertEqual(main.main(), 1)
        app.assert_not_called()
        vault.assert_not_called()
        box.showerror.assert_called_once()
        tk_root.return_value.destroy.assert_called_once()
        tk_root.return_value.mainloop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
