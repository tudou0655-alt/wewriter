"""T-007 delivery reliability regressions. Every account, article, key and cookie is fictional;
no test reads real credentials, a real database or calls a real API."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from wewriter import BUILD_INFO_NAME, __version__, build_label
from wewriter.api import ApiError
from wewriter.config import defaults
from wewriter.db import SCHEMA_VERSION, Database
from wewriter.engine import Engine, format_funnel, load_funnel
from wewriter.logs import redact
from wewriter.ui import job_detail_text, job_status_label
from wewriter.weread import WeReadSource

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('verify_delivery_t007', ROOT / 'scripts' / 'verify_delivery.py')
verify_delivery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_delivery)

FAKE_BEARER = 'FAKE-T007-bearer-0000.dummy'
FAKE_KEY = 'sk-FAKE0000T007000RELIABILITY'
FAKE_SKILL = 'FAKE_skill_key_T007'
FAKE_COOKIE = 'FAKEskey0000T007'
# Special-character values found by review: ! and : inside values, HTML-escaped quotes.
SPECIAL = ('password=!FAKE_T007_ONLY!；client_secret=FAKE_T007:colon:tail；'
           'password=&#34;FAKE_T007!suffix&#34;')
SECRETS = (FAKE_BEARER, FAKE_KEY, FAKE_SKILL, FAKE_COOKIE,
           'FAKE_T007_ONLY', '!suffix', 'FAKE_T007!', 'colon:tail', 'FAKE_T007:colon')
REQUEST_ID = 'req-FAKE-T007'
LEAKY = (f'HTTP 401：API Key 无效；Authorization: Bearer {FAKE_BEARER}；model rejected {FAKE_KEY}；'
         f'skill_key={FAKE_SKILL}；{SPECIAL}；请求编号：{REQUEST_ID}；Cookie: wr_vid=1; wr_skey={FAKE_COOKIE}')
BODY = '这是一段虚构的公众号正文，仅用于离线测试可靠性回归。' * 8
BOOK = 'MP_WXS_123'


def item(slug, hours_ago=1, **extra):
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {'title': '虚构文章' + slug, 'url': 'https://mp.weixin.qq.com/s/t007-' + slug, 'posted_at': when, **extra}


class Vault:
    def get(self, name):
        return 'test-only-never-a-real-key'

    def has(self, name):
        return False


def make_source(listings, bodies=None, coverage=None):
    """A fictional source; listings/bodies/coverage may be mutated between runs."""
    class Source:
        list_calls = 0

        def __init__(self, *args):
            self.last_coverage = None

        def articles(self, account, limit):
            type(self).list_calls += 1
            self.last_coverage = (coverage or {}).get(account)
            value = listings[account]
            if isinstance(value, Exception):
                raise value
            return [dict(i) for i in value][:limit]

        def content(self, url):
            value = (bodies or {}).get(url.rsplit('t007-', 1)[-1], BODY + url)
            if isinstance(value, Exception):
                raise value
            return value
    return Source


class Writer:
    fail_titles = set()
    review_fails = False
    written = []

    def __init__(self, *args):
        pass

    def summary(self, article, content):
        return {'summary': '虚构摘要', 'facts': [], 'uncertainties': []}

    def analyze_style(self, content, title=''):
        return {'tone': '客观'}

    def write(self, sources, config):
        title = [s for s in sources if 'article_id' in s][0]['title']
        if title in type(self).fail_titles:
            raise ApiError('simulated write failure ' + LEAKY)
        type(self).written.append(title)
        return {'title': title + '稿', 'outline': ['要点'], 'review_notes': [],
                'body': '正文' + ''.join(f"[{s['source_id']}]" for s in sources)}

    def review(self, draft, sources, config):
        if type(self).review_fails:
            raise ApiError('simulated review failure')
        return {**draft, 'quality_notes': []}


class Research:
    def __init__(self, *args):
        pass

    def search(self, article):
        return {'notes': '虚构联网资料[W1]', 'retrieved_at': '2026-10-19',
                'sources': [{'source_id': 'W1', 'title': '虚构来源', 'url': 'https://example.org/t007'}]}


class NetworkResearch(Research):
    def search(self, article):
        raise ApiError('simulated timeout', kind='network')


class Base(unittest.TestCase):
    def setUp(self):
        workspace_temp = ROOT / '.local-data' / 'tests'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.path = Path(self.temp.name)
        self.db = Database(self.path / 't007.sqlite')
        self.config = defaults()
        self.config.update(comments_enabled=False, selection_mode='recent', writing_mode='combined',
                           min_sources=1, article_limit=10, lookback_hours=24)
        Writer.fail_titles, Writer.review_fails, Writer.written = set(), False, []
        self.notices = []

    def tearDown(self):
        self.temp.cleanup()

    def accounts(self, *names):
        for n, name in enumerate(names):
            self.db.add_account(name, 'acct%d' % n)

    def engine(self, source, research=Research):
        return Engine(self.db, Vault(), notify=self.notices.append, source_factory=source,
                      model_factory=Writer, research_factory=research)

    def job(self, key):
        return self.db.rows('SELECT * FROM jobs WHERE run_key=?', (key,))[0]

    def assert_clean(self, text):
        for secret in SECRETS:
            self.assertNotIn(secret, str(text))

    def batch_config(self, **extra):
        return dict(self.config, writing_mode='separate_web', selection_mode='hot', draft_count=2, **extra)


# ---------------------------------------------------------------------- A. redaction

class PersistedRedactionTest(Base):
    def test_collection_error_is_redacted_in_every_stored_diagnostic_and_backup(self):
        self.accounts('虚构公众号甲', '虚构公众号乙')
        source = make_source({'acct0': ApiError(LEAKY), 'acct1': [item('a1')]})
        self.assertEqual(self.engine(source).run('redact:1', self.config), '部分完成')
        job = self.job('redact:1')
        for field in ('message', 'warnings', 'funnel'):
            self.assert_clean(job[field])
        self.assertIn('HTTP 401', job['warnings'])
        self.assertIn(REQUEST_ID, job['warnings'])
        self.assert_clean(self.notices)
        backup = self.path / 'backup.sqlite'
        self.db.backup(backup)
        with closing(sqlite3.connect(backup)) as raw:
            tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            dump = ''.join(str(row) for t in tables for row in raw.execute('SELECT * FROM ' + t))
        self.assert_clean(dump)
        self.assertIn(REQUEST_ID, dump)

    def test_article_error_and_task_message_are_redacted_but_bodies_are_not_rewritten(self):
        self.accounts('虚构公众号甲')
        quoted_body = BODY + '原文引用了示例写法 Authorization: Bearer ' + FAKE_BEARER
        source = make_source({'acct0': [item('m1'), item('m2')]},
                             bodies={'m1': ApiError(LEAKY), 'm2': quoted_body})
        config = dict(self.config, min_sources=2)
        self.assertEqual(self.engine(source).run('redact:2', config), '失败')
        job = self.job('redact:2')
        self.assert_clean(job['message'])
        self.assert_clean(job['warnings'])
        self.assertIn('素材处理未完成', job['message'])
        failed = self.db.rows("SELECT error FROM articles WHERE title='虚构文章m1'")[0]['error']
        self.assert_clean(failed)
        self.assertIn(REQUEST_ID, failed)
        # Ordinary article content is user material, not a diagnostic: it is kept verbatim.
        kept = self.db.rows("SELECT content FROM articles WHERE title='虚构文章m2'")[0]['content']
        self.assertIn(FAKE_BEARER, kept)

        Writer.fail_titles = {'虚构文章w1'}
        self.db.add_account('虚构公众号乙', 'acct-w')
        self.db.execute('UPDATE accounts SET enabled=0 WHERE identifier=?', ('acct0',))
        source = make_source({'acct-w': [item('w1')]})
        self.assertEqual(self.engine(source).run('redact:3', self.config), '失败')
        job = self.job('redact:3')
        self.assert_clean(job['message'])
        self.assertIn('simulated write failure', job['message'])
        self.assert_clean(self.notices)

    def test_special_characters_and_quotes_are_fully_masked_and_idempotent(self):
        cases = {
            'password=!FAKE_T007_ONLY! 下一步': 'password=[已隐藏] 下一步',
            'password=&#34;FAKE_T007!suffix&#34; 后续': 'password=&#34;[已隐藏]&#34; 后续',
            'password=&quot;FAKE_T007!q&quot;': 'password=&quot;[已隐藏]&quot;',
            'client_secret=FAKE_T007:colon:tail': 'client_secret=[已隐藏]',
            '{"client_secret": "FAKE_T007:a b", "model": "demo"}': '{"client_secret": "[已隐藏]", "model": "demo"}',
            "{'password': 'FAKE_T007!single'}": "{'password': '[已隐藏]'}",
            'password="FAKE\\"T007!escaped" 结束': 'password="[已隐藏]" 结束',
            'api_key=FAKE@T007#x&page=2': 'api_key=[已隐藏]&page=2',
            'password=&FAKE_T007': 'password=[已隐藏]',
            'passwd：FAKE_T007!cn': 'passwd：[已隐藏]',
            'authorization=FAKE!T007': 'authorization=[已隐藏]',
            'Authorization: Bearer FAKE!T007abc': 'Authorization: Bearer [已隐藏]',
            'GET https://x.test/v1?key=FAKE!T007&page=2': 'GET https://x.test/v1?key=[已隐藏]&page=2',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                once = redact(raw)
                self.assertEqual(once, expected)
                self.assertEqual(redact(once), once)
                self.assertEqual(redact(redact(once)), once)
        for keep in ('request_id=req-FAKE-0001 status=429', 'max_tokens=4096', 'the bearer of good news',
                     'password 未配置，已跳过', 'HTTP 401 Unauthorized'):
            self.assertEqual(redact(keep), keep)

    def test_update_job_redacts_any_warnings_shape(self):
        job = self.db.job('redact:shape', self.config)
        self.db.update_job(job['id'], warnings='not json ' + LEAKY, message=LEAKY)
        row = self.job('redact:shape')
        self.assertEqual(len(json.loads(row['warnings'])), 1)
        self.assert_clean(row['warnings'] + row['message'])

    def test_display_boundary_redacts_legacy_unredacted_text(self):
        job = {'id': 7, 'status': '失败', 'stage': '采集', 'message': LEAKY, 'error_code': 'auth',
               'warnings': json.dumps([LEAKY], ensure_ascii=False), 'funnel': '', 'config': '{}'}
        text = job_detail_text(job)
        self.assert_clean(text)
        self.assertIn(REQUEST_ID, text)
        self.assertIn('继续原任务', text)
        self.assert_clean(job_detail_text(dict(job, warnings='broken ' + LEAKY)))


def leaky_v3_database(path):
    """A database as left by 0.10.3: version 3 with unredacted historical diagnostics."""
    Database(path)
    funnel = json.dumps({'version': 2, 'collection': {'warnings': [LEAKY]}, 'counts': {},
                         'drafts': 0, 'accounts': {'acct:x': {'name': '虚构', 'status': 'failed', 'error': LEAKY,
                                                              'counts': {}}}}, ensure_ascii=False)
    with closing(sqlite3.connect(path)) as raw:
        stamp = '2026-10-01T00:00:00+00:00'
        raw.execute('''INSERT INTO jobs(run_key,status,stage,config,warnings,message,created_at,updated_at,funnel)
                       VALUES('legacy','失败','采集','{}',?,?,?,?,?)''',
                    (json.dumps([LEAKY], ensure_ascii=False), LEAKY, stamp, stamp, funnel))
        raw.execute('''INSERT INTO articles(source_key,account_name,title,url,published_at,content,fetched_at,error)
                       VALUES('k','虚构','虚构旧文章','https://mp.weixin.qq.com/s/legacy',?,?,?,?)''',
                    (stamp, '正文示例 Bearer ' + FAKE_BEARER, stamp, LEAKY))
        raw.execute('''INSERT INTO drafts(job_id,title,body,sources,report,created_at,updated_at)
                       VALUES(1,'虚构稿件',?,'[]','报告',?,?)''', ('稿件正文 ' + FAKE_KEY, stamp, stamp))
        raw.execute('PRAGMA user_version=3')
        raw.commit()


class HistoricalCleanupTest(Base):
    def test_v4_migration_redacts_only_diagnostic_fields_and_is_idempotent(self):
        path = self.path / 'legacy.sqlite'
        leaky_v3_database(path)
        db = Database(path)
        self.assertEqual(SCHEMA_VERSION, 4)
        job = db.rows('SELECT * FROM jobs')[0]
        for field in ('message', 'warnings', 'funnel'):
            self.assert_clean(job[field])
        self.assertIn(REQUEST_ID, job['message'])
        self.assertEqual(load_funnel(job['funnel'])['accounts']['acct:x']['status'], 'failed')
        article = db.rows('SELECT content, error FROM articles')[0]
        self.assert_clean(article['error'])
        self.assertIn(FAKE_BEARER, article['content'])
        self.assertIn(FAKE_KEY, db.rows('SELECT body FROM drafts')[0]['body'])
        before = db.rows('SELECT message, warnings, funnel FROM jobs')
        Database(path)
        self.assertEqual(db.rows('SELECT message, warnings, funnel FROM jobs'), before)
        self.assertEqual([len(db.rows('SELECT id FROM ' + t)) for t in ('jobs', 'articles', 'drafts')], [1, 1, 1])

    def test_restoring_an_old_backup_redacts_its_diagnostics(self):
        backup = self.path / 'old-backup.sqlite'
        leaky_v3_database(backup)
        self.db.restore(backup)
        job = self.db.rows("SELECT message, warnings FROM jobs WHERE run_key='legacy'")[0]
        self.assert_clean(job['message'] + job['warnings'])
        self.assert_clean(self.db.rows('SELECT error FROM articles')[0]['error'])


# ---------------------------------------------------------------------- B. metrics

class MetricTrustTest(Base):
    def test_zero_missing_and_invalid_reads_are_distinguished(self):
        self.accounts('虚构公众号甲')
        source = make_source({'acct0': [
            item('r0', read_count=0, like_count=0, metric_source='weread_list'),
            item('none', metric_source='weread_list'),
            item('bad', read_count='10万+', metric_source='weread_list'),
            item('r5', read_count=5, like_count=1, metric_source='weread_list')]})
        config = dict(self.config, selection_mode='hot', min_reads=0)
        self.assertEqual(self.engine(source).run('metric:1', config), '已完成')
        job = self.job('metric:1')
        rows = {r['title']: r for r in self.db.rows('''SELECT a.title, m.reads, n.source, n.reads_status
            FROM job_metrics m JOIN articles a ON a.id=m.article_id
            JOIN metric_notes n ON n.job_id=m.job_id AND n.article_id=m.article_id WHERE m.job_id=?''', (job['id'],))}
        self.assertEqual((rows['虚构文章r0']['reads'], rows['虚构文章r0']['reads_status']), (0, 'measured'))
        self.assertEqual((rows['虚构文章none']['reads'], rows['虚构文章none']['reads_status']), (None, 'missing'))
        self.assertEqual((rows['虚构文章bad']['reads'], rows['虚构文章bad']['reads_status']), (None, 'invalid'))
        self.assertEqual({r['source'] for r in rows.values()}, {'weread_list'})
        funnel = load_funnel(job['funnel'])
        self.assertEqual(funnel['accounts']['acct:acct0']['metrics'], {'measured': 2, 'zero': 1, 'unknown': 2})
        # A legal 0 is ranked; unknown never becomes 0.
        self.assertEqual((funnel['counts']['queued'], funnel['counts']['below_threshold']), (2, 2))
        self.assertIn('阅读量：有效 2 篇（其中为 0 的 1 篇），未知 2 篇（不参与热点排名）', format_funnel(job['funnel']))
        report = self.db.rows('SELECT report FROM drafts')[0]['report']
        self.assertIn('阅读量：0；', report)
        self.assertIn('指标来源：微信读书公众号文章列表（mpInfo.readNum / likeNum）', report)
        self.assertIn('（中国时间）', report)

    def test_unknown_never_erases_a_measured_value_and_replacements_are_recorded(self):
        self.accounts('虚构公众号甲')
        listing = {'acct0': [item('same', read_count=1068)]}
        source = make_source(listing)
        engine = self.engine(source)
        self.assertEqual(engine.run('metric:a', self.config), '已完成')
        listing['acct0'] = [item('same')]
        engine.run('metric:b', self.config)
        article = self.db.rows('SELECT id FROM articles')[0]['id']
        self.assertEqual(self.db.rows('SELECT reads FROM article_metrics')[0]['reads'], 1068)
        b = self.job('metric:b')['id']
        self.assertIsNone(self.db.rows('SELECT reads FROM job_metrics WHERE job_id=?', (b,))[0]['reads'])
        listing['acct0'] = [item('same', read_count=0)]
        engine.run('metric:c', self.config)
        self.assertEqual(self.db.rows('SELECT reads FROM article_metrics')[0]['reads'], 0)
        c = self.job('metric:c')['id']
        note = self.db.rows('SELECT * FROM metric_notes WHERE job_id=? AND article_id=?', (c, article))[0]
        self.assertEqual(note['previous_reads'], 1068)
        line = Engine._metrics_line({'reads': 0, 'likes': None, 'observed_at': '2026-10-19T00:00:00+00:00',
                                     'source': '', 'reads_status': 'measured', 'previous_reads': 1068,
                                     'previous_observed_at': '2026-10-18T02:00:00+00:00'})
        self.assertIn('阅读量：0（此前 2026-10-18 10:00（中国时间） 采集为 1068，本次以接口最新返回值为准）', line)
        self.assertIn('指标来源：采集来源未标注', line)
        self.assertIn('数据采集时间：2026-10-19 08:00（中国时间）', line)


class MetricFieldUpdateTest(Base):
    """Read and like counts are judged separately; valid values (0 included) update,
    missing or invalid values never clear a stored valid value."""

    def observe(self, engine, job_id, article_id, **counts):
        engine._save_metrics(job_id, article_id, counts)
        return self.db.rows('SELECT observed_at FROM job_metrics WHERE job_id=? AND article_id=?',
                            (job_id, article_id))[0]['observed_at']

    def stored(self, article_id):
        row = self.db.rows('''SELECT m.reads, m.likes, m.observed_at, t.reads_observed_at, t.likes_observed_at
            FROM article_metrics m LEFT JOIN article_metric_times t ON t.article_id=m.article_id
            WHERE m.article_id=?''', (article_id,))[0]
        return row

    def test_cross_combinations(self):
        engine = Engine(self.db, Vault())
        article = self.db.save_article({'source_key': 'k-metric', 'account_name': '虚构', 'title': '虚构指标文章',
                                        'url': 'https://mp.weixin.qq.com/s/metric', 'published_at': '2026-10-19'})
        t1 = self.observe(engine, 1, article, read_count=1068, like_count=17)
        t2 = self.observe(engine, 2, article, read_count=1100)                    # likes missing
        row = self.stored(article)
        self.assertEqual((row['reads'], row['likes']), (1100, 17))
        self.assertEqual((row['reads_observed_at'], row['likes_observed_at'], row['observed_at']), (t2, t1, t2))
        t3 = self.observe(engine, 3, article, like_count=25)                      # reads missing
        row = self.stored(article)
        self.assertEqual((row['reads'], row['likes']), (1100, 25))
        self.assertEqual((row['reads_observed_at'], row['likes_observed_at'], row['observed_at']), (t2, t3, t3))
        t4 = self.observe(engine, 4, article, read_count=0, like_count='10万+')  # valid 0, invalid likes
        row = self.stored(article)
        self.assertEqual((row['reads'], row['likes']), (0, 25))
        self.assertEqual((row['reads_observed_at'], row['likes_observed_at']), (t4, t3))
        self.observe(engine, 5, article, read_count='', like_count=None)          # nothing valid
        self.assertEqual(self.stored(article), row)
        t6 = self.observe(engine, 6, article, like_count=0)
        row = self.stored(article)
        self.assertEqual((row['reads'], row['likes'], row['likes_observed_at']), (0, 0, t6))
        # Each job keeps its exact observation, unknown as NULL.
        self.assertEqual([(r['reads'], r['likes']) for r in self.db.rows(
            'SELECT reads, likes FROM job_metrics WHERE article_id=? ORDER BY job_id', (article,))],
            [(1068, 17), (1100, None), (None, 25), (0, None), (None, None), (None, 0)])
        notes = {r['job_id']: r for r in self.db.rows('SELECT * FROM metric_notes WHERE article_id=?', (article,))}
        self.assertEqual((notes[2]['previous_reads'], notes[2]['previous_observed_at']), (1068, t1))
        self.assertEqual((notes[3]['previous_reads'], notes[3]['previous_observed_at']), (1100, t2))
        self.assertIn('文章库保留该有效值', Engine._metrics_line({'reads': None, **notes[3], 'observed_at': t3}))

    def test_first_observation_without_counts_and_legacy_rows(self):
        engine = Engine(self.db, Vault())
        article = self.db.save_article({'source_key': 'k-new', 'account_name': '虚构', 'title': '虚构新文章',
                                        'url': 'https://mp.weixin.qq.com/s/new', 'published_at': '2026-10-19'})
        self.observe(engine, 1, article)
        row = self.stored(article)
        self.assertEqual((row['reads'], row['likes'], row['reads_observed_at'], row['likes_observed_at']),
                         (None, None, '', ''))
        legacy = self.db.save_article({'source_key': 'k-old', 'account_name': '虚构', 'title': '虚构旧文章',
                                       'url': 'https://mp.weixin.qq.com/s/old', 'published_at': '2026-10-19'})
        self.db.execute('INSERT INTO article_metrics VALUES(?,?,?,?)', (legacy, 500, 9, '2026-10-01T00:00:00+00:00'))
        self.observe(engine, 2, legacy, read_count=600)
        row = self.stored(legacy)
        self.assertEqual((row['reads'], row['likes'], row['likes_observed_at']), (600, 9, '2026-10-01T00:00:00+00:00'))
        self.db.clear_records('articles', [legacy])
        self.assertFalse(self.db.rows('SELECT * FROM article_metric_times WHERE article_id=?', (legacy,)))


class WeReadCoverageTest(Base):
    def source(self, **kwargs):
        session = Mock()
        session.shelf.return_value = [{'bookId': BOOK, 'title': '测试号'}]
        session.check.side_effect = lambda result: result
        cancel = Mock()
        cancel.is_set.return_value = False
        cancel.wait.return_value = False
        return session, WeReadSource(kwargs.pop('state', None), self.db, cancel, session, **kwargs)

    @staticmethod
    def group(token, reads):
        return {'subReviews': [{'review': {'reviewId': BOOK + '_' + token, 'mpInfo': {
            'title': token, 'time': 1791534053, 'readNum': reads, 'likeNum': 1}}}]}

    def test_history_list_reports_end_reason_and_metric_source(self):
        session, source = self.source()
        session.request.side_effect = [{'reviews': [self.group('a', 0)]}, {'reviews': []}]
        items = source.articles(BOOK, 5)
        self.assertEqual((items[0]['read_count'], items[0]['metric_source']), (0, 'weread_list'))
        self.assertEqual(source.last_coverage, {'mode': 'history_list', 'end': 'exhausted'})
        session.request.side_effect = [{'reviews': [self.group('b', 3), self.group('c', 4)]}]
        self.assertEqual(len(source.articles(BOOK, 2)), 2)
        self.assertEqual(source.last_coverage, {'mode': 'history_list', 'end': 'limit'})

    def test_skill_fallback_is_marked_latest_only(self):
        skill = Mock()
        skill.shelf.return_value = [{'bookId': BOOK, 'title': '测试号'}]
        skill.review.return_value = {'reviewId': BOOK + '_official', 'mpInfo': {
            'title': '官方最新文章', 'time': 1791910256, 'doc_url': 'https://mp.weixin.qq.com/s/official',
            'readNum': 1068, 'likeNum': 9}}
        session, source = self.source(state='{"version":2,"cookies":[]}', skill=skill,
                                      profile_dir=self.path / 'profile')
        source.browser = Mock()
        source.browser.articles.side_effect = ApiError('业务码 -2041 ' + LEAKY, True, kind='weread_ticket')
        session.request.return_value = {'reviewId': BOOK + '_official', 'title': '封面'}
        items = source.articles(BOOK, 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['metric_source'], 'weread_skill')
        self.assertIn('仅获取最新一篇', items[0]['collection_warning'])
        self.assertEqual(source.last_coverage['mode'], 'latest_only')
        self.assertIn('历史列表不可用', source.last_coverage['reason'])
        self.assert_clean(source.last_coverage['reason'])
        source.close()


# ---------------------------------------------------------------------- C. batch warnings

class BatchWarningTest(Base):
    def setUp(self):
        super().setUp()
        self.accounts('虚构公众号甲')

    def listing(self, n):
        return {'acct0': [item('b%d' % i, read_count=(n - i) * 1000) for i in range(n)]}

    def test_collection_warning_survives_a_successful_batch(self):
        engine = self.engine(make_source(self.listing(2)))
        self.assertEqual(engine.run('batch-c1', self.batch_config(per_account_limit=2)), '已完成')
        job = self.job('batch-c1')
        warnings = json.loads(job['warnings'])
        self.assertEqual([w for w in warnings if w.startswith('采集：')],
                         ['采集：虚构公众号甲 返回条数达到单账号上限，时间窗内可能仍有未覆盖文章'])
        self.assertIn('已生成 2/2 篇独立主题文章，每篇分别联网研究和写作', job['message'])
        self.assertIn('采集阶段有 1 条提示', job['message'])
        self.assertEqual(job_status_label(job), '已完成（有提示）')

    def test_research_degradation_is_not_hidden_by_full_draft_count(self):
        engine = self.engine(make_source(self.listing(2)), research=NetworkResearch)
        self.assertEqual(engine.run('batch-c2', self.batch_config()), '已完成')
        job = self.job('batch-c2')
        self.assertTrue(job['message'].startswith('【需复核】已生成 2/2 篇'))
        self.assertIn('其中 2 篇联网研究失败，已降级为仅基于公众号原文写作', job['message'])
        self.assertNotIn('每篇分别联网研究', job['message'])
        warnings = json.loads(job['warnings'])
        self.assertEqual(sum(w.startswith('联网降级：') for w in warnings), 2)
        funnel = load_funnel(job['funnel'])
        self.assertEqual(funnel['quality'], {'drafts': 2, 'research_degraded': 2, 'flagged': 0})
        self.assertIn('质量：完成稿件 2 篇；联网研究降级为仅原文写作 2 篇', format_funnel(job['funnel']))
        child = self.db.rows("SELECT funnel FROM jobs WHERE run_key LIKE 'batch:%' LIMIT 1")[0]
        self.assertEqual(load_funnel(child['funnel'])['quality']['research'], 'degraded')

    def test_material_failure_replacement_keeps_failure_visible(self):
        source = make_source(self.listing(3), bodies={'b0': ApiError('素材正文不可用')})
        self.assertEqual(self.engine(source).run('batch-c3', self.batch_config()), '已完成')
        job = self.job('batch-c3')
        self.assertIn('热度第 3 篇补位', job['message'])
        self.assertTrue(any(w.startswith('未完成：热度第 1 篇：') for w in json.loads(job['warnings'])))

    def test_single_child_retry_refreshes_parent_and_drops_stale_failure(self):
        Writer.fail_titles = {'虚构文章b0'}
        engine = self.engine(make_source(self.listing(2)))
        self.assertEqual(engine.run('batch-c4', self.batch_config()), '部分完成')
        parent = self.job('batch-c4')
        self.assertTrue(any(w.startswith('未完成：热度第 1 篇') for w in json.loads(parent['warnings'])))
        self.assert_clean(parent['warnings'])
        child = self.db.rows('''SELECT j.run_key, j.config FROM jobs j JOIN batch_items b ON b.child_job_id=j.id
            JOIN articles a ON a.id=b.article_id WHERE a.title='虚构文章b0' ''')[0]
        Writer.fail_titles = set()
        self.assertEqual(engine.run(child['run_key'], json.loads(child['config'])), '已完成')
        parent = self.job('batch-c4')
        self.assertEqual(parent['status'], '已完成')
        self.assertIn('含单篇重试结果', parent['message'])
        self.assertFalse(any(w.startswith('未完成：') for w in json.loads(parent['warnings'])))

    def test_repeated_parent_retry_replaces_instead_of_accumulating(self):
        Writer.fail_titles = {'虚构文章b0'}
        source = make_source(self.listing(2))
        engine = self.engine(source)
        config = self.batch_config(per_account_limit=2)
        results = []
        for _ in range(3):
            self.assertEqual(engine.run('batch-c5', config), '部分完成')
            results.append(json.loads(self.job('batch-c5')['warnings']))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertEqual(sum(w.startswith('采集：') for w in results[-1]), 1)
        self.assertEqual(sum(w.startswith('未完成：') for w in results[-1]), 1)
        self.assertEqual(source.list_calls, 1)

    def test_warnings_from_several_stages_are_reported_together(self):
        Writer.review_fails = True
        engine = self.engine(make_source(self.listing(2)), research=NetworkResearch)
        self.assertEqual(engine.run('batch-c6', self.batch_config(per_account_limit=2)), '已完成')
        job = self.job('batch-c6')
        warnings = json.loads(job['warnings'])
        for prefix in ('采集：', '联网降级：', '质量提示：'):
            self.assertTrue(any(w.startswith(prefix) for w in warnings), prefix)
        self.assertTrue(any('AI 二审未完成' in w for w in warnings))
        self.assertIn('2 篇有处理或质量提示', job['message'])
        self.assertTrue(job['message'].startswith('【需复核】'))


# ---------------------------------------------------------------------- D. coverage

class CoverageTest(Base):
    def test_history_degraded_and_failed_accounts_are_described_separately(self):
        self.accounts('虚构历史号', '虚构降级号', '虚构失败号')
        source = make_source(
            {'acct0': [item('h1')], 'acct1': [item('l1', collection_warning='历史列表不可用，仅最新一篇')],
             'acct2': ApiError('simulated outage')},
            coverage={'acct0': {'mode': 'history_list', 'end': 'exhausted'},
                      'acct1': {'mode': 'latest_only', 'reason': '历史列表不可用：' + LEAKY}})
        self.assertEqual(self.engine(source).run('cover:1', self.config), '部分完成')
        job = self.job('cover:1')
        text = format_funnel(job['funnel'])
        self.assertIn('- 虚构历史号：采集方式：历史列表（本次返回 1 篇，接口未返回更多文章；不代表该公众号完整历史）', text)
        self.assertIn('- 虚构降级号：采集方式：降级：仅最新一篇（历史列表不可用', text)
        self.assertIn('注意：1 个公众号的历史列表不可用，已降级为仅获取最新一篇；扩大时间范围只改变筛选条件', text)
        self.assertIn('- 虚构失败号：采集失败，simulated outage', text)
        self.assertIn('最近返回文章：', text.split('虚构降级号')[1].split('\n')[0])
        self.assertIn('（中国时间）', text)
        self.assert_clean(job['funnel'])

    def test_old_funnel_without_coverage_fields_still_formats(self):
        old = {'version': 2, 'collection': {'accounts_enabled': 1, 'accounts_ok': 1, 'accounts_failed': 0},
               'counts': {'returned': 1, 'candidates': 1, 'queued': 1, 'usable': 1}, 'drafts': 1,
               'accounts': {'acct:x': {'name': '虚构旧号', 'status': 'ok', 'counts': {'returned': 1}}}}
        text = format_funnel(json.dumps(old, ensure_ascii=False))
        self.assertIn('- 虚构旧号：返回 1 篇', text)
        self.assertNotIn('采集方式', text)
        self.assertNotIn('质量：', text)

    def test_retry_reusing_selection_keeps_coverage_and_collection_note_once(self):
        self.accounts('虚构降级号')
        Writer.fail_titles = {'虚构文章l1'}
        source = make_source({'acct0': [item('l1', collection_warning='历史列表不可用，仅最新一篇')]},
                             coverage={'acct0': {'mode': 'latest_only', 'reason': '历史列表不可用'}})
        engine = self.engine(source)
        self.assertEqual(engine.run('cover:2', self.config), '失败')
        Writer.fail_titles = set()
        self.assertEqual(engine.run('cover:2', self.config), '部分完成')
        job = self.job('cover:2')
        self.assertEqual(source.list_calls, 1)
        self.assertIn('采集方式：降级：仅最新一篇', format_funnel(job['funnel']))
        self.assertEqual(json.loads(job['warnings']), ['虚构降级号：历史列表不可用，仅最新一篇'])


# ---------------------------------------------------------------------- E. continue vs new collection

class ContinueOrRecollectTest(Base):
    def test_continue_keeps_original_selection_and_new_collection_reads_current_list(self):
        self.accounts('虚构公众号甲')
        listing = {'acct0': [item('e1', read_count=2000), item('e2', read_count=1000)]}
        source = make_source(listing)
        engine = self.engine(source)
        config = self.batch_config()
        Writer.fail_titles = {'虚构文章e2'}
        self.assertEqual(engine.run('manual:original', config), '部分完成')
        first = self.db.rows('SELECT id, title, body FROM drafts')
        self.assertEqual(len(first), 1)
        listing['acct0'].insert(0, item('e3-new', read_count=3000))
        Writer.fail_titles = set()

        self.assertEqual(engine.run('manual:original', config), '已完成')
        self.assertEqual(source.list_calls, 1)
        self.assertFalse(self.db.rows("SELECT id FROM articles WHERE title='虚构文章e3-new'"))
        self.assertEqual(Writer.written.count('虚构文章e1'), 1)
        self.assertEqual(len(self.db.rows('SELECT id FROM drafts')), 2)

        current = dict(config, lookback_hours=48)
        status = engine.new_collection(current)
        self.assertEqual(source.list_calls, 2)
        new_job = self.db.rows("SELECT * FROM jobs WHERE run_key LIKE 'manual:%' AND run_key<>'manual:original'")
        self.assertEqual(len(new_job), 1)
        new_job = new_job[0]
        self.assertEqual(json.loads(new_job['config'])['lookback_hours'], 48)
        self.assertGreaterEqual(new_job['created_at'], self.job('manual:original')['created_at'])
        selected = json.loads(new_job['selected'])
        new_id = self.db.rows("SELECT id FROM articles WHERE title='虚构文章e3-new'")[0]['id']
        used = {r['id'] for r in self.db.rows("SELECT id FROM articles WHERE title IN ('虚构文章e1','虚构文章e2')")}
        self.assertEqual(selected, [new_id])
        self.assertFalse(used & set(selected))
        self.assertEqual(status, '部分完成')
        self.assertEqual(self.db.rows('SELECT id, title, body FROM drafts WHERE id=?', (first[0]['id'],)), first)
        self.assertEqual(len(self.db.rows('SELECT id FROM drafts')), 3)


# ---------------------------------------------------------------------- F. version and build provenance

def fake_git(commit='abcdef1234567890abcdef1234567890abcdef12', status=''):
    def git(root, *args):
        return commit + '\n' if args[0] == 'rev-parse' else status
    return git


def broken_git(root, *args):
    raise OSError('git missing')


class VersionTest(Base):
    def test_single_version_source(self):
        self.assertEqual(__version__, '0.10.4')
        self.assertEqual(verify_delivery.check_version(ROOT), __version__)
        build = (ROOT / 'scripts' / 'build.ps1').read_text(encoding='utf-8')
        self.assertNotIn("$version = '", build)
        self.assertNotIn('0.10.', (ROOT / 'scripts' / 'build_to_desktop.cmd').read_text(encoding='utf-8'))
        self.assertIn('v' + __version__, (ROOT / 'README.md').read_text(encoding='utf-8').splitlines()[0])

    def test_build_label_never_presents_dirty_or_unknown_as_clean(self):
        info = {'version': __version__, 'commit': 'abcdef1234', 'dirty': True}
        self.assertEqual(build_label(None, frozen=False), 'v%s · 源码运行' % __version__)
        self.assertEqual(build_label(None, frozen=True), 'v%s · 构建信息未记录' % __version__)
        self.assertEqual(build_label(info, frozen=True), 'v%s · abcdef1 · 含未提交改动' % __version__)
        self.assertIn('干净提交', build_label(dict(info, dirty=False), frozen=True))
        self.assertIn('工作区状态未知', build_label(dict(info, dirty=None), frozen=True))
        self.assertIn('构建信息未记录', build_label(dict(info, version='0.0.1'), frozen=True))

    def test_build_info_records_commit_dirty_state_and_china_time(self):
        now = datetime(2026, 10, 19, 2, 30, tzinfo=timezone.utc)
        clean = verify_delivery.build_info(ROOT, fake_git(), now)
        self.assertEqual((clean['dirty'], clean['build_id']), (False, __version__ + '+gabcdef1'))
        self.assertEqual(clean['built_at'], '2026-10-19T10:30:00+08:00')
        dirty = verify_delivery.build_info(ROOT, fake_git(status=' M wewriter/engine.py\n'), now)
        self.assertEqual((dirty['dirty'], dirty['build_id']), (True, __version__ + '+gabcdef1.dirty'))
        unknown = verify_delivery.build_info(ROOT, broken_git, now)
        self.assertEqual((unknown['commit'], unknown['dirty'], unknown['build_id']),
                         ('', None, __version__ + '+nogit.unknown'))
        text = json.dumps([clean, dirty, unknown], ensure_ascii=False)
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn(str(Path.home()), text)

    def package(self, files=None, info=None):
        release = self.path / ('v' + __version__)
        release.mkdir(exist_ok=True)
        (release / 'WeWriter.exe').write_bytes(b'fake exe for offline check')
        (release / 'install.ps1').write_text('# fake', encoding='utf-8')
        (release / 'README.md').write_text((ROOT / 'README.md').read_text(encoding='utf-8'), encoding='utf-8')
        info = info or verify_delivery.build_info(ROOT, fake_git(status='?? new.txt\n'))
        (release / BUILD_INFO_NAME).write_text(json.dumps(info), encoding='utf-8')
        archive = self.path / ('WeWriter-v%s-Windows.zip' % __version__)
        with zipfile.ZipFile(archive, 'w') as zf:
            for name in files or verify_delivery.PACKAGE_FILES:
                zf.write(release / name, name)
        return release, archive

    def test_package_check_accepts_consistent_package_and_reports_dirty(self):
        release, archive = self.package()
        result = verify_delivery.check_package(ROOT, release, archive)
        self.assertIn('含未提交改动', result)
        self.assertIn('.dirty', result)

    def test_package_check_rejects_missing_manifest_or_wrong_version(self):
        release, archive = self.package(files=('WeWriter.exe', 'install.ps1', 'README.md'))
        with self.assertRaises(verify_delivery.CheckFailed):
            verify_delivery.check_package(ROOT, release, archive)
        release, archive = self.package(info={'version': '0.10.3', 'commit': 'x', 'dirty': False})
        with self.assertRaisesRegex(verify_delivery.CheckFailed, 'build-info.json 版本'):
            verify_delivery.check_package(ROOT, release, archive)

    def test_cli_writes_manifest_without_paths(self):
        target = self.path / 'release-info' / BUILD_INFO_NAME
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(verify_delivery.main(['--write-build-info', str(target)]), 0)
        self.assertIn('[通过] 构建清单', output.getvalue())
        info = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(info['version'], __version__)
        self.assertIn(info['dirty'], (True, False, None))
        self.assertNotIn(str(ROOT), target.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
