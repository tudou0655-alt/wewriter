"""Web desktop UI: the five pages in wewriter/web, shown in a pywebview window.

The pages call ``window.pywebview.api.<method>`` (the ``Api`` class below);
every method returns plain JSON data. Long work (collection and writing, the
bookshelf sync, connection checks) runs on one worker thread and the pages
poll ``*_state`` methods for progress.
"""
import base64
import json
import os
from pathlib import Path
import re
import sys
import threading
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import __version__
from .api import ApiError, Model, Transport, wechat_url
from .config import due_key, validate
from .db import Database
from .engine import Engine, format_coverage, load_funnel, window_start
from .logs import logger, redact
from .ui import (EXPORT_FORMATS, MODEL_OPTIONS, PHASES, RETRYABLE, STYLE_PRESETS, china_time, compose_style,
                 draft_hint, job_status_label, phase_index, version_text, write_export, write_report)
from .windows import set_startup, startup_enabled

CHINA = timezone(timedelta(hours=8))
WEB_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'wewriter' / 'web'
KEY_LABELS = {'deepseek': 'DeepSeek', 'openai': 'OpenAI', 'dajiala': '极致了', 'everyinfra': 'EveryInfra'}
STATE_CLASSES = {'已通过': 'ok', '需修改': 'fix', '待审核': 'wait'}


def _now():
    return datetime.now(timezone.utc)


def article_state(used, content, error):
    if used:
        return 'used'
    if error:
        if str(error).startswith('摘要分析失败（正文已保存）'):
            return 'summary'
        return 'processing' if content else 'bad'
    return 'ok' if content else 'nobody'


def _elapsed(job, running):
    try:
        end = _now() if running else datetime.fromisoformat(job['updated_at'])
        return max(0, int((end - datetime.fromisoformat(job['created_at'])).total_seconds()))
    except (TypeError, ValueError):
        return 0


def _range_label(config):
    if config.get('lookback_mode') == 'today':
        return '今天'
    hours = int(config.get('lookback_hours', 24))
    return '近 3 天' if hours == 72 else f'近 {hours} 小时'


