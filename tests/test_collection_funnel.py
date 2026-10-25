"""Collection funnel counts, persistence and display. All accounts, articles and errors are fictional."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wewriter.api import ApiError
from wewriter.config import defaults
from wewriter.db import Database
from wewriter.engine import COLLECT_KEYS, MATERIAL_KEYS, Engine, format_funnel, load_funnel

BODY = '这是一段虚构的测试正文，用来验证采集漏斗的计数是否准确。' * 6
FAKE_SECRET = 'sk-FAKE0000FUNNEL0000DUMMY'


def item(slug, hours_ago=1, **extra):
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {'title': '虚构文章' + slug, 'url': 'https://mp.weixin.qq.com/s/fake-' + slug, 'posted_at': when, **extra}


class Vault:
    def get(self, name):
        return 'test-only-never-a-real-key'

    def has(self, name):
        return False


class Model:
    fail_write = False

    def __init__(self, *args):
        pass

    def summary(self, article, content):
        return {'summary': '虚构摘要', 'facts': [], 'uncertainties': []}

    def analyze_style(self, content, title=''):
        return {'tone': '客观'}

    def write(self, sources, config):
        if type(self).fail_write:
            raise ApiError('simulated write failure')
        return {'title': '虚构稿件', 'outline': ['要点'], 'review_notes': [],
                'body': '正文' + ''.join(f"[{s['source_id']}]" for s in sources)}


def source_factory(listings, bodies=None):
    """listings: identifier -> list of items or an exception; bodies: url suffix -> text or exception."""
    bodies = bodies or {}

    class Source:
        def __init__(self, *args):
            pass

        def articles(self, account, limit):
            value = listings[account]
            if isinstance(value, Exception):
                raise value
            return [dict(i) if isinstance(i, dict) else i for i in value]

        def content(self, url):
            value = bodies.get(url.rsplit('fake-', 1)[-1], BODY + url)
            if isinstance(value, Exception):
                raise value
            return value

    return Source


class FunnelTest(unittest.TestCase):
    def setUp(self):
        workspace_temp = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.db = Database(Path(self.temp.name) / 'funnel.sqlite')
        self.config = defaults()
        self.config.update(comments_enabled=False, selection_mode='recent', writing_mode='combined',
                           min_sources=1, article_limit=10, lookback_hours=24)
        Model.fail_write = False

    def tearDown(self):
        self.temp.cleanup()

    def accounts(self, *names):
        for n, name in enumerate(names):
            self.db.add_account(name, 'acct%d' % n)

    def run_job(self, key, listings, bodies=None, config=None):
        engine = Engine(self.db, Vault(), source_factory=source_factory(listings, bodies), model_factory=Model)
        status = engine.run(key, config or self.config)
        job = self.db.rows('SELECT * FROM jobs WHERE run_key=?', (key,))[0]
        return status, job, load_funnel(job['funnel'])

    def assert_consistent(self, funnel):
        counts = funnel['counts']
        if funnel['collection']:
            self.assertEqual(counts['returned'], sum(counts[k] for k in COLLECT_KEYS[1:7]))
            self.assertEqual(counts['candidates'], sum(counts[k] for k in COLLECT_KEYS[7:]))
        for key in COLLECT_KEYS + MATERIAL_KEYS:
            per_account = sum(a['counts'].get(key, 0) for a in funnel['accounts'].values())
            self.assertEqual(per_account, counts[key], key)

    def test_two_accounts_only_one_usable(self):
        self.accounts('虚构公众号甲', '虚构公众号乙')
        status, job, funnel = self.run_job('two:1', {
            'acct0': [item('a1')],
            'acct1': [item('b-old', hours_ago=48), item('b1')],
        }, bodies={'b1': ApiError('simulated body failure')})
        self.assertEqual(status, '部分完成')
        c = funnel['counts']
        self.assertEqual(funnel['collection']['accounts_enabled'], 2)
        self.assertEqual(funnel['collection']['accounts_ok'], 2)
        self.assertEqual((c['returned'], c['out_of_window'], c['candidates'], c['queued']), (3, 1, 2, 2))
        self.assertEqual((c['body_failed'], c['usable'], funnel['drafts']), (1, 1, 1))
        a, b = funnel['accounts']['acct:acct0']['counts'], funnel['accounts']['acct:acct1']['counts']
        self.assertEqual(a, {'returned': 1, 'candidates': 1, 'queued': 1, 'usable': 1})
        self.assertEqual(b, {'returned': 2, 'out_of_window': 1, 'candidates': 1, 'queued': 1, 'body_failed': 1})
        self.assert_consistent(funnel)
        text = format_funnel(job['funnel'])
        self.assertIn('扫描 2 个启用公众号（成功 2，失败 0），接口共返回 3 篇', text)
        self.assertIn('最终可用素材 1 篇，完成稿件 1 篇', text)
        self.assertIn('- 虚构公众号甲：返回 1 篇，进入候选 1 篇，进入处理队列 1 篇；可用素材 1 篇', text)
        self.assertIn('- 虚构公众号乙：返回 2 篇，进入候选 1 篇，进入处理队列 1 篇；可用素材 0 篇；'
                      '跳过：超出时间范围 1、正文获取失败（旧记录可能含摘要失败） 1', text)
        self.assertNotIn(BODY[:20], job['funnel'])

    def test_account_failure_is_recorded_and_redacted(self):
        self.accounts('虚构公众号甲', '虚构公众号乙')
        status, job, funnel = self.run_job('fail:1', {
            'acct0': ApiError('列表读取失败 Authorization: Bearer ' + FAKE_SECRET),
            'acct1': [item('b1')],
        })
        self.assertEqual(status, '部分完成')
        self.assertEqual((funnel['collection']['accounts_ok'], funnel['collection']['accounts_failed']), (1, 1))
        failed = funnel['accounts']['acct:acct0']
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('列表读取失败', failed['error'])
        self.assertNotIn(FAKE_SECRET, job['funnel'])
        self.assertIn('- 虚构公众号甲：采集失败，列表读取失败', format_funnel(job['funnel']))
        self.assert_consistent(funnel)

    def test_all_accounts_failing_keeps_stats(self):
        self.accounts('虚构公众号甲')
        status, job, funnel = self.run_job('allfail:1', {'acct0': ApiError('simulated outage')})
        self.assertEqual(status, '失败')
        self.assertEqual(funnel['collection']['accounts_failed'], 1)
        self.assertEqual(funnel['counts']['returned'], 0)

    def test_listing_filters_count_each_item_once(self):
        self.accounts('虚构公众号甲', '虚构公众号乙')
        status, job, funnel = self.run_job('filters:1', {
            'acct0': [item('d1', is_deleted=True), item('ok1'), {'title': '', 'url': 'x', 'posted_at': 'bad'},
                      'not-a-dict', item('ok1')],
            'acct1': [item('ok1'), item('ok2')],
        })
        c = funnel['counts']
        self.assertEqual((c['returned'], c['deleted'], c['invalid'], c['repeated'], c['candidates']), (7, 1, 2, 2, 2))
        self.assertEqual((c['queued'], c['usable']), (2, 2))
        self.assertEqual(funnel['accounts']['acct:acct1']['counts'],
                         {'returned': 2, 'repeated': 1, 'candidates': 1, 'queued': 1, 'usable': 1})
        self.assert_consistent(funnel)

    def test_used_articles_are_counted(self):
        self.accounts('虚构公众号甲')
        listing = {'acct0': [item('u1'), item('u2')]}
        self.assertEqual(self.run_job('used:1', listing)[0], '已完成')
        status, job, funnel = self.run_job('used:2', listing)
        self.assertEqual(status, '无新文章')
        self.assertEqual((funnel['counts']['candidates'], funnel['counts']['used'], funnel['counts']['queued']),
                         (2, 2, 0))
        self.assertIn('排除：已用于既有稿件 2', format_funnel(job['funnel']))
        self.assert_consistent(funnel)

    def test_hot_threshold_and_article_limit(self):
        self.accounts('虚构公众号甲')
        config = dict(self.config, selection_mode='hot', min_reads=100, article_limit=1)
        status, job, funnel = self.run_job('hot:1', {'acct0': [
            item('h1', read_count=500), item('h2', read_count=300), item('h3', read_count=50), item('h4')]},
            config=config)
        self.assertEqual(status, '已完成')
        c = funnel['counts']
        self.assertEqual((c['candidates'], c['below_threshold'], c['over_limit'], c['queued'], c['usable']),
                         (4, 2, 1, 1, 1))
        self.assert_consistent(funnel)

    def test_image_only_body_failure_and_duplicate_body(self):
        self.accounts('虚构公众号甲')
        status, job, funnel = self.run_job('material:1', {'acct0': [item(s) for s in ('m1', 'm2', 'm3', 'm4')]},
                                           bodies={'m1': '图很多字很少', 'm2': ApiError('simulated body failure'),
                                                   'm3': BODY, 'm4': BODY})
        c = funnel['counts']
        self.assertEqual((c['queued'], c['image_only'], c['body_failed'], c['duplicate_body'], c['usable']),
                         (4, 1, 1, 1, 1))
        self.assertEqual(funnel['drafts'], 1)
        text = format_funnel(job['funnel'])
        for line in ('排除：图片型跳过 1', '排除：正文获取失败（旧记录可能含摘要失败） 1', '排除：正文重复 1', '- 可用素材：1'):
            self.assertIn(line, text)
        self.assert_consistent(funnel)

    def test_failure_keeps_partial_stats_and_retry_does_not_accumulate(self):
        self.accounts('虚构公众号甲')
        listing = {'acct0': [item('r1'), item('r2')]}
        Model.fail_write = True
        status, job, funnel = self.run_job('retry:1', listing)
        self.assertEqual(status, '失败')
        self.assertEqual((funnel['counts']['queued'], funnel['counts']['usable'], funnel['drafts']), (2, 2, 0))
        Model.fail_write = False
        status, job, funnel = self.run_job('retry:1', listing)
        self.assertEqual(status, '已完成')
        c = funnel['counts']
        self.assertEqual((c['returned'], c['candidates'], c['queued'], c['usable'], funnel['drafts']), (2, 2, 2, 2, 1))
        self.assertTrue(funnel['collection']['reused'])
        self.assertEqual(funnel['accounts']['acct:acct0']['counts']['usable'], 2)
        self.assertIn('本次重试沿用上次采集的选文', format_funnel(job['funnel']))
        self.assert_consistent(funnel)

    def test_batch_parent_sums_child_materials(self):
        self.accounts('虚构公众号甲')
        config = dict(self.config, writing_mode='separate_web', draft_count=2, min_sources=1)

        class Research:
            def __init__(self, *args):
                pass

            def search(self, article):
                return {'notes': '虚构联网资料[W1]', 'retrieved_at': '2026-10-17',
                        'sources': [{'source_id': 'W1', 'title': '虚构来源', 'url': 'https://example.test/w1'}]}

        listing = {'acct0': [item('p1', read_count=900), item('p2', read_count=800), item('p3', read_count=700)]}
        engine = Engine(self.db, Vault(), source_factory=source_factory(listing, {'p1': '图很多字很少'}),
                        model_factory=Model, research_factory=Research)
        self.assertEqual(engine.run('batch-parent:1', config), '已完成')
        parent = self.db.rows("SELECT * FROM jobs WHERE run_key='batch-parent:1'")[0]
        funnel = load_funnel(parent['funnel'])
        c = funnel['counts']
        self.assertEqual((c['queued'], c['image_only'], c['usable'], funnel['drafts']), (3, 1, 2, 2))
        self.assert_consistent(funnel)
        child = self.db.rows("SELECT funnel FROM jobs WHERE run_key LIKE 'batch:%' ORDER BY id LIMIT 1")[0]
        self.assertIn('没有本次采集统计', format_funnel(child['funnel']))

    def test_same_name_accounts_are_kept_apart(self):
        self.accounts('同名公众号', '同名公众号')
        status, job, funnel = self.run_job('same-name:1', {
            'acct0': [item('s1')],
            'acct1': [item('s-old', hours_ago=48), item('s2')],
        }, bodies={'s2': ApiError('simulated body failure')})
        self.assertEqual(set(funnel['accounts']), {'acct:acct0', 'acct:acct1'})
        first, second = funnel['accounts']['acct:acct0'], funnel['accounts']['acct:acct1']
        self.assertEqual((first['name'], first['identifier']), ('同名公众号', 'acct0'))
        self.assertEqual(first['counts'], {'returned': 1, 'candidates': 1, 'queued': 1, 'usable': 1})
        self.assertEqual(second['counts'],
                         {'returned': 2, 'out_of_window': 1, 'candidates': 1, 'queued': 1, 'body_failed': 1})
        self.assert_consistent(funnel)
        text = format_funnel(job['funnel'])
        self.assertIn('- 同名公众号（acct0）：返回 1 篇，进入候选 1 篇，进入处理队列 1 篇；可用素材 1 篇', text)
        self.assertIn('- 同名公众号（acct1）：返回 2 篇，进入候选 1 篇，进入处理队列 1 篇；可用素材 0 篇；'
                      '跳过：超出时间范围 1、正文获取失败（旧记录可能含摘要失败） 1', text)

    def test_same_name_failure_stays_on_its_account(self):
        self.accounts('同名公众号', '同名公众号')
        status, job, funnel = self.run_job('same-name-fail:1', {
            'acct0': ApiError('simulated outage'), 'acct1': [item('f1')]})
        self.assertEqual(funnel['accounts']['acct:acct0']['status'], 'failed')
        self.assertEqual(funnel['accounts']['acct:acct1']['status'], 'ok')
        self.assertEqual(funnel['accounts']['acct:acct1']['counts']['usable'], 1)
        text = format_funnel(job['funnel'])
        self.assertIn('- 同名公众号（acct0）：采集失败，simulated outage', text)
        self.assertIn('- 同名公众号（acct1）：返回 1 篇', text)

    def test_batch_same_name_accounts_attribute_child_materials(self):
        self.accounts('同名公众号', '同名公众号')
        config = dict(self.config, writing_mode='separate_web', draft_count=2, min_sources=1)

        class Research:
            def __init__(self, *args):
                pass

            def search(self, article):
                return {'notes': '虚构联网资料[W1]', 'retrieved_at': '2026-10-17',
                        'sources': [{'source_id': 'W1', 'title': '虚构来源', 'url': 'https://example.test/w1'}]}

        listing = {'acct0': [item('q1', read_count=900)],
                   'acct1': [item('q2', read_count=800), item('q3', read_count=700)]}
        engine = Engine(self.db, Vault(), source_factory=source_factory(listing, {'q1': '图很多字很少'}),
                        model_factory=Model, research_factory=Research)
        self.assertEqual(engine.run('batch-same-name:1', config), '已完成')
        parent = self.db.rows("SELECT funnel FROM jobs WHERE run_key='batch-same-name:1'")[0]
        funnel = load_funnel(parent['funnel'])
        self.assertEqual(funnel['accounts']['acct:acct0']['counts'],
                         {'returned': 1, 'candidates': 1, 'queued': 1, 'image_only': 1})
        self.assertEqual(funnel['accounts']['acct:acct1']['counts'],
                         {'returned': 2, 'candidates': 2, 'queued': 2, 'usable': 2})
        self.assertEqual(funnel['drafts'], 2)
        self.assert_consistent(funnel)

    def test_latest_returned_time_is_true_max_in_china_time(self):
        self.accounts('虚构公众号甲')
        posted = ['2026-01-01T10:00:00Z',          # UTC -> 2026-01-01 18:00 China time
                  '2026-01-02T03:07:00+05:00',     # offset -> 2026-01-02 06:07 China time (newest)
                  '2026-01-02 05:30:00',           # naive, read as China time
                  1767276000000,                   # epoch ms -> 2026-01-01 22:00 China time
                  'not-a-date']
        listing = [{'title': '虚构旧文' + str(n), 'url': 'https://mp.weixin.qq.com/s/fake-old%d' % n, 'posted_at': when}
                   for n, when in enumerate(posted)]
        status, job, funnel = self.run_job('latest:1', {'acct0': listing})
        account = funnel['accounts']['acct:acct0']
        self.assertEqual((account['counts']['out_of_window'], account['counts']['invalid']), (4, 1))
        self.assertEqual(account['latest_published'], '2026-01-01T22:07:00+00:00')
        self.assertIn('跳过：超出时间范围 4、条目无效 1；最近返回文章：2026-01-02 06:07（中国时间）',
                      format_funnel(job['funnel']))
        # Neither the first nor the last listed item decides the answer.
        listing.reverse()
        status, job, funnel = self.run_job('latest:2', {'acct0': listing})
        self.assertEqual(funnel['accounts']['acct:acct0']['latest_published'], '2026-01-01T22:07:00+00:00')
        self.assert_consistent(funnel)

    def test_latest_returned_time_shown_only_with_out_of_window(self):
        self.accounts('虚构公众号甲', '虚构公众号乙', '虚构公众号丙', '虚构公众号丁')
        fresh = item('m1', hours_ago=1)
        status, job, funnel = self.run_job('latest:mixed', {
            'acct0': [item('m-old', hours_ago=72), fresh, item('m-older', hours_ago=96)],
            'acct1': [item('n1', hours_ago=2)],
            'acct2': ApiError('simulated outage'),
            'acct3': [],
        })
        expected = datetime.fromisoformat(fresh['posted_at']).astimezone(timezone(timedelta(hours=8)))
        text = format_funnel(job['funnel'])
        # The account still has an in-window candidate: the newest returned time is that one.
        self.assertIn('跳过：超出时间范围 2；最近返回文章：%s（中国时间）' % expected.strftime('%Y-%m-%d %H:%M'), text)
        self.assertEqual(text.count('最近返回文章'), 1)
        self.assertIn('latest_published', funnel['accounts']['acct:acct1'])
        self.assertNotIn('latest_published', funnel['accounts']['acct:acct2'])
        self.assertNotIn('latest_published', funnel['accounts']['acct:acct3'])
        self.assertIn('- 虚构公众号丙：采集失败，simulated outage', text)
        self.assertIn('- 虚构公众号丁：返回 0 篇，进入候选 0 篇，进入处理队列 0 篇；可用素材 0 篇\n', text + '\n')
        self.assert_consistent(funnel)

    def test_old_funnel_without_latest_time_still_formats(self):
        self.accounts('虚构公众号甲')
        status, job, funnel = self.run_job('latest:legacy', {'acct0': [item('l-old', hours_ago=48), item('l1')]})
        for value in (None, 'not-a-date', '2026-01-01T10:00:00'):
            account = funnel['accounts']['acct:acct0']
            account.pop('latest_published', None)
            if value is not None:
                account['latest_published'] = value
            text = format_funnel(json.dumps(funnel, ensure_ascii=False))
            self.assertIn('跳过：超出时间范围 1', text)
            self.assertNotIn('最近返回文章', text)

    def test_retry_reusing_selection_keeps_latest_time(self):
        self.accounts('虚构公众号甲')
        listing = {'acct0': [item('t-old', hours_ago=48), item('t1')]}
        Model.fail_write = True
        status, job, first = self.run_job('latest:retry', listing)
        self.assertEqual(status, '失败')
        Model.fail_write = False
        status, job, funnel = self.run_job('latest:retry', listing)
        self.assertEqual(status, '已完成')
        self.assertTrue(funnel['collection']['reused'])
        latest = first['accounts']['acct:acct0']['latest_published']
        self.assertEqual(funnel['accounts']['acct:acct0']['latest_published'], latest)
        shown = datetime.fromisoformat(latest).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        self.assertIn('最近返回文章：%s（中国时间）' % shown, format_funnel(job['funnel']))

    def test_old_job_without_funnel(self):
        self.assertEqual(format_funnel(''), '该历史任务没有采集统计')
        self.assertEqual(format_funnel(None), '该历史任务没有采集统计')
        self.assertEqual(format_funnel('{broken'), '该历史任务没有采集统计')
        job = self.db.job('legacy:1', self.config)
        self.assertEqual(job['funnel'], '')
        self.assertEqual(format_funnel(job['funnel']), '该历史任务没有采集统计')


if __name__ == '__main__':
    unittest.main()
