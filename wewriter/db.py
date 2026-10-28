import json
import logging
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import defaults, migrate
from .logs import redact, redact_warnings

logger = logging.getLogger('wewriter')


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
-- Provenance of one job's metric observation (see engine.METRIC_SOURCES). Kept in its own
-- table so the positional INSERTs into job_metrics/article_metrics stay valid.
-- When each value kept in article_metrics was observed ('' = no value). Separate for the
-- same reason; article_metrics.observed_at is the time of its latest update.
CREATE TABLE IF NOT EXISTS article_metric_times (
 article_id INTEGER PRIMARY KEY, reads_observed_at TEXT NOT NULL DEFAULT '',
 likes_observed_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS metric_notes (
 job_id INTEGER NOT NULL, article_id INTEGER NOT NULL, source TEXT NOT NULL DEFAULT '',
 reads_status TEXT NOT NULL DEFAULT '', likes_status TEXT NOT NULL DEFAULT '',
 previous_reads INTEGER, previous_observed_at TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(job_id,article_id));
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


def _migrate_v3(db):
    # Structured collection funnel of the latest attempt (JSON, see engine.Funnel); '' = none.
    _add_column(db, 'jobs', 'funnel', "TEXT NOT NULL DEFAULT ''")


def _redact_funnel(raw):
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return raw
    if not isinstance(data, dict) or not isinstance(data.get('accounts'), dict):
        return raw
    for account in data['accounts'].values():
        if isinstance(account, dict):
            for key in ('error', 'coverage_reason'):
                if account.get(key):
                    account[key] = redact(account[key])
            coverage = account.get('coverage')
            if isinstance(coverage, dict) and coverage.get('reason'):
                coverage['reason'] = redact(coverage['reason'])
    collection = data.get('collection')
    if isinstance(collection, dict) and isinstance(collection.get('warnings'), list):
        collection['warnings'] = [redact(w) for w in collection['warnings']]
    return json.dumps(data, ensure_ascii=False)


def _migrate_v4(db):
    # One-time redaction of historical diagnostics written before 0.10.4. Only diagnostic
    # fields are touched (task message/warnings/funnel errors, article error); tasks, article
    # bodies, drafts and reports are never rewritten or deleted. redact() is idempotent.
    for row in db.execute('SELECT id, message, warnings, funnel FROM jobs').fetchall():
        values = (redact(row[1]), redact_warnings(row[2]), _redact_funnel(row[3]))
        if values != (row[1], row[2], row[3]):
            db.execute('UPDATE jobs SET message=?, warnings=?, funnel=? WHERE id=?', (*values, row[0]))
    for row in db.execute("SELECT id, error FROM articles WHERE error<>''").fetchall():
        if redact(row[1]) != row[1]:
            db.execute('UPDATE articles SET error=? WHERE id=?', (redact(row[1]), row[0]))


def _migrate_v5(db):
    # Background web searches started but not yet retrieved (one per task). A retry fetches the
    # stored response instead of starting, and paying for, a new search.
    db.execute('''CREATE TABLE IF NOT EXISTS research_pending (
        job_id INTEGER PRIMARY KEY, response_id TEXT NOT NULL, created_at TEXT NOT NULL)''')


# MIGRATIONS[n] upgrades a version n-1 database to version n. Steps must be idempotent,
# because a database may have been partially upgraded or opened by an older release.
def _migrate_v6(db):
    # Example articles the customer likes ('good') or dislikes ('bad'); the style card is distilled
    # from them. skeleton is the short form sent to the writer (title, opening, headings, samples).
    db.execute('''CREATE TABLE IF NOT EXISTS examples (
        id INTEGER PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'good', title TEXT NOT NULL, body TEXT NOT NULL,
        score INTEGER, avoid TEXT NOT NULL DEFAULT '', skeleton TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)''')


MIGRATIONS = {2: _migrate_v2, 3: _migrate_v3, 4: _migrate_v4, 5: _migrate_v5, 6: _migrate_v6}
SCHEMA_VERSION = max(MIGRATIONS)
RESTORE_TABLES = {"accounts", "articles", "jobs", "drafts", "settings", "analyses", "draft_versions"}


class NewerDatabaseError(ValueError):
    """The database was written by a newer release than this one supports."""


def _remove_sqlite_files(path):
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


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
            raise NewerDatabaseError("数据库来自更新版本的软件（版本 %d，当前支持 %d），请升级软件后再打开"
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
                db.execute('DELETE FROM article_metric_times WHERE article_id IN (SELECT id FROM cleanup_ids)')
                db.execute('DELETE FROM source_refs WHERE url IN (SELECT url FROM articles WHERE id IN (SELECT id FROM cleanup_ids))')
                db.execute("UPDATE articles SET content='',content_hash='',error='',cleared=1 WHERE id IN (SELECT id FROM cleanup_ids)")
            else:
                for row in db.execute('SELECT id,sources FROM drafts WHERE id IN (SELECT id FROM cleanup_ids)').fetchall():
                    refs = [{k:s[k] for k in ('article_id','source_id','url') if k in s} for s in json.loads(row['sources']) if 'article_id' in s]
                    db.execute("UPDATE drafts SET body='',report='',sources=?,status='已清理',cleared=1 WHERE id=?", (json.dumps(refs),row['id']))
                # Stage checkpoints can contain earlier full drafts. Remove
                # only this draft's job keys; article summaries remain reusable.
                for job_row in db.execute('SELECT job_id FROM drafts WHERE id IN (SELECT id FROM cleanup_ids)').fetchall():
                    db.execute('DELETE FROM analyses WHERE cache_key LIKE ?',
                               (f't040-%:{job_row[0]}:%',))
                db.execute('DELETE FROM draft_versions WHERE draft_id IN (SELECT id FROM cleanup_ids)')
                db.execute("UPDATE jobs SET status='已清理',message='稿件已由用户清理，原任务不再生成' WHERE id IN (SELECT job_id FROM drafts WHERE id IN (SELECT id FROM cleanup_ids))")
                db.execute('DELETE FROM research_cache WHERE job_id IN (SELECT job_id FROM drafts WHERE id IN (SELECT id FROM cleanup_ids))')
        self.remove_image_files(image_paths)
        return total

    def clear_all(self, *, preserve_accounts=False):
        """Remove all business records while preserving settings and external secret files."""
        tables = ('draft_versions', 'drafts', 'analyses', 'research_cache', 'research_pending', 'batch_items',
                  'article_images', 'metric_notes', 'article_metric_times',
                  'job_metrics', 'article_metrics', 'source_refs', 'jobs', 'articles', 'accounts')
        if preserve_accounts:
            tables = tuple(t for t in tables if t != 'accounts')
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
        result = json.loads(values[0]["value"]) if values else {}
        # The old saved OpenAI search is now switched to the requested DeepSeek
        # default for new jobs. Historical job snapshots still migrate as OpenAI.
        if values and 'search_provider' not in result:
            result['search_provider'] = 'deepseek'
            result['search_model'] = 'deepseek-flash'
            result['search_url'] = defaults()['search_url']
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
        allowed = {"status", "stage", "selected", "warnings", "message", "error_code", "funnel"}
        if not fields or not set(fields).issubset(allowed):
            raise ValueError("无效任务字段")
        # Persistence boundary for diagnostics: upstream error text may carry credentials.
        if "message" in fields:
            fields["message"] = redact(fields["message"])
        if "warnings" in fields:
            fields["warnings"] = redact_warnings(fields["warnings"])
        if "funnel" in fields:
            fields["funnel"] = _redact_funnel(fields["funnel"])
        fields["updated_at"] = stamp()
        self.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                     (*fields.values(), job_id))

    def set_article_error(self, article_id, message):
        self.execute("UPDATE articles SET error=? WHERE id=?", (redact(message), article_id))

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
            # An edited text needs a new review: an approval covers the text it saw.
            if (title.strip(), body.strip()) != (current["title"], current["body"]):
                status = '待审核'
            revision = current["revision"] + 1
            db.execute("UPDATE drafts SET title=?,body=?,status=?,revision=?,updated_at=? WHERE id=?",
                       (title.strip(), body.strip(), status, revision, stamp(), draft_id))
            db.execute("INSERT INTO draft_versions(draft_id,revision,title,body,created_at) VALUES(?,?,?,?,?)",
                       (draft_id, revision, title.strip(), body.strip(), stamp()))

    def add_example(self, title, body, kind='good', score=None, avoid='', origin=''):
        """Store an example article for the style card; returns its id."""
        from .style import skeleton
        title, body = str(title or '').strip(), str(body or '').strip()
        if not body:
            raise ValueError('范文正文不能为空')
        if kind not in ('good', 'bad'):
            raise ValueError('范文类型只能是范文或反例')
        if score is not None and (type(score) is not int or not 0 <= score <= 100):
            raise ValueError('分数请填 0 到 100 的整数')
        with self.connect() as db:
            return db.execute('''INSERT INTO examples(kind,title,body,score,avoid,skeleton,origin,created_at)
                                 VALUES(?,?,?,?,?,?,?,?)''',
                              (kind, title[:120], body, score, str(avoid or '').strip()[:1000],
                               skeleton(title, body, avoid), str(origin)[:200], stamp())).lastrowid

    def examples(self):
        return self.rows('SELECT id,kind,title,body,score,avoid,skeleton,origin,created_at FROM examples ORDER BY id')

    def delete_example(self, example_id):
        self.execute('DELETE FROM examples WHERE id=?', (int(example_id),))

    def revise_draft(self, draft_id, revision, title, body, report, sources=None):
        """Save a machine revision (the re-run AI review) as a new version; earlier versions stay.

        Refused when the draft changed since `revision` was read, so a manual edit made while the
        review was running is never overwritten."""
        with self.connect() as db:
            current = db.execute("SELECT revision FROM drafts WHERE id=? AND cleared=0", (draft_id,)).fetchone()
            if current is None:
                raise ValueError('稿件不存在或已清理')
            if current["revision"] != revision:
                raise ValueError('稿件在二审期间被修改过，二审结果未保存；请重新补跑')
            db.execute("UPDATE drafts SET title=?,body=?,report=?,status='待审核',revision=?,updated_at=? WHERE id=?",
                       (title, body, report, revision + 1, stamp(), draft_id))
            if sources is not None:      # sources the automatic check added
                db.execute("UPDATE drafts SET sources=? WHERE id=?", (json.dumps(sources, ensure_ascii=False), draft_id))
            db.execute("INSERT INTO draft_versions(draft_id,revision,title,body,created_at) VALUES(?,?,?,?,?)",
                       (draft_id, revision + 1, title, body, stamp()))
        return revision + 1

    # Manual review states. '已审核' is the pre-T-010 name of '已通过'.
    REVIEW_STATES = ('待审核', '需修改', '已通过')

    @staticmethod
    def review_state(status):
        return '已通过' if status == '已审核' else (status if status in Database.REVIEW_STATES else '待审核')

    def set_draft_status(self, draft_id, status, note=''):
        """Record a manual review decision without a new text revision.

        The machine-generated report is never rewritten: the decision is
        appended as a dated manual-review line, so the audit trail stays."""
        if status not in self.REVIEW_STATES:
            raise ValueError('审核状态无效')
        with self.connect() as db:
            current = db.execute("SELECT report FROM drafts WHERE id=? AND cleared=0", (draft_id,)).fetchone()
            if current is None:
                raise ValueError('稿件不存在或已清理')
            line = f"\n【人工审核记录】{stamp()} · {status}" + (f" · {note.strip()[:500]}" if note.strip() else '')
            db.execute("UPDATE drafts SET status=?,report=?,updated_at=? WHERE id=?",
                       (status, current["report"] + line, stamp(), draft_id))

    def qualified_drafts(self, job_id):
        """Drafts of a batch (or single) task that a person has approved."""
        return self.rows("""SELECT COUNT(*) n FROM drafts d WHERE d.cleared=0 AND d.status IN ('已通过','已审核')
                            AND (d.job_id=? OR d.job_id IN (SELECT child_job_id FROM batch_items WHERE parent_job_id=?))""",
                         (job_id, job_id))[0]['n']

    def task_drafts(self, job_id):
        return self.rows("""SELECT d.id,d.title,d.status,d.updated_at FROM drafts d WHERE d.cleared=0
                            AND (d.job_id=? OR d.job_id IN (SELECT child_job_id FROM batch_items WHERE parent_job_id=?))
                            ORDER BY d.id""", (job_id, job_id))

    def top_jobs(self, limit, offset=0):
        """Tasks shown to people: batch children are folded into their parent."""
        where = "id NOT IN (SELECT child_job_id FROM batch_items)"
        total = self.rows("SELECT COUNT(*) n FROM jobs WHERE " + where)[0]['n']
        return total, self.rows("SELECT * FROM jobs WHERE " + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                                (limit, offset))

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
        """Validate and migrate the backup in a sibling temp file, then atomically swap it in.

        The live database is untouched unless every step succeeds.
        """
        source = Path(source).resolve()
        if source == self.path.resolve():
            raise ValueError("不能从当前数据库恢复")
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".restore-", suffix=".tmp", dir=self.path.parent)
        os.close(fd)
        staged = Path(name)
        try:
            self._stage_restore(source, staged)
            self._swap_in(staged)
        finally:
            _remove_sqlite_files(staged)
        self.prune_missing_images()

    def _stage_restore(self, source, staged):
        try:
            origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
            try:
                if origin.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("备份数据库损坏")
                dest = sqlite3.connect(staged)
                try:
                    origin.backup(dest)
                finally:
                    dest.close()
            finally:
                origin.close()
            with closing(sqlite3.connect(staged)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("备份数据库损坏")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise NewerDatabaseError("备份来自更新版本的软件，请先升级后再恢复")
                if version < 1:
                    raise ValueError("备份版本不匹配")
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not RESTORE_TABLES <= tables:
                    raise ValueError("不是本软件的备份文件")
        except sqlite3.Error:
            raise ValueError("备份数据库损坏或无法读取") from None
        try:
            staging = object.__new__(type(self))
            staging.path = staged
            with staging.connect() as db:
                self.migrate_schema(db)
            staging.recover()
            config = staging.config()
            config["schedule_enabled"] = False
            staging.save_config(config)
            with closing(sqlite3.connect(staged)) as db:
                ok = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except sqlite3.Error:
            raise ValueError("备份升级失败，原数据未改动") from None
        if not ok:
            raise ValueError("备份升级后校验失败，原数据未改动")

    def _swap_in(self, staged):
        # Fold the WAL into the main file and leave WAL mode so no stale -wal/-shm
        # can be replayed onto the restored file.
        busy = "数据库正在被使用，无法恢复。原数据未改动，请关闭正在运行的任务后重试"
        try:
            with closing(sqlite3.connect(self.path, timeout=20)) as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                mode = db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        except sqlite3.Error:
            raise ValueError(busy) from None
        replaced = False
        try:
            if mode.lower() != "delete":
                raise ValueError(busy)
            try:
                os.replace(staged, self.path)
            except OSError:
                raise ValueError("无法替换当前数据库，原数据未改动，请关闭占用数据库的程序后重试") from None
            replaced = True
        finally:
            # Returning to WAL is an optimisation only: Database() enables it again on next
            # start. A failure here must neither report a completed swap as failed nor mask
            # the error that aborted the swap.
            try:
                self._enable_wal()
            except sqlite3.Error:
                logger.warning("恢复%s后未能切换回 WAL 模式，将在下次启动时重试",
                               "完成" if replaced else "中止")

    def _enable_wal(self):
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            db.execute("PRAGMA journal_mode=WAL")