class Backend:
    """State shared by the API methods; one worker thread, as in the Tk UI."""

    def __init__(self, db, vault, data_dir, demo=False):
        self.db, self.vault, self.data_dir, self.demo = db, vault, Path(data_dir), demo
        self.engine = Engine(db, vault, lambda msg: None, profile_dir=self.data_dir / 'weread-edge-profile')
        self.lock = threading.RLock()
        self.busy = False
        self.current_job_id = None
        self.session_queue = []
        self.queue_paused = False
        self.sync = None
        self.window = None
        self.last_message = ''

    # ------------------------------------------------------------ worker
    def require_live(self):
        if self.demo:
            raise ValueError('这是演示模式，不会调用真实服务；请打开正常版使用')

    def require_idle(self):
        if self.busy:
            raise ValueError('后台正在执行操作，请等它完成或先停止任务')

    def start_worker(self, action, on_done=None):
        with self.lock:
            if self.busy:
                raise ValueError('后台正在执行操作，请等它完成或先停止任务')
            self.busy = True

        def work():
            result, error = None, None
            try:
                result = action()
            except Exception as exc:               # reported to the page, never raised into pywebview
                if not isinstance(exc, (ApiError, ValueError)):
                    logger.exception('后台任务异常')
                error = exc
            finally:
                with self.lock:
                    self.busy = False
            self.last_message = redact(str(error)) if error else str(result or '')
            if on_done:
                try:
                    on_done(result, error)
                except Exception:
                    logger.exception('后台任务回调异常')
            self.next_queued()
        threading.Thread(target=work, daemon=True, name='worker').start()

    def launch(self, job):
        config = json.loads(job['config'])
        if job['stage'] == '排队中':
            self.db.update_job(job['id'], stage='采集', message='')
        self.current_job_id = job['id']
        self.start_worker(lambda: self.engine.run(job['run_key'], config))

    # Display preferences live beside the database, not in the task config, so changing the
    # look never touches a frozen task configuration.
    def ui_prefs(self):
        try:
            return json.loads((Path(self.data_dir) / 'ui.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def save_ui_prefs(self, **values):
        prefs = {**self.ui_prefs(), **values}
        (Path(self.data_dir) / 'ui.json').write_text(json.dumps(prefs, ensure_ascii=False), encoding='utf-8')

    def queued(self):
        return self.db.rows("SELECT * FROM jobs WHERE status='等待中' AND stage='排队中' ORDER BY id")

    def start_task(self, config, job=None):
        with self.lock:
            job = job or self.db.job('manual:' + uuid.uuid4().hex, config)
            if self.busy:
                self.db.update_job(job['id'], stage='排队中', message='排队中：等待当前任务完成后自动开始')
                self.session_queue.append(job['id'])
                self.queue_paused = False
                return {'queued': True, 'count': len(self.queued())}
            self.queue_paused = False
            self.launch(job)
            return {'queued': False}

    def next_queued(self):
        with self.lock:
            if self.busy or self.queue_paused:
                return
            for job in self.queued():
                if job['id'] in self.session_queue:
                    self.session_queue.remove(job['id'])
                    try:
                        self.launch(job)
                    except ValueError:
                        pass
                    return

    def schedule_loop(self):
        while True:
            threading.Event().wait(30)
            if self.demo:
                continue
            try:
                with self.lock:
                    config = self.db.config()
                    key = due_key(config)
                    if key and not self.db.rows('SELECT id FROM jobs WHERE run_key=?', (key,)):
                        job = self.db.job(key, config)
                        if self.busy:
                            # Scheduled and manual tasks share one execution slot.
                            self.db.update_job(job['id'], stage='排队中', message='每日定时任务：排队等待当前任务完成')
                            self.session_queue.append(job['id'])
                        else:
                            self.launch(job)
            except Exception:
                logger.exception('定时检查异常')

    def weread_source(self, cancel=None):
        from .weread import WeReadSource
        return WeReadSource(self.vault.get('weread'), self.db, cancel,
                            state_saver=lambda value: self.vault.set_many({'weread': value}),
                            profile_dir=self.data_dir / 'weread-edge-profile',
                            skill_key=(self.vault.get('weread_skill') if self.vault.has('weread_skill') else None))


def _safe(method):
    """Wrap an API method: errors come back as {'ok': False, 'error': text} for the page to show."""
    def call(self, *args):
        try:
            result = method(self, *args)
            return {'ok': True, **(result if isinstance(result, dict) else {'value': result})}
        except Exception as exc:
            if not isinstance(exc, (ApiError, ValueError)):
                logger.exception('界面接口异常：%s', method.__name__)
            return {'ok': False, 'error': redact(str(exc)) or type(exc).__name__}
    call.__name__ = method.__name__
    return call


class Api:
    """Methods callable from the pages. Attributes start with '_' so pywebview does not expose them."""

    def __init__(self, backend):
        self._b = backend

    # ------------------------------------------------------------ common
    @_safe
    def meta(self):
        return {'version': version_text(), 'demo': self._b.demo, 'busy': self._b.busy,
                'message': self._b.last_message, 'theme': self._b.ui_prefs().get('theme', 'system')}

    @_safe
    def ui_theme(self, theme):
        if theme not in ('system', 'light', 'dark'):
            raise ValueError('外观选项无效')
        self._b.save_ui_prefs(theme=theme)
        return {}

    # ------------------------------------------------------------ workbench
    def _job_summary(self, job):
        b = self._b
        try:
            config = json.loads(job['config'])
        except ValueError:
            config = {}
        target = int(config.get('draft_count', 3))
        running = b.busy and job['id'] == b.current_job_id
        drafts = b.db.task_drafts(job['id'])
        states = [Database.review_state(d['status']) for d in drafts]
        label = job_status_label(job)
        if running:
            chip, text = 'run', '生成中'
        elif label == '排队中':
            chip, text = 'stop', '排队中'
        elif states.count('待审核'):
            chip, text = 'wait', '等你审核'
        elif job['status'] in ('已完成', '无新文章'):
            chip, text = 'done', '已完成'
        elif job['status'] in ('失败', '已中断'):
            chip, text = 'fail', job['status']
        else:
            chip, text = 'stop', label
        step = phase_index(job['stage'])
        if not running and job['status'] in ('已完成', '部分完成'):
            step = 4
        return {
            'id': job['id'], 'chip': chip, 'chip_text': text, 'running': running, 'range': _range_label(config),
            'target': target, 'elapsed': _elapsed(job, running), 'step': step, 'stage': redact(job['stage'] or ''),
            'message': '' if running else redact(job['message'] or '')[:240],
            'waiting': states.count('待审核'), 'passed': states.count('已通过'), 'fix': states.count('需修改'),
            'processing': max(0, target - len(drafts)) if running else 0,
            'stopped': job['status'] == '已停止', 'retryable': job['status'] in RETRYABLE and label != '排队中',
            'drafts': [{'id': d['id'], 'title': d['title'], 'state': s, 'hint': ''}
                       for d, s in zip(drafts, states)],
        }

    @_safe
    def wb_state(self):
        b = self._b
        config = b.db.config()
        job = None
        if b.current_job_id:
            rows = b.db.rows('SELECT * FROM jobs WHERE id=?', (b.current_job_id,))
            job = rows[0] if rows else None
        if job is None:
            _, rows = b.db.top_jobs(1)
            job = rows[0] if rows else None
        summary = self._job_summary(job) if job else None
        if summary:
            hints = {d['id']: draft_hint(d['report']) for d in b.db.rows(
                'SELECT id,report FROM drafts WHERE id IN (%s)' % ','.join('?' * len(summary['drafts'])),
                [d['id'] for d in summary['drafts']])} if summary['drafts'] else {}
            for d in summary['drafts']:
                d['hint'] = hints.get(d['id'], '')
        queued = b.queued()
        return {'accounts': len(b.db.accounts(enabled=True)), 'mode': config.get('lookback_mode', 'rolling'),
                'hours': int(config.get('lookback_hours', 24)), 'count': int(config.get('draft_count', 3)),
                'busy': b.busy, 'job': summary, 'queue': len(queued),
                'restored': len([q for q in queued if q['id'] not in b.session_queue]), 'demo': b.demo}

    @_safe
    def wb_history(self, page):
        per = 5
        total, rows = self._b.db.top_jobs(per, max(0, int(page)) * per)
        out = []
        for job in rows:
            try:
                target = json.loads(job['config']).get('draft_count', 3)
            except ValueError:
                target = 0
            label = job_status_label(job)
            chip = 'done' if job['status'] == '已完成' else 'fail' if job['status'] in ('失败', '已中断') else 'stop'
            out.append({'id': job['id'], 'when': china_time(job['created_at']), 'status': label, 'chip': chip,
                        'ok': self._b.db.qualified_drafts(job['id']), 'target': target})
        return {'total': total, 'per': per, 'rows': out}

    @_safe
    def wb_detail(self, job_id):
        db = self._b.db
        job = db.rows('SELECT * FROM jobs WHERE id=?', (int(job_id),))[0]
        summary = self._job_summary(job)
        funnel = load_funnel(job.get('funnel')) or {'counts': {}, 'accounts': {}}
        c = funnel.get('counts', {})
        returned = c.get('returned', 0)
        outside = sum(c.get(k, 0) for k in ('out_of_window', 'deleted', 'invalid', 'repeated', 'cleared_blocked'))
        reasons = [(c.get(k, 0), label) for k, label in (
            ('used', '已经写过稿件'), ('below_threshold', '未达到最低阅读量或阅读量未知'),
            ('image_only', '以图片为主、文字不足'), ('body_failed', '正文或素材处理失败（旧记录可能含摘要）'),
            ('summary_failed', '摘要分析失败，正文已保存'),
            ('duplicate_body', '与其他文章正文重复'), ('material_cleared', '素材已清理'), ('over_limit', '超出本次篇数'))
            if c.get(k, 0)]
        try:
            selected = json.loads(job['selected'] or '[]')
        except ValueError:
            selected = []
        picked = []
        for article_id in selected[:10]:
            rows = db.rows('SELECT a.title,m.reads FROM articles a LEFT JOIN article_metrics m ON m.article_id=a.id '
                           'WHERE a.id=?', (article_id,))
            if rows:
                picked.append({'title': rows[0]['title'], 'reads': rows[0]['reads']})
        accounts = []
        for entry in (funnel.get('accounts') or {}).values():
            failed = entry.get('status') == 'failed'
            accounts.append({'name': entry.get('name'), 'got': (entry.get('counts') or {}).get('returned', 0),
                             'latest': china_time(entry['latest_published']) if entry.get('latest_published') else '—',
                             'failed': failed, 'error': redact(entry.get('error') or '')})
        try:
            warnings = json.loads(job.get('warnings') or '[]')
        except ValueError:
            warnings = []
        alert = ''
        if job['status'] in ('失败', '已中断'):
            alert = redact(job['message'] or '任务没有完成')
        elif job['status'] == '已停止':
            alert = '任务已手动停止，已写好的稿件都保留着。'
        elif warnings:
            alert = '；'.join(redact(str(w)) for w in warnings[:3])
        # 文章库“再写一篇” tasks reuse a chosen article and never collect: no funnel to explain.
        rewrite = str(job['run_key']).startswith('rewrite:')
        return {'title': '当前任务详情' if summary['running'] else
                f"{'再写一篇' if rewrite else '任务'} · {china_time(job['created_at'])}", 'rewrite': rewrite,
                'sub': f"{summary['range']} · 目标 {summary['target']} 篇 · " +
                       (f"正在{PHASES[min(summary['step'], 4)]}" if summary['running'] else job_status_label(job)),
                'alert': alert, 'has_funnel': bool(c) and not rewrite, 'accounts_n': len(accounts), 'returned': returned,
                'in_window': max(0, returned - outside), 'outside': outside,
                'removed': sum(n for n, _ in reasons), 'reasons': [f'{label} {n} 篇' for n, label in reasons],
                'candidates': c.get('queued', 0), 'picked': picked, 'accounts': accounts,
                'retryable': summary['retryable'] and not summary['running'], 'running': summary['running']}

    @_safe
    def wb_start(self, mode, count):
        b = self._b
        b.require_live()
        if not b.db.accounts(enabled=True):
            raise ValueError('请先在“公众号”页添加并启用至少一个公众号')
        config = b.db.config()
        hours = {'today': 24, '24': 24, '72': 72}.get(str(mode), int(config.get('lookback_hours', 24)))
        config.update(draft_count=int(count), lookback_mode='today' if mode == 'today' else 'rolling',
                      lookback_hours=hours, writing_mode='separate_web', selection_mode='hot', comments_enabled=False)
        validate(config)
        b.db.save_config(config)        # remembered as the next task's defaults
        return b.start_task(config)

    @_safe
    def wb_stop(self):
        b = self._b
        b.queue_paused = True
        b.engine.stop()
        return {}

    @_safe
    def wb_cancel_queue(self):
        b = self._b
        queued = b.queued()
        for job in queued:
            b.db.update_job(job['id'], status='已取消', stage='已取消', message='排队任务已取消，未开始执行')
            if job['id'] in b.session_queue:
                b.session_queue.remove(job['id'])
        return {'count': len(queued)}

    @_safe
    def wb_start_restored(self):
        b = self._b
        b.require_live()
        b.session_queue.extend(q['id'] for q in b.queued() if q['id'] not in b.session_queue)
        b.queue_paused = False
        b.next_queued()
        return {}

    @_safe
    def wb_resume(self, job_id):
        b = self._b
        b.require_live()
        b.require_idle()
        job = b.db.rows('SELECT * FROM jobs WHERE id=?', (int(job_id),))[0]
        if job['status'] not in RETRYABLE:
            raise ValueError('该任务已结束，无需继续；如需获取新文章，请点“重新采集”')
        b.queue_paused = False
        b.launch(job)
        return {}

    @_safe
    def wb_recollect(self):
        b = self._b
        b.require_live()
        return b.start_task(b.db.config())

    # ------------------------------------------------------------ accounts
    def _account_details(self):
        found = {}
        for job in self._b.db.rows("SELECT funnel,created_at FROM jobs WHERE funnel IS NOT NULL AND funnel<>'' "
                                   "ORDER BY id DESC LIMIT 30"):
            funnel = load_funnel(job['funnel']) or {}
            for entry in (funnel.get('accounts') or {}).values():
                identifier = entry.get('identifier')
                if not identifier or identifier in found:
                    continue
                returned = (entry.get('counts') or {}).get('returned', 0)
                found[identifier] = {
                    'when': china_time(job['created_at']), 'got': returned, 'ok': entry.get('status') != 'failed',
                    'latest': china_time(entry['latest_published']) if entry.get('latest_published') else '—',
                    'reason': redact(entry.get('error') or ''), 'coverage': format_coverage(entry.get('coverage'), returned)}
        return found

    @_safe
    def acc_list(self):
        db = self._b.db
        details = self._account_details()
        out = []
        for a in db.accounts():
            last = details.get(a['identifier'])
            if last:
                last['recent'] = [[r['title'], china_time(r['published_at'])] for r in db.rows(
                    'SELECT title,published_at FROM articles WHERE account_name=? AND cleared=0 '
                    'ORDER BY published_at DESC LIMIT 3', (a['name'],))]
            out.append({'id': a['id'], 'name': a['name'], 'mp': a['identifier'], 'on': bool(a['enabled']), 'last': last})
        return {'accounts': out, 'logged_in': self._b.vault.has('weread')}

    @_safe
    def acc_toggle(self, account_id):
        self._b.db.execute('UPDATE accounts SET enabled=1-enabled WHERE id=?', (int(account_id),))
        return {}

    @_safe
    def acc_delete(self, account_id):
        self._b.db.execute('DELETE FROM accounts WHERE id=?', (int(account_id),))
        return {}

    @_safe
    def acc_parse(self, link):
        from .weread import identifier_from_link
        return {'mp': identifier_from_link(link)}

    @_safe
    def acc_add(self, name, link):
        from .weread import identifier_from_link
        identifier = identifier_from_link(link)
        if not str(name).strip():
            raise ValueError('请填写公众号名称')
        if self._b.db.rows('SELECT id FROM accounts WHERE identifier=?', (identifier,)):
            raise ValueError('这个公众号已经在名单里了')
        return {'id': self._b.db.add_account(str(name), identifier)}

    @_safe
    def sync_start(self, force_login):
        b = self._b
        b.require_live()
        need_login = bool(force_login) or not b.vault.has('weread')
        cancel = threading.Event()
        state = {'phase': 'login' if need_login else 'reading', 'cancel': cancel, 'books': None, 'message': ''}
        b.sync = state

        def work():
            from .weread import browser_login
            if need_login:
                login_state, _ = browser_login(b.data_dir / 'weread-edge-profile', cancel)
                b.vault.set_many({'weread': login_state})
                state['phase'] = 'reading'
            source = b.weread_source(cancel)
            try:
                return source.shelf()
            finally:
                source.close()

        def done(books, error):
            if b.sync is not state:
                return
            if error:
                auth = isinstance(error, ApiError) and error.kind == 'weread_auth'
                state.update(phase='error', relogin=auth or need_login,
                             message=('登录已失效：' if auth else '读取书架失败：') + redact(str(error)))
            else:
                state.update(phase='choose', books=books)
        b.start_worker(work, done)
        return {'phase': state['phase']}

    @_safe
    def sync_state(self):
        from .weread import shelf_accounts
        state = self._b.sync
        if not state:
            return {'phase': 'closed'}
        out = {'phase': state['phase'], 'message': state.get('message', ''), 'relogin': state.get('relogin', False)}
        if state['phase'] == 'choose':
            have = {a['identifier'] for a in self._b.db.accounts()}
            out['shelf'] = [{'name': a['name'], 'mp': a['identifier'], 'have': a['identifier'] in have}
                            for a in shelf_accounts(state['books'])]
        return out

    @_safe
    def sync_close(self):
        state = self._b.sync
        if state:
            state['cancel'].set()
        self._b.sync = None
        return {}

    @_safe
    def sync_import(self, identifiers):
        from .weread import sync_books
        state = self._b.sync
        if not state or state['phase'] != 'choose':
            raise ValueError('请先读取书架')
        chosen = set(identifiers or [])
        if not chosen:
            raise ValueError('请至少勾选一个新增公众号')
        result = sync_books(self._b.db, state['books'], selected=chosen)
        self._b.sync = None
        return {'message': result, 'count': len(chosen)}

    # ------------------------------------------------------------ articles
    @_safe
    def art_list(self, q, account, period, sort, page):
        db = self._b.db
        where, params = ['a.cleared=0'], []
        if q:
            where.append("a.title LIKE ? ESCAPE '!'")
            params.append('%' + q.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%')
        if account and account != 'all':
            where.append('a.account_name=?')
            params.append(account)
        now = datetime.now(CHINA)
        since = {'today': now.replace(hour=0, minute=0, second=0, microsecond=0),
                 '3': now - timedelta(days=3), '7': now - timedelta(days=7)}.get(str(period))
        if since:
            where.append('a.published_at>=?')
            params.append(since.astimezone(timezone.utc).isoformat())
        # Unknown metrics sort after every valid value (0 included); the whole result is ordered before paging.
        order = {'reads': '(m.reads IS NULL), m.reads DESC, a.published_at DESC',
                 'likes': '(m.likes IS NULL), m.likes DESC, a.published_at DESC'}.get(sort, 'a.published_at DESC')
        sql = ' FROM articles a LEFT JOIN article_metrics m ON m.article_id=a.id WHERE ' + ' AND '.join(where)
        total = db.rows('SELECT COUNT(*) n' + sql, params)[0]['n']
        per = 10
        rows = db.rows("SELECT a.id,a.title,a.account_name,a.published_at,a.content<>'' body,a.error,m.reads,m.likes" + sql +
                       ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', params + [per, max(0, int(page)) * per])
        used = self._b.engine._used_articles()
        out = [{'id': r['id'], 't': r['title'], 'acct': r['account_name'], 'when': china_time(r['published_at']),
                'reads': r['reads'], 'likes': r['likes'],
                'state': article_state(r['id'] in used, r['body'], r['error'])}
               for r in rows]
        names = [r['account_name'] for r in db.rows(
            'SELECT DISTINCT account_name FROM articles WHERE cleared=0 ORDER BY account_name')]
        return {'total': total, 'all': db.rows('SELECT COUNT(*) n FROM articles WHERE cleared=0')[0]['n'],
                'per': per, 'rows': out, 'accounts': names}

    @_safe
    def art_detail(self, article_id):
        db = self._b.db
        a = db.rows('SELECT * FROM articles WHERE id=?', (int(article_id),))[0]
        m = db.rows('SELECT * FROM article_metrics WHERE article_id=?', (a['id'],))
        note = db.rows('SELECT source FROM metric_notes WHERE article_id=? ORDER BY rowid DESC LIMIT 1', (a['id'],))
        used = a['id'] in self._b.engine._used_articles()
        images = []
        root = self._b.data_dir.resolve()
        for item in db.article_images(a['id'])[:8]:
            try:
                path = (self._b.data_dir / item['local_path']).resolve()
                if path.is_relative_to(root) and path.is_file() and path.stat().st_size < 4 * 1024 * 1024:
                    kind = {'.png': 'png', '.gif': 'gif', '.webp': 'webp'}.get(path.suffix.lower(), 'jpeg')
                    images.append({'src': f'data:image/{kind};base64,' + base64.b64encode(path.read_bytes()).decode(),
                                   'alt': item.get('alt_text') or ''})
            except (OSError, ValueError):
                continue
        reads = m[0]['reads'] if m else None
        likes = m[0]['likes'] if m else None
        meta = [['阅读量来源', note[0]['source'] if note and note[0]['source'] else
                 ('采集服务没有返回（显示为“未知”，不参与热度排名）' if reads is None else '采集服务返回')],
                ['文章采集时间', china_time(a['fetched_at'], '%Y-%m-%d %H:%M')]]
        if m:
            meta.insert(1, ['数字采集时间', china_time(m[0]['observed_at'], '%Y-%m-%d %H:%M')])
        meta.append(['原文链接', a['url']])
        return {'acct': a['account_name'], 't': a['title'], 'when': china_time(a['published_at'], '%Y-%m-%d %H:%M'),
                'reads': reads, 'likes': likes, 'body': a['content'], 'images': images,
                'state': article_state(used, a['content'], a['error']),
                'error': redact(a['error'] or ''), 'meta': meta}

    @_safe
    def art_rewrite(self, article_id):
        b = self._b
        b.require_live()
        return b.start_task(None, b.engine.rewrite_job(article_id, b.db.config()))

    @_safe
    def art_open(self, article_id):
        url = self._b.db.rows('SELECT url FROM articles WHERE id=?', (int(article_id),))[0]['url']
        webbrowser.open(wechat_url(url))
        return {}

    # ------------------------------------------------------------ drafts
    @_safe
    def dr_list(self, q, tab, page):
        db = self._b.db
        where, params = ['cleared=0'], []
        if q:
            pattern = '%' + q.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            where.append("(title LIKE ? ESCAPE '!' OR body LIKE ? ESCAPE '!')")
            params += [pattern, pattern]
        base_where = ' AND '.join(where)
        counts = {'全部': db.rows('SELECT COUNT(*) n FROM drafts WHERE ' + base_where, params)[0]['n']}
        for state, values in (('待审核', ('待审核',)), ('需修改', ('需修改',)), ('已通过', ('已通过', '已审核'))):
            counts[state] = db.rows('SELECT COUNT(*) n FROM drafts WHERE ' + base_where + ' AND status IN (%s)'
                                    % ','.join('?' * len(values)), params + list(values))[0]['n']
        if tab and tab != '全部':
            values = ('已通过', '已审核') if tab == '已通过' else (tab,)
            where.append('status IN (%s)' % ','.join('?' * len(values)))
            params += list(values)
        sql = ' FROM drafts WHERE ' + ' AND '.join(where)
        total = db.rows('SELECT COUNT(*) n' + sql, params)[0]['n']
        per = 10
        rows = db.rows('SELECT id,title,status,updated_at,report' + sql + ' ORDER BY id DESC LIMIT ? OFFSET ?',
                       params + [per, max(0, int(page)) * per])
        return {'total': total, 'per': per, 'counts': counts,
                'rows': [{'id': r['id'], 't': r['title'], 's': Database.review_state(r['status']),
                          'when': china_time(r['updated_at']), 'flag': draft_hint(r['report'])} for r in rows]}

    @_safe
    def dr_get(self, draft_id):
        db = self._b.db
        d = db.rows('SELECT * FROM drafts WHERE id=? AND cleared=0', (int(draft_id),))
        if not d:
            raise ValueError('稿件不存在或已清理')
        d = d[0]
        try:
            sources = json.loads(d['sources'])
        except ValueError:
            sources = []
        src = [[str(s.get('source_id') or ''), str(s.get('title') or s.get('account_name') or '来源'),
                ('公众号原文' if str(s.get('source_id', '')).startswith('S') else '联网资料') +
                (' · ' + str(s.get('url')) if s.get('url') else '')]
               for s in sources if isinstance(s, dict) and s.get('source_id')]
        report = redact(d['report'] or '')
        manual = [line for line in report.splitlines() if line.startswith('【人工审核记录】')]
        machine_text = '\n'.join(line for line in report.splitlines() if not line.startswith('【人工审核记录】')).strip()
        log = []
        for line in manual:
            parts = line[len('【人工审核记录】'):].split(' · ')
            log.append([china_time(parts[0]) if parts else '', parts[1] if len(parts) > 1 else '',
                        ' · '.join(parts[2:]) if len(parts) > 2 else ''])
        versions = [[v['revision'], china_time(v['created_at'])] for v in db.rows(
            'SELECT revision,created_at FROM draft_versions WHERE draft_id=? ORDER BY revision DESC', (d['id'],))]
        return {'id': d['id'], 't': d['title'], 's': Database.review_state(d['status']), 'when': china_time(d['updated_at']),
                'rev': d['revision'], 'body': d['body'], 'flag': draft_hint(d['report']), 'sources': src,
                'machine': machine_text, 'log': log, 'versions': versions,
                'words': len(re.sub(r'\s|\[[SW]\d+\]|[#>*]', '', d['body'] or ''))}

    @_safe
    def dr_save(self, draft_id, title, body):
        self._b.db.edit_draft(int(draft_id), str(title), str(body), '待审核')
        return {}

    @_safe
    def dr_status(self, draft_id, state, note):
        self._b.db.set_draft_status(int(draft_id), str(state), str(note or ''))
        return {}

    @_safe
    def dr_export(self, draft_id, kind, fmt):
        import webview
        b = self._b
        if fmt not in EXPORT_FORMATS:
            raise ValueError('导出格式无效')
        d = b.db.rows('SELECT * FROM drafts WHERE id=?', (int(draft_id),))[0]
        name = re.sub(r'[\\/:*?"<>|]+', '-', ('素材报告-' if kind == 'report' else '') + d['title'])[:80] + '.' + fmt
        folder = b.db.config().get('export_dir') or ''
        if folder and Path(folder).is_dir():
            path = Path(folder) / name
        else:
            chosen = b.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name,
                                                 file_types=(f"{EXPORT_FORMATS[fmt]} (*.{fmt})",))
            if not chosen:
                return {'cancelled': True}
            path = Path(chosen if isinstance(chosen, str) else chosen[0])
            if path.suffix.lower() != '.' + fmt:
                path = path.with_suffix('.' + fmt)
        if kind == 'report':
            write_report(path, fmt, d['title'], d['report'])
        else:
            write_export(path, fmt, d['title'], d['body'], json.loads(d['sources']))
        return {'path': str(path)}

    # ------------------------------------------------------------ settings
    @_safe
    def set_get(self):
        b = self._b
        c = b.db.config()
        writer = c['writer_provider']
        preset = c.get('style_preset') or next((n for n, t in STYLE_PRESETS.items() if t == c['style']), '')
        return {
            'writer_model': c['providers'][writer]['model'], 'summary_provider': c['summary_provider'],
            'summary_model': c['providers'][c['summary_provider']]['model'], 'search_model': c['search_model'],
            'openai_url': c['providers']['openai']['base_url'], 'deepseek_url': c['providers']['deepseek']['base_url'],
            'words': c['target_words'], 'style_preset': preset,
            'style_custom': '' if preset else c['style'], 'style_extra': c.get('style_extra', ''),
            'export_format': c.get('export_format', 'docx'), 'export_dir': c.get('export_dir', ''),
            'schedule_enabled': c['schedule_enabled'], 'schedule_time': c['schedule_time'],
            'review': c['quality_review_enabled'], 'vision': c.get('image_articles') == 'vision',
            'recollect': c['allow_recollect_cleared'], 'startup': startup_enabled(),
            'limit': c['per_account_limit'], 'min_reads': c['min_reads'],
            'keys': {name: b.vault.has(name) for name in KEY_LABELS}, 'models': MODEL_OPTIONS,
            'version': version_text(), 'data_dir': str(b.data_dir), 'presets': list(STYLE_PRESETS)}

    @_safe
    def set_save(self, v):
        b = self._b
        b.require_live()
        c = b.db.config()            # unshown settings are kept as they are
        model = str(v['writer_model']).strip()
        if not model:
            raise ValueError('请选择或填写写作模型')
        writer = 'deepseek' if model.lower().startswith('deepseek') else 'openai'
        c['writer_provider'] = writer
        c['providers'][writer]['model'] = model
        summary_model = str(v['summary_model']).strip()
        summary = 'deepseek' if summary_model.lower().startswith('deepseek') else 'openai'
        if summary != writer or summary_model == model:
            c['providers'][summary]['model'] = summary_model
        elif summary_model != model:
            raise ValueError('写作和摘要都用同一家服务商时只能选同一个模型；请把摘要模型换成另一家的，或与写作模型一致')
        c['summary_provider'] = summary
        c['search_model'] = str(v['search_model']).strip()
        c['providers']['openai']['base_url'] = str(v['openai_url']).strip().rstrip('/')
        c['providers']['deepseek']['base_url'] = str(v['deepseek_url']).strip().rstrip('/')
        try:
            c['target_words'] = int(str(v['words']).strip())
            c['per_account_limit'] = int(str(v['limit']).strip())
            c['min_reads'] = int(str(v['min_reads']).strip())
        except ValueError:
            raise ValueError('篇幅、读取上限和最低阅读量需要填写整数') from None
        preset = str(v.get('style_preset') or '')
        c['style_preset'] = preset if preset in STYLE_PRESETS else str(v.get('style_custom') or '').strip()
        c['style_extra'] = str(v.get('style_extra') or '').strip()
        c['style'] = compose_style(c['style_preset'], c['style_extra'])
        if not c['style']:
            raise ValueError('请填写写作风格')
        c['export_format'] = v['export_format']
        c['export_dir'] = str(v.get('export_dir') or '')
        c['schedule_time'] = str(v['schedule_time']).strip()
        c['quality_review_enabled'] = bool(v['review'])
        c['image_articles'] = 'vision' if v['vision'] else 'skip'
        c['allow_recollect_cleared'] = bool(v['recollect'])
        c['comments_enabled'] = False
        if v['schedule_enabled'] and not c['schedule_enabled']:
            c['schedule_start'] = datetime.now(ZoneInfo(c['timezone'])).date().isoformat()
        c['schedule_enabled'] = bool(v['schedule_enabled'])
        validate(c)
        keys = {name: str(value).strip() for name, value in (v.get('keys') or {}).items()
                if name in KEY_LABELS and str(value).strip()}
        if keys:
            b.vault.set_many(keys)
        if bool(v['startup']) != startup_enabled():
            set_startup(bool(v['startup']))
        b.db.save_config(c)
        return {}

    @_safe
    def set_check(self, billed):
        b = self._b
        b.require_live()
        result = {}
        try:
            if not b.vault.has('weread'):
                raise ValueError('尚未扫码登录微信读书（到“公众号”页扫码同步书架）')
            source = b.weread_source()
            try:
                count = len(source.shelf())
            finally:
                source.close()
            have = '、'.join(KEY_LABELS[n] for n in ('dajiala', 'everyinfra') if b.vault.has(n)) or '未配置'
            result['collect'] = {'ok': True, 'text': f'正常 · 书架 {count} 个公众号 · 采集服务 Key：{have}'}
        except Exception as exc:
            result['collect'] = {'ok': False, 'text': '异常：' + redact(str(exc))[:160]}
        if billed:
            c = b.db.config()
            provider = c['writer_provider']
            settings = c['providers'][provider]
            try:
                Model(provider, settings, b.vault.get(provider), Transport()).complete(
                    '只输出 JSON 对象 {"ok":true}', '测试连接，输出 JSON', tokens=128)
                result['model'] = {'ok': True, 'text': f"正常 · {settings['model']}"}
            except Exception as exc:
                result['model'] = {'ok': False, 'text': '异常：' + redact(str(exc))[:160]}
        result['time'] = datetime.now(CHINA).strftime('%H:%M')
        return result

    @_safe
    def set_clear_key(self, name):
        if name not in KEY_LABELS:
            raise ValueError('未知的 Key')
        self._b.vault.delete(name)
        return {}

    @_safe
    def set_pick_folder(self):
        import webview
        chosen = self._b.window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {'cancelled': True}
        return {'path': chosen if isinstance(chosen, str) else chosen[0]}

    @_safe
    def set_backup(self):
        import webview
        b = self._b
        b.require_idle()
        chosen = b.window.create_file_dialog(webview.FileDialog.SAVE, save_filename='公众号工作台备份.sqlite',
                                             file_types=('SQLite 备份 (*.sqlite)',))
        if not chosen:
            return {'cancelled': True}
        path = chosen if isinstance(chosen, str) else chosen[0]
        b.db.backup(path)
        return {'path': path}

    @_safe
    def set_restore(self):
        import webview
        b = self._b
        b.require_idle()
        chosen = b.window.create_file_dialog(webview.FileDialog.OPEN, file_types=('SQLite 备份 (*.sqlite)',))
        if not chosen:
            return {'cancelled': True}
        path = chosen if isinstance(chosen, str) else chosen[0]
        b.db.backup(b.data_dir / ('before-restore-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite'))
        b.db.restore(path)
        return {'path': path}

    @_safe
    def set_clear_data(self, confirmed=False):
        if confirmed is not True:
            raise ValueError('请先确认清理范围')
        b = self._b
        with b.lock:
            b.require_idle()
            if b.db.rows("SELECT id FROM jobs WHERE status='运行中' LIMIT 1"):
                raise ValueError('有运行中的任务，请先停止任务再清理')
            backup = b.data_dir / ('before-clear-' + datetime.now(CHINA).strftime('%Y%m%d-%H%M%S-%f') + '.sqlite')
            b.db.backup(backup)  # A failed backup must never proceed to deletion.
            removed = b.db.clear_all(preserve_accounts=True)
            b.session_queue.clear()
            b.queue_paused = True
            b.current_job_id = None
            b.last_message = ''
            return {'path': str(backup), 'removed': removed}

    @_safe
    def set_open_dir(self):
        os.startfile(self._b.data_dir)
        return {}


def window_size(preferred=(1280, 860)):
    """Initial window size in logical pixels, fitted to the screen's work area (taskbar excluded).

    Returns (width, height, maximize): small laptop screens open maximized so nothing is cut off.
    """
    try:
        import ctypes
        from ctypes import wintypes
        area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)   # SPI_GETWORKAREA
        scale = ctypes.windll.user32.GetDpiForSystem() / 96 or 1
        free = ((area.right - area.left) / scale, (area.bottom - area.top) / scale)
    except Exception:
        return preferred[0], preferred[1], False
    if free[0] < preferred[0] + 40 or free[1] < preferred[1] + 40:
        return int(free[0] * .92), int(free[1] * .92), True
    return preferred[0], preferred[1], False


def run(db, vault, data_dir, demo=False, minimized=False):
    """Open the web UI; returns when the window is closed (or the app quits from the tray)."""
    import webview
    backend = Backend(db, vault, data_dir, demo)
    api = Api(backend)
    width, height, fill = window_size()
    window = webview.create_window('WeWriter 公众号工作台' + (' · 演示数据' if demo else ''),
                                   str(WEB_DIR / 'workbench.html'), js_api=api, width=width, height=height,
                                   min_size=(min(900, width), min(600, height)), maximized=fill,
                                   hidden=minimized, background_color='#0e1416')
    backend.window = window
    state = {'quit': False, 'tray': None}

    def show():
        window.show()
        window.restore()

    def quit_app():
        state['quit'] = True
        if backend.busy:
            backend.queue_paused = True
            backend.engine.stop()
        if state['tray']:
            state['tray'].stop()
        window.destroy()

    def tray():
        try:
            import pystray
            from PIL import Image
            from .ui import asset_path
            icon = Image.open(asset_path('wewriter-cat.png')).convert('RGBA').resize((64, 64))
            state['tray'] = pystray.Icon('WeWriter', icon, '公众号工作台', menu=pystray.Menu(
                pystray.MenuItem('打开工作台', lambda: show(), default=True),
                pystray.MenuItem('退出软件', lambda: quit_app())))
            state['tray'].run_detached()
        except Exception:
            logger.exception('托盘不可用')

    def closing():
        if state['quit']:
            return True
        keep = window.create_confirmation_dialog(
            '关闭窗口', '是否继续在托盘运行？\n\n确定：隐藏窗口，定时任务继续。\n取消：退出软件。')
        if keep and state['tray']:
            window.hide()
            return False
        state['quit'] = True
        if backend.busy:
            backend.queue_paused = True
            backend.engine.stop()
        return True
    window.events.closing += closing
    threading.Thread(target=backend.schedule_loop, daemon=True, name='schedule').start()
    tray()
    webview.start(http_server=True, private_mode=True)
    if state['tray']:
        try:
            state['tray'].stop()
        except Exception:
            pass
