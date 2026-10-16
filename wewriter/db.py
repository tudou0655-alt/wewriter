import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import defaults, migrate


def stamp():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_items (
 parent_job_id INTEGER NOT NULL, article_id INTEGER NOT NULL, child_job_id INTEGER NOT NULL,
 PRIMARY KEY(parent_job_id,article_id));
CREATE TABLE IF NOT EXISTS research_cache (job_id INTEGER PRIMARY KEY, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_refs (url TEXT PRIMARY KEY, review_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS article_metrics (
 article_id INTEGER PRIMARY KEY, reads INTEGER, likes INTEGER, observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS article_images (
 article_id INTEGER NOT NULL REFERENCES articles(id), position INTEGER NOT NULL,
 source_url TEXT NOT NULL, local_path TEXT NOT NULL, alt_text TEXT NOT NULL DEFAULT '',
 width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(article_id,position));
CREATE TABLE IF NOT EXISTS job_metrics (
 job_id INTEGER NOT NULL, article_id INTEGER NOT NULL, reads INTEGER, likes INTEGER,
 observed_at TEXT NOT NULL, PRIMARY KEY(job_id,article_id));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, identifier TEXT NOT NULL UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS articles (
 id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, account_name TEXT NOT NULL,
 title TEXT NOT NULL, url TEXT NOT NULL, published_at TEXT NOT NULL,
 content TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '',
 fetched_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS articles_published ON articles(published_at);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, run_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
 stage TEXT NOT NULL, config TEXT NOT NULL, selected TEXT NOT NULL DEFAULT '[]',
 warnings TEXT NOT NULL DEFAULT '[]', message TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyses (
 article_id INTEGER NOT NULL REFERENCES articles(id), cache_key TEXT NOT NULL,
 result TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(article_id,cache_key));
CREATE TABLE IF NOT EXISTS drafts (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
 title TEXT NOT NULL, body TEXT NOT NULL, sources TEXT NOT NULL, report TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT '待审核', revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS draft_versions (
 id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES drafts(id),
 revision INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(draft_id, revision));
"""


def _add_column(db, table, column, ddl):
    if column not in {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')}:
        db.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' ' + ddl)


def _migrate_v2(db):
    # v1 databases from older releases may lack these columns.
    for table in ('articles', 'drafts'):
        _add_column(db, table, 'cleared', 'INTEGER NOT NULL DEFAULT 0')
    # Structured failure reason (see engine.ErrorCode); '' = no error.
    _add_column(db, 'jobs', 'error_code', "TEXT NOT NULL DEFAULT ''")


# MIGRATIONS[n] upgrades a version n-1 database to version n. Steps must be idempotent,
# because a database may have been partially upgraded or opened by an older release.
MIGRATIONS = {2: _migrate_v2}
SCHEMA_VERSION = max(MIGRATIONS)


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            self.check_version(db)
            db.execute("PRAGMA journal_mode=WAL")
            self.migrate_schema(db)

    @staticmethod
    def check_version(db):
        """Refuse a database written by a newer release before anything modifies it."""
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("数据库来自更新版本的软件（版本 %d，当前支持 %d），请升级软件后再打开"
                             % (version, SCHEMA_VERSION))
        return version

    @classmethod
    def migrate_schema(cls, db):
        """Create missing tables and apply pending migrations; return the resulting version."""
        version = cls.check_version(db)
        db.executescript(SCHEMA)
        for target in range(2, SCHEMA_VERSION + 1):
            if target > version:
                MIGRATIONS[target](db)
        db.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
        return SCHEMA_VERSION

    def clear_records(self, kind, ids=None):
        """Purge content, retaining minimal identity to prevent accidental regeneration."""
        if kind not in ('articles', 'drafts'):
            raise ValueError('无效清理类型')
        image_paths = []
        with self.connect() as db:
            if db.execute("SELECT 1 FROM jobs WHERE status='运行中' LIMIT 1").fetchone():
                raise ValueError('任务正在运行，请等待完成或停止后再清理')
            if ids is not None:
                ids = sorted({int(i) for i in ids})
                if not ids:
                    return 0
            db.execute('CREATE TEMP TABLE cleanup_ids(id INTEGER PRIMARY KEY)')
            if ids is None:
                db.execute('INSERT INTO cleanup_ids SELECT id FROM ' + kind + ' WHERE cleared=0')
            else:
                db.executemany('INSERT INTO cleanup_ids VALUES(?)', ((i,) for i in ids))
                db.execute('DELETE FROM cleanup_ids WHERE id NOT IN (SELECT id FROM ' + kind + ' WHERE cleared=0)')
            total = db.execute('SELECT COUNT(*) FROM cleanup_ids').fetchone()[0]
            if kind == 'articles':
                image_paths = [r[0] for r in db.execute(
                    'SELECT local_path FROM article_images WHERE article_id IN (SELECT id FROM cleanup_ids)').fetchall()]
                db.execute('DELETE FROM article_images WHERE article_id IN (SELECT id FROM cleanup_ids)')
                db.execute('DELETE FROM analyses WHERE article_id IN (SELECT id FROM cleanup_ids)')
                db.execute('DELETE FROM article_metrics WHERE article_id IN (SELECT id FROM cleanup_ids)')
                db.execute('DELETE FROM source_refs WHERE url IN (SELECT url FROM articles WHERE id IN (SELECT id FROM cleanup_ids))')
                db.execute("UPDATE articles SET content='',content_hash='',error='',cleared=1 WHERE id IN (SELECT id FROM cleanup_ids)")
            else:
                for row in db.execute('SELECT id,sources FROM drafts WHERE id IN (SELECT id FROM cleanup_ids)').fetchall():
                    refs = [{k:s[k] for k in ('article_id','source_id','url') if k in s} for s in json.loads(row['sources']) if 'article_id' in s]
                    db.execute("UPDATE drafts SET body='',report='',sources=?,status='已清理',cleared=1 WHERE id=?", (json.dumps(refs),row['id']))
                db.execute('DELETE FROM draft_versions WHERE draft_id IN (SELECT id FROM cleanup_ids)')
                db.execute("UPDATE jobs SET status='已清理',message='稿件已由用户清理，原任务不再生成' WHERE id IN (SELECT job_id FROM drafts WHERE id IN (SELECT id FROM cleanup_ids))")
                db.execute('DELETE FROM research_cache WHERE job_id IN (SELECT job_id FROM drafts WHERE id IN (SELECT id FROM cleanup_ids))')
        self.remove_image_files(image_paths)
        return total

    def clear_all(self):
        """Remove all business records while preserving settings and external secret files."""
        tables = ('draft_versions', 'drafts', 'analyses', 'research_cache', 'batch_items',
                  'article_images',
                  'job_metrics', 'article_metrics', 'source_refs', 'jobs', 'articles', 'accounts')
        with self.connect() as db:
            if db.execute("SELECT 1 FROM jobs WHERE status='运行中' LIMIT 1").fetchone():
                raise ValueError('任务正在运行，请先停止任务并退出软件')
            removed = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}
            for table in tables:
                db.execute('DELETE FROM ' + table)
            row = db.execute("SELECT value FROM settings WHERE key='config'").fetchone()
            if row:
                config = json.loads(row['value'])
                config['schedule_enabled'] = False
                db.execute("UPDATE settings SET value=? WHERE key='config'",
                           (json.dumps(config, ensure_ascii=False),))
        with self.connect() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('数据库清理后完整性检查失败，请从自动备份恢复')
        image_root = (self.path.parent / 'article-images').resolve()
        if image_root.parent == self.path.parent.resolve() and image_root.name == 'article-images':
            shutil.rmtree(image_root, ignore_errors=True)
        return removed

    def article_images(self, article_id):
        return self.rows('SELECT * FROM article_images WHERE article_id=? ORDER BY position',
                         (int(article_id),))

    def replace_article_images(self, article_id, images):
        with self.connect() as db:
            db.execute('DELETE FROM article_images WHERE article_id=?', (int(article_id),))
            db.executemany('''INSERT INTO article_images
                (article_id,position,source_url,local_path,alt_text,width,height)
                VALUES(?,?,?,?,?,?,?)''', ((int(article_id), int(item['position']), item['source_url'],
                    item['local_path'], item.get('alt_text', ''), int(item.get('width', 0)),
                    int(item.get('height', 0))) for item in images))

    def remove_image_files(self, paths):
        root = (self.path.parent / 'article-images').resolve()
        for value in paths:
            try:
                path = (self.path.parent / str(value)).resolve()
                if path.is_relative_to(root) and path.is_file():
                    path.unlink()
                    try:
                        path.parent.rmdir()
                    except OSError:
                        pass
            except (OSError, ValueError):
                pass

    def prune_missing_images(self):
        missing = []
        root = self.path.parent.resolve()
        for item in self.rows('SELECT article_id,position,local_path FROM article_images'):
            try:
                path = (self.path.parent / item['local_path']).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    missing.append((item['article_id'], item['position']))
            except (OSError, ValueError):
                missing.append((item['article_id'], item['position']))
        if missing:
            with self.connect() as db:
                db.executemany('DELETE FROM article_images WHERE article_id=? AND position=?', missing)
        return len(missing)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, params=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        with self.connect() as db:
            return db.execute(sql, params).lastrowid

    def config(self):
        values = self.rows("SELECT value FROM settings WHERE key='config'")
        result = defaults()
        if values:
            result.update(json.loads(values[0]["value"]))
        return migrate(result)

    def save_config(self, value):
        self.execute("INSERT INTO settings VALUES('config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(value, ensure_ascii=False),))

    def accounts(self, enabled=False):
        return self.rows("SELECT * FROM accounts" + (" WHERE enabled=1" if enabled else "") + " ORDER BY id")

    def add_account(self, name, identifier):
        if not name.strip() or not identifier.strip():
            raise ValueError("公众号名称和标识不能为空")
        return self.execute("INSERT INTO accounts(name,identifier,created_at) VALUES(?,?,?)",
                            (name.strip(), identifier.strip(), stamp()))

    def save_article(self, article):
        with self.connect() as db:
            db.execute("""INSERT INTO articles(source_key,account_name,title,url,published_at,fetched_at)
              VALUES(:source_key,:account_name,:title,:url,:published_at,:fetched_at)
              ON CONFLICT(source_key) DO NOTHING""", {**article, "fetched_at": stamp()})
            return db.execute("SELECT id FROM articles WHERE source_key=?", (article["source_key"],)).fetchone()[0]

    def job(self, run_key, config):
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO jobs(run_key,status,stage,config,created_at,updated_at)
                          VALUES(?,'等待中','采集',?,?,?)""", (run_key, json.dumps(config, ensure_ascii=False), stamp(), stamp()))
            return dict(db.execute("SELECT * FROM jobs WHERE run_key=?", (run_key,)).fetchone())

    def update_job(self, job_id, **fields):
        allowed = {"status", "stage", "selected", "warnings", "message", "error_code"}
        if not fields or not set(fields).issubset(allowed):
            raise ValueError("无效任务字段")
        fields["updated_at"] = stamp()
        self.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                     (*fields.values(), job_id))

    def recover(self):
        self.execute("UPDATE jobs SET status='已中断',message='上次运行异常退出，可选择任务重试' WHERE status='运行中'")

    def save_draft(self, job_id, title, body, sources, report):
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO drafts(job_id,title,body,sources,report,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?)""", (job_id, title, body, json.dumps(sources, ensure_ascii=False), report, stamp(), stamp()))
            draft = db.execute("SELECT * FROM drafts WHERE job_id=?", (job_id,)).fetchone()
            db.execute("INSERT OR IGNORE INTO draft_versions(draft_id,revision,title,body,created_at) VALUES(?,?,?,?,?)",
                       (draft["id"], draft["revision"], draft["title"], draft["body"], stamp()))
            return draft["id"]

    def edit_draft(self, draft_id, title, body, status):
        if not title.strip() or not body.strip():
            raise ValueError("标题和正文不能为空")
        with self.connect() as db:
            current = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            revision = current["revision"] + 1
            db.execute("UPDATE drafts SET title=?,body=?,status=?,revision=?,updated_at=? WHERE id=?",
                       (title.strip(), body.strip(), status, revision, stamp(), draft_id))
            db.execute("INSERT INTO draft_versions(draft_id,revision,title,body,created_at) VALUES(?,?,?,?,?)",
                       (draft_id, revision, title.strip(), body.strip(), stamp()))

    def backup(self, target):
        if Path(target).resolve() == self.path.resolve():
            raise ValueError("不能覆盖正在使用的数据库")
        with self.connect() as source:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()

    def restore(self, source):
        if Path(source).resolve() == self.path.resolve():
            raise ValueError("不能从当前数据库恢复")
        origin = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if origin.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份数据库损坏")
            version = origin.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("备份来自更新版本的软件，请先升级后再恢复")
            if version < 1:
                raise ValueError("备份版本不匹配")
            tables = {r[0] for r in origin.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"accounts", "articles", "jobs", "drafts", "settings", "analyses", "draft_versions"} <= tables:
                raise ValueError("不是本软件的备份文件")
            with self.connect() as dest:
                origin.backup(dest)
        finally:
            origin.close()
        self.recover()
        with self.connect() as db:
            self.migrate_schema(db)
        self.prune_missing_images()
        config = self.config()
        config["schedule_enabled"] = False
        self.save_config(config)
