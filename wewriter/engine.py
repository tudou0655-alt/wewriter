"""Collection and writing pipeline.

A task runs as a sequence of small stages. Every stage caches its result in
SQLite, so a retry resumes after the last completed stage instead of paying
for it again:

    collect -> materials (body, images, summaries) -> style -> comments
            -> research -> compose (write + review) -> report/save

Failures carry a structured ``code`` (see ``ErrorCode``) that is stored in
``jobs.error_code``; batch logic decides on codes, never on message text.
"""
import json
import re
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from .api import ApiError, Cancelled, Dajiala, EveryInfra, Model, Transport, digest, parse_date, wechat_url
from .config import validate, migrate
from .db import stamp
from .hotspots import count, rank
from .logs import logger, redact


class ErrorCode:
    MATERIAL = 'material'          # this article cannot provide usable source text -> next article may replace it
    NO_ACCOUNTS = 'no_accounts'
    COLLECT = 'collect'            # every account failed to list articles
    AUTH = 'auth'                  # WeRead login / API key / balance / permission
    NETWORK = 'network'
    MODEL_OUTPUT = 'model_output'  # model output failed validation even after one repair
    API = 'api'
    CONFIG = 'config'
    CANCELLED = 'cancelled'
    INTERNAL = 'internal'


class TaskError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def error_code(exc):
    if isinstance(exc, Cancelled):
        return ErrorCode.CANCELLED
    if getattr(exc, 'code', None):
        return exc.code
    if isinstance(exc, ApiError):
        if exc.kind in ('weread_auth', 'weread_ticket') or exc.fatal:
            return ErrorCode.AUTH
        if exc.kind == 'network':
            return ErrorCode.NETWORK
        if exc.kind == 'validation':
            return ErrorCode.MODEL_OUTPUT
        return ErrorCode.API
    if isinstance(exc, ValueError):
        return ErrorCode.CONFIG
    return ErrorCode.INTERNAL


FINISHED = ("已完成", "无新文章", "已清理")

# Collection funnel layers. Every listed article ends in exactly one of the keys
# below its parent: returned = deleted + out_of_window + invalid + cleared_blocked
# + repeated + candidates; candidates = below_threshold + used + over_limit + queued.
COLLECT_KEYS = ('returned', 'deleted', 'out_of_window', 'invalid', 'cleared_blocked', 'repeated',
                'candidates', 'below_threshold', 'used', 'over_limit', 'queued')
# Each article taken from the queue ends in exactly one of these.
MATERIAL_KEYS = ('material_cleared', 'body_failed', 'summary_failed', 'image_only', 'duplicate_body', 'usable')
FUNNEL_LABELS = {
    'returned': '接口返回', 'deleted': '已删除', 'out_of_window': '超出时间范围', 'invalid': '条目无效',
    'cleared_blocked': '已清理且不允许重采', 'repeated': '重复条目（已计入其他位置）', 'candidates': '合格候选',
    'below_threshold': '未达热度门槛（含无阅读量）', 'used': '已用于既有稿件', 'over_limit': '超出单次篇数上限',
    'queued': '进入处理队列', 'material_cleared': '原素材已清理', 'body_failed': '正文获取失败（旧记录可能含摘要失败）',
    'summary_failed': '摘要分析失败（正文已保存）',
    'image_only': '图片型跳过', 'duplicate_body': '正文重复', 'usable': '可用素材',
}
# China Standard Time has no DST, so a fixed offset avoids depending on the
# customer's local zone or on tzdata.
CHINA_TZ = timezone(timedelta(hours=8))
# Where an article's read/like counts came from. Only fields the adapters actually read are
# named; a source that does not report its origin is shown as unlabelled, never guessed.
METRIC_SOURCES = {
    'weread_list': '微信读书公众号文章列表（mpInfo.readNum / likeNum）',
    'weread_skill': '微信读书 Skill 单篇详情（mpInfo.readNum / likeNum）',
    'everyinfra_metrics': 'EveryInfra 文章传播数据（read_count / like_count）',
    'dajiala_list': '极致了历史发文列表（read / zan）',
}
def window_start(config, now):
    """Start of the article time window: the China-time calendar day of the
    task for 'today', otherwise the rolling lookback_hours."""
    if config.get('lookback_mode') == 'today':
        china = timezone(timedelta(hours=8))
        return now.astimezone(china).replace(hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(hours=config["lookback_hours"])


# How an account's articles were listed (``source.last_coverage['mode']``).
COVERAGE_ENDS = {
    'limit': '达到单账号列表上限，时间窗内可能仍有未覆盖文章',
    'exhausted': '接口未返回更多文章',
    'scroll_end': '列表翻页中断，未确认是否还有更早文章',
    'window': '已翻页到时间窗起点之前',
    'provider_page': '数据服务单次返回篇数有限，未覆盖到时间窗起点',
}
RESEARCH_DEGRADED_MARK = '联网搜索连接失败'


def china_time(value):
    """Format a stored ISO time as China Standard Time; '未知' when absent or invalid."""
    when = _published_at(value)
    return f'{when.astimezone(CHINA_TZ):%Y-%m-%d %H:%M}（中国时间）' if when else '未知'


def metric_status(raw, value):
    """'measured' (a valid count, 0 included), 'missing' (field absent/empty) or 'invalid'."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 'missing'
    return 'measured' if value is not None else 'invalid'


class Funnel:
    """Structured funnel of the current attempt, persisted as ``jobs.funnel`` JSON.

    Observability only: it records outcomes, it never influences selection.
    """

    def __init__(self, db, job_id, data):
        self.db, self.job_id, self.data = db, job_id, data

    @staticmethod
    def empty():
        # accounts: unique account key -> {name, identifier, status, error, counts}
        # owners: article id -> account key that listed it; outcomes: article id -> material key
        return {'version': 2, 'collection': None, 'counts': {k: 0 for k in COLLECT_KEYS + MATERIAL_KEYS},
                'drafts': 0, 'accounts': {}, 'owners': {}, 'outcomes': {}}

    @classmethod
    def for_attempt(cls, db, job, reuse_collection):
        """Start a fresh funnel. A retry that reuses the stored selection keeps the
        collection layers of the attempt that produced it; nothing is accumulated."""
        data = cls.empty()
        previous = load_funnel(job.get('funnel')) if reuse_collection else None
        if previous and previous.get('collection'):
            data['collection'] = {**previous['collection'], 'reused': True}
            data['counts'].update({k: previous['counts'].get(k, 0) for k in COLLECT_KEYS})
            for key, account in previous['accounts'].items():
                data['accounts'][key] = {
                    'name': account.get('name', key), 'identifier': account.get('identifier', ''),
                    'status': account.get('status', 'ok'), 'error': account.get('error', ''),
                    'counts': {k: v for k, v in account.get('counts', {}).items() if k in COLLECT_KEYS}}
                for field in ('latest_published', 'coverage', 'metrics'):
                    if account.get(field):
                        data['accounts'][key][field] = deepcopy(account[field])
            data['owners'] = dict(previous.get('owners', {}))
        funnel = cls(db, job['id'], data)
        funnel.save()
        return funnel

    def account(self, key, name=None, identifier=''):
        return self.data['accounts'].setdefault(
            key, {'name': name or key, 'identifier': identifier, 'status': 'ok', 'error': '', 'counts': {}})

    def add(self, key, account=None, n=1):
        if n <= 0:
            return
        counts = self.data['counts']
        counts[key] = counts.get(key, 0) + n
        if account is not None:
            counts = self.account(account)['counts']
            counts[key] = counts.get(key, 0) + n

    def published(self, key, when):
        """Keep the newest parseable publish time the account returned, stored as UTC ISO."""
        account = self.account(key)
        latest = _published_at(account.get('latest_published'))
        if latest is None or when > latest:
            account['latest_published'] = when.astimezone(timezone.utc).isoformat()

    def collection_warnings(self):
        """Collection-stage warnings of the attempt that produced the stored selection."""
        collection = self.data.get('collection') or {}
        warnings = collection.get('warnings')
        return [str(w) for w in warnings] if isinstance(warnings, list) else []

    def metric(self, key, status, reads):
        """Count one candidate's read-count quality for its account."""
        metrics = self.account(key).setdefault('metrics', {'measured': 0, 'zero': 0, 'unknown': 0})
        if status == 'measured':
            metrics['measured'] += 1
            if reads == 0:
                metrics['zero'] += 1
        else:
            metrics['unknown'] += 1

    def article_account(self, article):
        """Key of the account that listed the article; articles without a recorded owner
        (e.g. a batch child or rewrite) fall back to the stored account name."""
        key = self.data.setdefault('owners', {}).get(str(article['id']))
        if key is None:
            key = 'name:' + article['account_name']
            self.account(key, article['account_name'])
        return key

    def material(self, key, article):
        """Record the single material outcome of one queued article and persist it."""
        self.add(key, self.article_account(article))
        self.data.setdefault('outcomes', {})[str(article['id'])] = key
        self.save()

    def fail_account(self, name, exc):
        account = self.account(name)
        account['status'], account['error'] = 'failed', redact(str(exc))[:200]
        self.data['collection']['accounts_failed'] += 1

    def save(self):
        self.db.update_job(self.job_id, funnel=json.dumps(self.data, ensure_ascii=False))


def load_funnel(raw):
    try:
        data = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get('counts'), dict) else None


def _published_at(value):
    try:
        when = datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None
    return when if when and when.tzinfo else None


def format_funnel(raw):
    """Readable Chinese funnel for the run-record detail window."""
    data = load_funnel(raw)
    if not data:
        return '该历史任务没有采集统计'
    c = data['counts']
    collection = data.get('collection')

    def reasons(counts, keys):
        return [f'{FUNNEL_LABELS[k]} {counts[k]}' for k in keys if counts.get(k)]

    lines = []
    if collection:
        lines.append(f"结果：扫描 {collection['accounts_enabled']} 个启用公众号"
                     f"（成功 {collection['accounts_ok']}，失败 {collection['accounts_failed']}），"
                     f"接口共返回 {c['returned']} 篇；合格候选 {c['candidates']} 篇，进入处理队列 {c['queued']} 篇，"
                     f"最终可用素材 {c['usable']} 篇，完成稿件 {data['drafts']} 篇。")
        if collection.get('reused'):
            lines.append('（本次重试沿用上次采集的选文，采集部分为当时的统计；素材与稿件为本次结果。）')
        degraded = [a for a in data['accounts'].values()
                    if (a.get('coverage') or {}).get('mode') == 'latest_only' and a.get('status') != 'failed']
        if degraded:
            lines.append(f'注意：{len(degraded)} 个公众号的历史列表不可用，已降级为仅获取最新一篇；'
                         '扩大时间范围只改变筛选条件，不能恢复这些公众号的历史覆盖。')
    else:
        lines.append(f"结果：本任务沿用已保存的选文，没有本次采集统计；最终可用素材 {c['usable']} 篇，"
                     f"完成稿件 {data['drafts']} 篇。")
    lines.append('')
    lines.append('全局阶段：')
    if collection:
        lines.append(f"- 接口返回：{c['returned']}")
        lines += ['  - 排除：' + r for r in reasons(c, COLLECT_KEYS[1:6])]
        lines.append(f"- 合格候选：{c['candidates']}")
        lines += ['  - 排除：' + r for r in reasons(c, ('below_threshold', 'used', 'over_limit'))]
        lines.append(f"- 进入处理队列：{c['queued']}")
    lines += ['  - 排除：' + r for r in reasons(c, MATERIAL_KEYS[:-1])]
    lines.append(f"- 可用素材：{c['usable']}")
    lines.append(f"- 完成稿件：{data['drafts']}")
    quality = format_quality(data.get('quality'))
    if quality:
        lines.append('- 质量：' + quality)
    if data['accounts']:
        lines.append('')
        lines.append('各公众号：')
        names = [a.get('name', k) for k, a in data['accounts'].items()]
        for key, account in data['accounts'].items():
            name = account.get('name', key)
            if names.count(name) > 1 and account.get('identifier'):
                name += f"（{account['identifier']}）"
            ac = account.get('counts', {})
            if account.get('status') == 'failed':
                lines.append(f"- {name}：采集失败，{account.get('error') or '原因未知'}")
                continue
            parts = []
            if collection:
                parts.append(f"返回 {ac.get('returned', 0)} 篇，进入候选 {ac.get('candidates', 0)} 篇，"
                             f"进入处理队列 {ac.get('queued', 0)} 篇")
            parts.append(f"可用素材 {ac.get('usable', 0)} 篇")
            skipped = reasons(ac, tuple(k for k in COLLECT_KEYS + MATERIAL_KEYS
                                        if k not in ('returned', 'candidates', 'queued', 'usable')))
            if skipped:
                parts.append('跳过：' + '、'.join(skipped))
            latest = _published_at(account.get('latest_published'))
            if ac.get('out_of_window') and latest:
                parts.append(f"最近返回文章：{latest.astimezone(CHINA_TZ):%Y-%m-%d %H:%M}（中国时间）")
            coverage = format_coverage(account.get('coverage'), ac.get('returned', 0))
            if coverage:
                parts.insert(0, coverage)
            metrics = account.get('metrics')
            if collection and isinstance(metrics, dict) and any(metrics.values()):
                parts.append(f"阅读量：有效 {metrics.get('measured', 0)} 篇（其中为 0 的 {metrics.get('zero', 0)} 篇）"
                             + (f"，未知 {metrics['unknown']} 篇（不参与热点排名）" if metrics.get('unknown') else ''))
            latest = _published_at(account.get('latest_published'))
            if latest and not ac.get('out_of_window') and (account.get('coverage') or {}).get('mode') == 'latest_only':
                parts.append(f"最近返回文章：{latest.astimezone(CHINA_TZ):%Y-%m-%d %H:%M}（中国时间）")
            lines.append(f"- {name}：" + '；'.join(parts))
    return '\n'.join(lines)


def format_coverage(coverage, returned):
    """Describe how an account was listed. Never claims a complete history."""
    if not isinstance(coverage, dict):
        return ''
    mode = coverage.get('mode')
    if mode == 'latest_only':
        reason = coverage.get('reason') or '原因未记录'
        return f'采集方式：降级：仅最新一篇（{reason}）'
    if mode == 'history_list':
        end = COVERAGE_ENDS.get(coverage.get('end'), '列表结束原因未记录')
        via = {'dajiala': '，来源：极致了', 'everyinfra': '，来源：EveryInfra'}.get(coverage.get('provider'), '')
        return f'采集方式：历史列表（本次返回 {returned} 篇{via}，{end}；不代表该公众号完整历史）'
    if coverage.get('limit_reached'):
        return f"采集方式：接口列表（{COVERAGE_ENDS['limit']}）"
    return ''


def format_quality(quality):
    if not isinstance(quality, dict):
        return ''
    if 'research_degraded' in quality:   # batch parent
        parts = [f"完成稿件 {quality.get('drafts', 0)} 篇"]
        if quality.get('research_degraded'):
            parts.append(f"联网研究降级为仅原文写作 {quality['research_degraded']} 篇")
        if quality.get('flagged'):
            parts.append(f"有处理或质量提示 {quality['flagged']} 篇")
        return '；'.join(parts)
    research = {'done': '已完成联网补充', 'degraded': '连接失败，已降级为仅基于公众号原文',
                'not_used': '本任务未使用联网研究'}.get(quality.get('research'), '')
    review = {'done': '已完成', 'failed': '未完成，保留初稿', 'off': '未开启'}.get(quality.get('review'), '')
    return '；'.join(p for p in ((f'联网研究：{research}' if research else ''),
                                (f'AI 二审：{review}' if review else '')) if p)


class _Run:
    """Per-execution state shared by the stages of one job."""

    def __init__(self, engine, job, config, transport, source):
        self.engine, self.job, self.config = engine, job, config
        self.transport, self.source = transport, source
        self.warnings = []
        self._models = {}
        self.funnel = None
        self.review_state = 'off'

    @property
    def db(self):
        return self.engine.db

    def warn(self, message):
        message = redact(message)
        self.warnings.append(message)
        self.db.update_job(self.job["id"], warnings=json.dumps(self.warnings, ensure_ascii=False))
        self.engine.notify(message)

    def stage(self, name, message=None):
        self.db.update_job(self.job['id'], stage=name)
        if message:
            self.engine.notify(message)

    def model(self, name):
        if name not in self._models:
            e = self.engine
            self._models[name] = e.model_factory(name, self.config["providers"][name], e.vault.get(name),
                                                 self.transport)
        return self._models[name]

    @property
    def summarizer(self):
        return self.model(self.config["summary_provider"])

    @property
    def writer(self):
        return self.model(self.config["writer_provider"])

    def cached(self, article_id, key, compute):
        rows = self.db.rows('SELECT result FROM analyses WHERE article_id=? AND cache_key=?', (article_id, key))
        if rows:
            return json.loads(rows[0]['result'])
        value = compute()
        self.db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)',
                        (article_id, key, json.dumps(value, ensure_ascii=False), stamp()))
        return value


class Engine:
    def __init__(self, db, vault, notify=lambda text: None, source_factory=None, model_factory=Model,
                 research_factory=None, profile_dir=None):
        self.db, self.vault, self.notify = db, vault, notify
        self.source_factory, self.model_factory = source_factory, model_factory
        from .research import WebResearch
        self.research_factory = research_factory or WebResearch
        self.profile_dir = profile_dir
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def stop(self):
        self.cancel.set()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("任务已停止，可在运行记录中重试")

    # ------------------------------------------------------------------ run

    def run(self, run_key, config, reset_cancel=True, shared_source=None):
        if not self.lock.acquire(blocking=False):
            raise ValueError("已有任务运行中")
        if reset_cancel:
            self.cancel.clear()
        job = None
        source = shared_source
        owns_source = shared_source is None
        try:
            config = migrate(config)
            validate(config)
            job = self.db.job(run_key, config)
            if job["status"] in FINISHED or (job['status'] == '部分完成'
                                             and json.loads(job['config']).get('writing_mode') != 'separate_web'):
                return job["status"]
            # Retry uses the original non-secret configuration and time window.
            config = migrate(json.loads(job["config"]))
            validate(config)
            if self.db.rows("SELECT id FROM drafts WHERE job_id=?", (job["id"],)):
                self.db.update_job(job["id"], status="已完成", message="已有稿件，未重复生成", error_code='')
                return "已完成"
            # A retry is a new attempt: previous warnings stay in history only.
            self.db.update_job(job["id"], status="运行中", message="正在准备任务", warnings="[]", error_code='')
            # Likewise the funnel describes this attempt only and is rebuilt, never summed.
            funnel = Funnel.for_attempt(self.db, job, reuse_collection=bool(json.loads(job["selected"])))
            transport = Transport(self.cancel)
            if source is None:
                source = self._open_source(transport)
            ctx = _Run(self, job, config, transport, source)
            ctx.funnel = funnel
            # Collection notes of a reused selection (coverage, list limits) remain valid and
            # are carried once; warnings of the earlier material/writing attempt are not.
            ctx.warnings = funnel.collection_warnings()
            if ctx.warnings:
                self.db.update_job(job["id"], warnings=json.dumps(ctx.warnings, ensure_ascii=False))
            # Resolve both models up front so a missing API key fails before
            # any paid collection or summarising work is done.
            ctx.summarizer, ctx.writer
            selected = json.loads(job["selected"])
            if not selected:
                selected = self._collect(ctx)
                if not selected:
                    status = "失败" if ctx.warnings else "无新文章"
                    message = ("采集存在异常且没有可用新文章，请查看警告后重试" if ctx.warnings
                               else "时间范围内没有未使用的新文章")
                    self.db.update_job(job["id"], status=status, message=message,
                                       error_code=ErrorCode.COLLECT if ctx.warnings else '')
                    return status
            if config.get('writing_mode') == 'separate_web':
                # The batch owns one source/browser session; children reuse it.
                return self.run_separate(job, config, selected, source)
            return self._write_one(ctx, selected)
        except (Cancelled, ValueError, ApiError) as exc:
            code = error_code(exc)
            if not isinstance(exc, Cancelled):
                logger.warning("任务 %s 失败 [%s]：%s", run_key, code, redact(str(exc)))
            status = "已停止" if isinstance(exc, Cancelled) else "失败"
            if job:
                self.db.update_job(job["id"], status=status, message=str(exc), error_code=code)
            self.notify(redact(str(exc)))
            return status
        except Exception as exc:
            logger.exception("任务 %s 内部异常", run_key)
            message = (f"内部处理异常（{type(exc).__name__}），数据已保留，可重试；"
                       "详情见数据目录 logs\\wewriter.log")
            if job:
                self.db.update_job(job["id"], status="失败", message=message, error_code=ErrorCode.INTERNAL)
            self.notify(message)
            raise
        finally:
            if owns_source and source is not None and hasattr(source, 'close'):
                try:
                    source.close()
                except Exception:
                    logger.exception("关闭采集会话失败")
            self.lock.release()
            if job and shared_source is None and str(run_key).startswith('batch:'):
                try:
                    self.refresh_parent(job['id'])
                except Exception:
                    logger.exception("刷新批次状态失败")

    def article_drafts(self, article_id):
        """Drafts already written from one source article, oldest first."""
        return [row for row in self.db.rows('SELECT id,title,body,sources FROM drafts WHERE cleared=0 ORDER BY id')
                if any(s.get('article_id') == int(article_id) for s in json.loads(row['sources'] or '[]'))]

    def rewrite_job(self, article_id, config, note=''):
        """A new single-article task from a source that may already have drafts (customer
        2026-10-25: a past hit is written again). Earlier titles are passed as avoid_titles so
        the new draft takes another angle; earlier drafts are untouched. Not started here."""
        article_id = int(article_id)
        if not self.db.rows('SELECT id FROM articles WHERE id=?', (article_id,)):
            raise ValueError('文章不存在或已被清理')
        # One pending rewrite per article: repeated clicks used to queue near-identical drafts.
        for row in self.db.rows("SELECT status FROM jobs WHERE run_key LIKE ?", (f'rewrite:{article_id}:%',)):
            if row['status'] in ('等待中', '运行中'):
                raise ValueError('这篇文章已经在“再写一篇”的队列里，等它完成后再写下一篇')
        drafts = self.article_drafts(article_id)
        # Earlier angles = title + section headings, so the writer can see the structure to avoid.
        angles = [d['title'] + '｜小标题：' + ' / '.join(re.findall(r'(?m)^#{1,6}\s*(.+?)\s*$', d['body'] or '')[:8])
                  for d in drafts]
        config = deepcopy(config)
        config.update(writing_mode='single_web', selection_mode='hot', min_sources=1, article_limit=1,
                      draft_count=1, topic=str(note or '').strip()[:300], avoid_titles=[d['title'] for d in drafts],
                      avoid_angles=angles)
        job = self.db.job(f'rewrite:{article_id}:{uuid.uuid4().hex}', config)
        self.db.update_job(job['id'], selected=json.dumps([article_id]))
        return self.db.rows('SELECT * FROM jobs WHERE id=?', (job['id'],))[0]

    def new_collection(self, config):
        """Explicitly collect again with the current configuration.

        Always a new task (new key, so a new time window ending now); earlier tasks and their
        drafts are untouched, and articles used in saved drafts stay excluded by _collect."""
        return self.run('manual:' + uuid.uuid4().hex, config)

    def _open_source(self, transport):
        if self.source_factory:
            return self.source_factory(self.vault.get("everyinfra"), transport)
        from .weread import WeReadSource
        # EveryInfra supplies the article list and read counts when its key is
        # saved: the WeRead list is refused account-wide (-2041) for long periods.
        provider = (EveryInfra(self.vault.get('everyinfra'), transport)
                    if self.vault.has('everyinfra') and self.vault.get('everyinfra') else None)
        # 极致了 is tried first: its list carries read counts and it answered
        # every request in the 2026-10-25 trial where EveryInfra was mostly busy.
        primary = (Dajiala(self.vault.get('dajiala'), transport)
                   if self.vault.has('dajiala') and self.vault.get('dajiala') else None)
        return WeReadSource(self.vault.get("weread"), self.db, self.cancel, provider=provider,
                            primary=primary,
                            state_saver=lambda value: self.vault.set_many({'weread': value}),
                            profile_dir=self.profile_dir,
                            skill_key=(self.vault.get('weread_skill')
                                       if self.vault.has('weread_skill') else None))

    # -------------------------------------------------------------- collect

    def _collect(self, ctx):
        """List recent articles of every enabled account and rank them."""
        config, job, db = ctx.config, ctx.job, self.db
        accounts = db.accounts(enabled=True)
        if not accounts:
            raise TaskError("请先添加并启用至少一个公众号", ErrorCode.NO_ACCOUNTS)
        now = datetime.fromisoformat(job["created_at"])
        earliest = window_start(config, now)
        candidates, successes, recollect = {}, 0, set()
        funnel, owner = ctx.funnel, {}
        collection = {'reused': False, 'accounts_enabled': len(accounts), 'accounts_ok': 0, 'accounts_failed': 0,
                      'warnings': []}
        funnel.data['collection'] = collection

        def collect_warn(message):
            collection['warnings'].append(redact(message))
            ctx.warn(message)
        if hasattr(ctx.source, 'list_since'):
            ctx.source.list_since = earliest
        for account in accounts:
            self.check()
            who = 'acct:' + account['identifier']
            funnel.account(who, account['name'], account['identifier'])
            self.notify(f"正在同步：{account['name']}")
            try:
                items = ctx.source.articles(account["identifier"], config["per_account_limit"])
                successes += 1
                collection['accounts_ok'] += 1
            except ApiError as exc:
                funnel.fail_account(who, exc)
                funnel.save()
                if exc.fatal:
                    raise
                collect_warn(f"{account['name']}：{exc}")
                continue
            funnel.add('returned', who, len(items))
            funnel.account(who)['coverage'] = self._coverage(ctx.source, items, config["per_account_limit"])
            if len(items) >= config["per_account_limit"]:
                collect_warn(f"{account['name']} 返回条数达到单账号上限，时间窗内可能仍有未覆盖文章")
            for item in items:
                self.check()
                try:
                    if not isinstance(item, dict):
                        raise ValueError("文章条目格式无效")
                    if item.get('collection_warning'):
                        collect_warn(f"{account['name']}：{item['collection_warning']}")
                    if item.get("is_deleted") in (True, 1, "true"):
                        funnel.add('deleted', who)
                        continue
                    published = parse_date(item.get("posted_at"))
                    funnel.published(who, published)
                    if not earliest <= published <= now:
                        funnel.add('out_of_window', who)
                        continue
                    url = wechat_url(item.get("url", ""))
                    title = str(item.get("title") or "").strip()
                    if not title:
                        raise ValueError("标题缺失")
                    article_id = db.save_article({"source_key": digest(url), "account_name": account["name"],
                        "title": title, "url": url, "published_at": published.isoformat()})
                    if db.rows('SELECT cleared FROM articles WHERE id=?', (article_id,))[0]['cleared']:
                        if not config.get('allow_recollect_cleared', False):
                            funnel.add('cleared_blocked', who)
                            continue
                        recollect.add(article_id)
                    reads_status = self._save_metrics(job['id'], article_id, item)
                    reads, likes = count(item.get('read_count')), count(item.get('like_count'))
                    if article_id in candidates:
                        funnel.add('repeated', who)
                    else:
                        funnel.add('candidates', who)
                        funnel.metric(who, reads_status, reads)
                        owner[article_id] = who
                    candidates[article_id] = {'published': published, 'reads': reads, 'likes': likes}
                except (ValueError, OverflowError, OSError):
                    funnel.add('invalid', who)
                    collect_warn(f"{account['name']}：一条文章的标题、URL 或发布时间无效，已跳过")
            funnel.save()
        if not successes:
            raise TaskError("所有公众号采集失败；" + (ctx.warnings[0] if ctx.warnings else "请检查登录或接口配置"),
                            ErrorCode.COLLECT)
        # Articles already used in a saved draft are not consumed again.
        used = self._used_articles()
        used.difference_update(recollect)
        separate = config.get('writing_mode') == 'separate_web'
        mode = 'hot' if separate else config.get('selection_mode', 'recent')
        eligible = rank(candidates, mode, config.get('min_reads', 0))
        available = [i for i in eligible if i not in used]
        # Independent writing keeps the full ranked queue so a failed top
        # article can be replaced by the next one without recollecting.
        selected = available if separate else available[:config['article_limit']]
        layers = (set(eligible), set(available), set(selected))
        for article_id, who in owner.items():
            funnel.data['owners'][str(article_id)] = who
            if article_id not in layers[0]:
                funnel.add('below_threshold', who)
            elif article_id not in layers[1]:
                funnel.add('used', who)
            elif article_id not in layers[2]:
                funnel.add('over_limit', who)
            else:
                funnel.add('queued', who)
        funnel.save()
        if config.get('selection_mode') == 'hot' and candidates and not eligible:
            raise TaskError('没有达到阅读量门槛的文章，或接口未提供阅读量；请查看文章库，调整门槛或时间范围',
                            ErrorCode.CONFIG)
        db.update_job(job["id"], selected=json.dumps(selected))
        return selected

    @staticmethod
    def _coverage(source, items, limit):
        """Coverage of one successful listing as reported by the source; never inferred as complete."""
        reported = getattr(source, 'last_coverage', None)
        coverage = {k: v for k, v in reported.items() if k in ('mode', 'end', 'reason')} \
            if isinstance(reported, dict) else {}
        coverage['mode'] = coverage.get('mode') or 'list'
        if coverage.get('reason'):
            coverage['reason'] = redact(coverage['reason'])[:200]
        coverage['limit_reached'] = len(items) >= limit
        return coverage

    def _save_metrics(self, job_id, article_id, item):
        """Store this job's exact observation and its provenance; return the read-count status.

        job_metrics always holds what this job observed (unknown stays NULL, never 0).
        article_metrics is the library's latest *measured* value of each count, decided per
        field: a valid read or like count (0 included) replaces the stored one, a missing or
        invalid one keeps it. article_metric_times records when each kept value was observed;
        article_metrics.observed_at is the last time any count was updated. A changed earlier
        read count is recorded in metric_notes instead of vanishing."""
        db = self.db
        raw_reads, raw_likes = item.get('read_count'), item.get('like_count')
        reads, likes = count(raw_reads), count(raw_likes)
        reads_status, likes_status = metric_status(raw_reads, reads), metric_status(raw_likes, likes)
        observed = stamp()
        previous = db.rows('''SELECT m.reads, m.likes, m.observed_at, t.reads_observed_at, t.likes_observed_at
            FROM article_metrics m LEFT JOIN article_metric_times t ON t.article_id=m.article_id
            WHERE m.article_id=?''', (article_id,))
        prev = previous[0] if previous else {}
        # Rows written before per-field times existed: their only known time is the row time.
        kept = {field: (prev.get(field), prev.get(field + '_observed_at') or prev.get('observed_at') or '')
                for field in ('reads', 'likes')}
        merged = {field: (value, observed) if value is not None else kept[field]
                  for field, value in (('reads', reads), ('likes', likes))}
        if not prev or reads is not None or likes is not None:
            db.execute('INSERT OR REPLACE INTO article_metrics VALUES(?,?,?,?)',
                       (article_id, merged['reads'][0], merged['likes'][0], observed))
            db.execute('INSERT OR REPLACE INTO article_metric_times VALUES(?,?,?)',
                       (article_id, *(at if value is not None else '' for value, at in merged.values())))
        db.execute('INSERT OR REPLACE INTO job_metrics VALUES(?,?,?,?,?)', (job_id, article_id, reads, likes, observed))
        changed = kept['reads'][0] is not None and kept['reads'][0] != reads
        db.execute('''INSERT OR REPLACE INTO metric_notes(job_id,article_id,source,reads_status,likes_status,
                      previous_reads,previous_observed_at) VALUES(?,?,?,?,?,?,?)''',
                   (job_id, article_id, str(item.get('metric_source') or ''), reads_status, likes_status,
                    kept['reads'][0] if changed else None, kept['reads'][1] if changed else ''))
        return reads_status

    def _used_articles(self, exclude_jobs=()):
        used = set()
        for draft in self.db.rows("SELECT job_id,sources FROM drafts"):
            if draft['job_id'] in exclude_jobs:
                continue
            used.update(s["article_id"] for s in json.loads(draft["sources"]) if 'article_id' in s)
        return used

    # ------------------------------------------------------------ materials

    def _materials(self, ctx, selected):
        """Extract body text (and images if allowed) and summarise each article."""
        config, db = ctx.config, self.db
        sources, hashes = [], set()
        for number, article_id in enumerate(selected, 1):
            self.check()
            article = db.rows("SELECT * FROM articles WHERE id=?", (article_id,))[0]
            if article['cleared'] and config.get('allow_recollect_cleared', False):
                db.execute('UPDATE articles SET cleared=0 WHERE id=?', (article_id,))
                article['cleared'] = 0
            if article['cleared']:
                ctx.funnel.material('material_cleared', article)
                ctx.warn(f"{article['title']}：原素材已清理，无法继续处理")
                continue
            self.notify(f"处理素材 {number}/{len(selected)}：{article['title']}")
            ctx.stage("正文与摘要")
            body_ready = False
            try:
                content, image_analysis = self._article_text(ctx, article)
                content_hash = digest(content)
                db.execute("UPDATE articles SET content=?,content_hash=?,error='' WHERE id=?",
                           (content, content_hash, article_id))
                if content_hash in hashes:
                    ctx.funnel.material('duplicate_body', article)
                    ctx.warn(f"{article['title']}：正文与其他素材相同，已去重")
                    continue
                hashes.add(content_hash)
                body_ready = True
                ctx.stage('摘要分析')
                parts = self._summaries(ctx, article, content)
                metrics = db.rows('''SELECT m.reads,m.likes,m.observed_at,n.source,n.reads_status,n.previous_reads,
                    n.previous_observed_at FROM job_metrics m LEFT JOIN metric_notes n
                    ON n.job_id=m.job_id AND n.article_id=m.article_id WHERE m.job_id=? AND m.article_id=?''',
                                  (ctx.job['id'], article_id))
                sources.append({"source_id": f"S{article_id}", "article_id": article_id, "title": article["title"],
                                "url": article["url"], "account_name": article["account_name"],
                                "published_at": article["published_at"], "analysis": parts,
                                "metrics": metrics[0] if metrics else {},
                                "image_analysis": image_analysis})
                ctx.funnel.material('usable', article)
            except ApiError as exc:
                if body_ready:
                    message = redact('摘要分析失败（正文已保存）：' + str(exc))
                    ctx.funnel.material('summary_failed', article)
                    db.set_article_error(article_id, message)
                    ctx.warn(message)
                    raise TaskError(message, error_code(exc)) from None
                if exc.fatal:
                    raise
                ctx.funnel.material('image_only' if exc.kind == 'image_only' else 'body_failed', article)
                db.set_article_error(article_id, str(exc))
                ctx.warn(f"{article['title']}：{exc}")
        return sources

    def _article_text(self, ctx, article):
        content = article["content"] or ctx.source.content(article["url"])
        image_analysis = '【图片识别资料（AI 转写' in content
        if len(content) >= 100:
            return content, image_analysis
        if ctx.config.get('image_articles', 'skip') != 'vision':
            raise ApiError('正文以图片为主（文字不足 100 字），已按设置跳过；'
                           '如需识别图片内容，可在设置中开启“识别图片型文章”', kind='image_only')
        return self._read_images(ctx, article, content), True

    def _read_images(self, ctx, article, content):
        article_id = article['id']
        images = self.db.article_images(article_id)
        if not images:
            raise ApiError('正文不足 100 字且未取得原文图片，无法可靠总结')
        image_root = self.db.path.parent.resolve()
        paths, fingerprints = [], []
        for image in images:
            path = (self.db.path.parent / image['local_path']).resolve()
            if not path.is_relative_to(image_root) or not path.is_file():
                continue
            paths.append(path)
            fingerprints.append([image['source_url'], path.stat().st_size])
        if not paths:
            raise ApiError('正文文字不足，已记录图片但本地文件不存在，请补采图片')
        key = digest(json.dumps(['image-reading-v1', ctx.config['providers']['openai'], article['title'],
                                 content, fingerprints], ensure_ascii=False, sort_keys=True))

        def recognise():
            if not self.vault.get('openai'):
                raise ApiError('图片型文章需要 OpenAI 图片识别，请先在设置中保存 OpenAI API Key')
            ctx.stage('图片识别', f"正文以图片为主，正在识别 {min(len(paths), 8)} 张原文图片…")
            try:
                return ctx.model('openai').analyze_article_images(article, paths, content)
            finally:
                ctx.stage('正文与摘要')
        result = ctx.cached(article_id, key, recognise)
        return (content.strip() + '\n\n' + result['content']).strip()

    def _summaries(self, ctx, article, content):
        # Cache each chunk separately so a retry never repeats completed summaries.
        provider = ctx.config["summary_provider"]
        parts = []
        for start in range(0, len(content), 24000):
            self.check()
            chunk = content[start:start + 24000]
            key = digest(json.dumps(["summary-v1", provider, ctx.config["providers"][provider], chunk],
                                    ensure_ascii=False, sort_keys=True))
            parts.append(ctx.cached(article['id'], key,
                                    lambda: self._summary_attempts(ctx, article, chunk, start // 24000 + 1)))
        return parts

    def _summary_attempts(self, ctx, article, chunk, part):
        """Retry only failed network calls; cache remains owned by the chunk caller."""
        transport = ctx.transport
        previous = getattr(transport, 'retry_attempts', 3)
        transport.retry_attempts = 1  # Avoid multiplying Transport and stage retries.
        provider = ctx.config['summary_provider']
        model = redact(str(ctx.config['providers'][provider]['model']))
        try:
            for attempt in range(1, 4):
                self.check()
                started = time.monotonic()
                try:
                    result = ctx.summarizer.summary(article, chunk)
                    if attempt > 1:
                        logger.info('摘要重试成功 job=%s article=%s part=%s attempt=%s',
                                    ctx.job['id'], article['id'], part, attempt)
                    return result
                except ApiError as exc:
                    message = redact(f'摘要模型 {provider}/{model}，分段 {part}，尝试 {attempt}/3，'
                                     f'耗时 {time.monotonic() - started:.1f} 秒：{exc}')
                    logger.warning('%s', message)
                    retry_delay = getattr(exc, 'retry_delay', None)
                    transient_http = retry_delay is not None and retry_delay <= 30
                    if exc.fatal or (exc.kind != 'network' and not transient_http) or attempt == 3:
                        raise ApiError(message, exc.fatal, exc.kind) from None
                    delay = max(2 ** attempt, retry_delay or 0)
                    ctx.stage('摘要重试', f'{message}；{delay} 秒后重试，可能产生额外费用')
                    if self.cancel.wait(delay):
                        raise Cancelled('任务已停止')
        finally:
            transport.retry_attempts = previous

    # --------------------------------------------------- enrichment stages

    def _style(self, ctx, reference):
        ctx.stage('热点风格分析', '正在分析最高阅读量有效素材的写作风格…')
        provider = ctx.config['summary_provider']
        content = self.db.rows('SELECT content FROM articles WHERE id=?',
                               (reference['article_id'],))[0]['content'][:24000]
        key = digest(json.dumps(['style-v2', provider, ctx.config['providers'][provider],
                                 reference['title'], content], sort_keys=True))
        reference['style_reference'] = ctx.cached(
            reference['article_id'], key, lambda: ctx.summarizer.analyze_style(content, reference['title']))

    def _comments(self, ctx, sources):
        provider = ctx.config['summary_provider']
        for reference in sources:
            self.check()
            ctx.stage('评论分析', '正在通过 EveryInfra 获取评论并分析读者关注点…')
            aid = reference['article_id']
            try:
                comments = ctx.cached(aid, 'comments-v1:' + str(ctx.job['id']), lambda: EveryInfra(
                    self.vault.get('everyinfra'), ctx.transport).comments(reference['url'],
                                                                          ctx.config['comment_limit']))
                key = digest(json.dumps(['comments-analysis-v1', provider, ctx.config['providers'][provider],
                                         comments], sort_keys=True))
                analysis = ctx.cached(aid, key, lambda: (ctx.summarizer.analyze_comments(comments)
                                                         if comments else {'insights': []}))
                reference['comment_analysis'] = {'status': '已分析' if comments else '接口未返回可见评论',
                                                 'sample_count': len(comments), **analysis}
            except (ApiError, ValueError) as exc:
                reference['comment_analysis'] = {'status': '获取或分析失败', 'reason': redact(str(exc))}
                ctx.warn('评论分析未完成：' + str(exc))

    def _research(self, ctx, sources):
        """Return (research, degraded). Only connection failures degrade."""
        ctx.stage('联网研究', '正在联网补充该主题的信息并核对来源…')
        job_id = ctx.job['id']
        cached = self.db.rows('SELECT result FROM research_cache WHERE job_id=?', (job_id,))
        if cached and json.loads(cached[0]['result']).get('provider') != 'openai':
            cached = []
        try:
            if cached:
                research = json.loads(cached[0]['result'])
            else:
                research = self.research_factory(self.vault.get('openai'), ctx.config,
                                                 ctx.transport).search(sources[0])
                research['provider'] = 'openai'
                self.db.execute('INSERT OR REPLACE INTO research_cache VALUES(?,?)',
                                (job_id, json.dumps(research, ensure_ascii=False)))
            sources[0]['web_research'] = research['notes']
            return research, False
        except ApiError as exc:
            if exc.kind != 'network':
                raise
            ctx.config['research_degraded'] = True
            sources[0]['web_research'] = RESEARCH_DEGRADED_MARK + '或超过 300 秒，已降级为仅基于公众号原文写作。'
            ctx.warn('联网搜索连接失败或超时，已降级为仅基于现有公众号内容生成；本稿未经联网补充核实')
            return None, True

    def _compose(self, ctx, writing_sources):
        ctx.stage("综合写作", "正在综合材料、组织大纲并生成正文…")
        writer = ctx.writer
        output = writer.write(writing_sources, ctx.config)
        quality_notes = []
        review = getattr(writer, 'review', None)
        if ctx.config.get('quality_review_enabled', True) and callable(review):
            self.check()
            ctx.stage('质量二审', '正在对照证据复核事实边界、结构和表达…')
            try:
                output = review(output, writing_sources, ctx.config)
                quality_notes = output.pop('quality_notes', [])
                ctx.review_state = 'done'
            except ApiError as exc:
                ctx.review_state = 'failed'
                message = 'AI 二审未完成，已保留通过基础校验的初稿：' + redact(str(exc))
                output.setdefault('review_notes', []).append(message)
                ctx.warn(message)
        return output, quality_notes

    # ---------------------------------------------------------- write one

    def _write_one(self, ctx, selected):
        config, job = ctx.config, ctx.job
        sources = self._materials(ctx, selected)
        if len(sources) < config["min_sources"]:
            if config.get('writing_mode') == 'single_web' and ctx.warnings:
                raise TaskError("素材处理未完成：" + ctx.warnings[-1], ErrorCode.MATERIAL)
            detail = "最近错误：" + ctx.warnings[-1] if ctx.warnings else "请增加时间范围或调整最少素材数"
            raise TaskError(f"素材处理未完成：通过校验 {len(sources)} 篇，需要 {config['min_sources']} 篇；{detail}",
                            ErrorCode.MATERIAL)
        if len(json.dumps(sources, ensure_ascii=False)) > 180000:
            raise TaskError("写作素材过长，请减少单次文章数量", ErrorCode.CONFIG)
        self.check()
        if config.get('selection_mode') == 'hot' or config.get('writing_mode') == 'single_web':
            self._style(ctx, sources[0])
        # EveryInfra comment analysis was withdrawn in T-010: old snapshots with
        # comments_enabled=True (retries, schedules) must not fetch comments.
        research, degraded = (self._research(ctx, sources) if config.get('writing_mode') == 'single_web'
                              else (None, False))
        output, quality_notes = self._compose(ctx, sources + (research['sources'] if research else []))
        self.check()
        report = self._report(config, sources, output, quality_notes, research, degraded)
        if research:
            sources += research['sources']
        self.db.save_draft(job["id"], output["title"], output["body"], sources, report)
        ctx.funnel.data['drafts'] = 1
        ctx.funnel.data['quality'] = {
            'research': 'degraded' if degraded else ('done' if research else 'not_used'),
            'review': ctx.review_state, 'warnings': len(ctx.warnings)}
        ctx.funnel.save()
        status = "部分完成" if ctx.warnings else "已完成"
        self.db.update_job(job["id"], stage="完成", status=status, error_code='',
                           message=f"已生成 1 篇草稿，使用 {len(sources)} 篇素材，请人工审核")
        self.notify("稿件已保存，可在稿件工作台查看与编辑")
        return status

    @staticmethod
    def _report(config, sources, output, quality_notes, research, degraded):
        report = "# 每日素材报告\n\n"
        if config.get('selection_mode') == 'hot':
            report += ('选文规则：时间窗内按接口返回阅读量降序，点赞量及发布时间作为同阅读量时的排序依据。'
                       '缺失阅读量不参与热点排名。仅代表已管理公众号及本轮查询范围。\n\n')
            report += (f"风格参考：{sources[0]['title']}\n\n"
                       + json.dumps(sources[0]['style_reference'], ensure_ascii=False, indent=2) + '\n\n')
        for s in sources:
            report += f"## [{s['source_id']}] {s['title']}\n公众号：{s['account_name']}\n原文：{s['url']}\n\n"
            report += Engine._metrics_line(s.get('metrics') or {}) + '\n\n'
            if 'comment_analysis' in s:
                report += ('评论样本分析（EveryInfra 提供评论，所选摘要模型分析；不代表总体民意）：\n'
                           + json.dumps(s['comment_analysis'], ensure_ascii=False, indent=2) + '\n\n')
            if s.get('image_analysis'):
                report += ('素材说明：原文正文以图片为主，已使用 OpenAI 识别图片内容；'
                           '转写可能有误，价格、日期、功效及专有名词需人工对照原图。\n\n')
            for part in s["analysis"]:
                report += part["summary"] + "\n\n"
                for fact in part["facts"]:
                    report += f"- {fact['claim']}\n  原文证据：{fact['quote']}\n"
                for uncertainty in part["uncertainties"]:
                    report += f"- 待核实：{uncertainty}\n"
            report += "\n"
        report += "## 写作大纲\n" + "\n".join("- " + x for x in output["outline"])
        report += "\n\n## 审核提示\n" + "\n".join("- " + x for x in output["review_notes"])
        if quality_notes:
            report += "\n\n## AI 二审\n" + "\n".join("- " + x for x in quality_notes)
        report += "\n\n来源标记与引用片段已做格式校验，事实及文章观点仍需人工审核。\n"
        if degraded:
            report += '\n## 联网研究状态\n联网搜索连接失败或超过 300 秒，本稿已降级为仅基于公众号原文生成，未使用联网补充资料。\n'
        if research:
            report += '\n## 联网补充资料\n检索时间：' + research['retrieved_at'] + '\n\n' + research['notes'] + '\n\n'
            report += '\n'.join(f"[{s['source_id']}] {s['title']} — {s['url']}" for s in research['sources'])
        return report

    @staticmethod
    def _metrics_line(metrics):
        def shown(value, status):
            if value is not None:
                return str(value)
            return '无效' if status == 'invalid' else '未知'
        reads = metrics.get('reads')
        line = f"阅读量：{shown(reads, metrics.get('reads_status'))}"
        if reads is None:
            line += '（接口未提供有效值，不参与热点排名）'
        if metrics.get('previous_reads') is not None:
            line += (f"（此前 {china_time(metrics.get('previous_observed_at'))} 采集为 {metrics['previous_reads']}"
                     + ('，本次以接口最新返回值为准）' if reads is not None else '；本稿使用本次观察值，文章库保留该有效值）'))
        likes = metrics.get('likes')
        line += f"；点赞量：{likes if likes is not None else '未知'}"
        source = metrics.get('source')
        line += '；指标来源：' + (METRIC_SOURCES.get(source, source) if source else '采集来源未标注')
        observed = metrics.get('observed_at')
        line += '；数据采集时间：' + (china_time(observed) if observed else '未知')
        return line

    # -------------------------------------------------------------- batches

    def refresh_parent(self, child_job_id):
        """Recount a batch after one of its articles was retried on its own."""
        rows = self.db.rows('SELECT parent_job_id FROM batch_items WHERE child_job_id=?', (child_job_id,))
        if not rows:
            return
        parent_id = rows[0]['parent_job_id']
        parent = self.db.rows('SELECT config,status FROM jobs WHERE id=?', (parent_id,))
        if not parent or parent[0]['status'] in ('运行中', '已清理'):
            return
        target = json.loads(parent[0]['config']).get('draft_count', 3)
        summary = self._batch_summary(parent_id, target, retried=True)
        self.db.update_job(parent_id, status=summary['status'], error_code='', message=summary['message'],
                           warnings=json.dumps(summary['warnings'], ensure_ascii=False))
        self._fold_children(parent_id, summary['done'], summary['quality'])

    def _batch_summary(self, parent_id, target, extra='', retried=False):
        """Recompute a batch's status, message and warnings from the latest state of every child.

        Built from stored rows each time, so retries replace rather than accumulate warnings.
        The draft count and quality findings are reported separately."""
        parent = self.db.rows('SELECT selected, funnel FROM jobs WHERE id=?', (parent_id,))[0]
        order = {aid: n for n, aid in enumerate(json.loads(parent['selected'] or '[]'), 1)}
        children = self.db.rows('''SELECT b.article_id, j.status, j.message, j.warnings, j.funnel,
            (SELECT title FROM drafts d WHERE d.job_id=j.id) draft_title,
            (SELECT title FROM articles a WHERE a.id=b.article_id) article_title
            FROM batch_items b JOIN jobs j ON j.id=b.child_job_id WHERE b.parent_job_id=?''', (parent_id,))
        children.sort(key=lambda c: order.get(c['article_id'], len(order) + 1))
        parent_funnel = load_funnel(parent['funnel']) or {}
        warnings = ['采集：' + w for w in Funnel(self.db, parent_id, parent_funnel).collection_warnings()]
        done = degraded = flagged = 0
        for child in children:
            label = f"热度第 {order[child['article_id']]} 篇" if child['article_id'] in order else '单篇'
            if not child['draft_title']:
                warnings.append(f"未完成：{label}：{child['message']}")
                continue
            done += 1
            notes = json.loads(child['warnings'] or '[]')
            quality = (load_funnel(child['funnel']) or {}).get('quality') or {}
            is_degraded = (quality.get('research') == 'degraded' if quality
                           else any(RESEARCH_DEGRADED_MARK in str(n) for n in notes))
            name = f"{label}《{child['article_title'] or child['draft_title']}》"
            if is_degraded:
                degraded += 1
                warnings.append(f'联网降级：{name}联网搜索失败，仅基于公众号原文写作，未经联网补充核实')
            others = [n for n in notes if RESEARCH_DEGRADED_MARK not in str(n)]
            if others:
                flagged += 1
                warnings += [f'质量提示：{name}：{n}' for n in others]
        status = '已完成' if done >= target else ('部分完成' if done else '失败')
        message = f'已生成 {min(done, target)}/{target} 篇独立主题文章'
        if retried:
            message += '（含单篇重试结果）'
        if done and not degraded:
            message += '，每篇分别联网研究和写作'
        if degraded:
            message += f'；其中 {degraded} 篇联网研究失败，已降级为仅基于公众号原文写作，未经联网核实'
        if flagged:
            message += f'；{flagged} 篇有处理或质量提示'
        collection_notes = sum(1 for w in warnings if w.startswith('采集：'))
        if collection_notes:
            message += f'；采集阶段有 {collection_notes} 条提示'
        if done and (degraded or flagged):
            message = '【需复核】' + message
        return {'status': status, 'message': message + extra, 'warnings': warnings, 'done': done,
                'quality': {'drafts': done, 'research_degraded': degraded, 'flagged': flagged}}

    def _fold_children(self, parent_id, drafts, quality=None):
        """Batch funnel: material layers are the sum of each child article's latest attempt."""
        rows = self.db.rows('SELECT funnel FROM jobs WHERE id=?', (parent_id,))
        data = load_funnel(rows[0]['funnel']) if rows else None
        if data is None:
            return
        for key in MATERIAL_KEYS:
            data['counts'][key] = 0
        for account in data['accounts'].values():
            for key in MATERIAL_KEYS:
                account['counts'].pop(key, None)
        data['outcomes'] = {}
        funnel = Funnel(self.db, parent_id, data)
        for row in self.db.rows("""SELECT j.funnel FROM jobs j JOIN batch_items b ON b.child_job_id=j.id
                                   WHERE b.parent_job_id=?""", (parent_id,)):
            child = load_funnel(row['funnel'])
            for article_id, outcome in (child or {}).get('outcomes', {}).items():
                article = self.db.rows('SELECT id, account_name FROM articles WHERE id=?', (int(article_id),))
                if article and outcome in MATERIAL_KEYS:
                    funnel.add(outcome, funnel.article_account(article[0]))
                    data['outcomes'][article_id] = outcome
        data['drafts'] = drafts
        if quality is not None:
            data['quality'] = quality
        funnel.save()

    def _batch_queue(self, job, config, selected):
        """Ranked article queue: the stored choices first, then ranked backups."""
        selected = list(dict.fromkeys(selected))
        # v0.9.6 and earlier stored only the first N choices; rebuild backups
        # from the parent's captured metrics so a retry can still use them.
        candidates = {}
        for row in self.db.rows('''SELECT m.article_id,m.reads,m.likes,a.published_at,a.title
                FROM job_metrics m JOIN articles a ON a.id=m.article_id WHERE m.job_id=?''', (job['id'],)):
            try:
                candidates[row['article_id']] = {'reads': row['reads'], 'likes': row['likes'],
                                                  'published': parse_date(row['published_at'])}
            except (ValueError, TypeError, OverflowError, OSError):
                continue
        child_ids = {row['child_job_id'] for row in self.db.rows(
            'SELECT child_job_id FROM batch_items WHERE parent_job_id=?', (job['id'],))}
        used_elsewhere = self._used_articles(exclude_jobs=child_ids)
        for article_id in rank(candidates, 'hot', config.get('min_reads', 0)):
            if article_id not in selected and article_id not in used_elsewhere:
                selected.append(article_id)
        self.db.update_job(job['id'], selected=json.dumps(selected))
        return selected

    def _run_child(self, job, config, article_id, titles, source):
        child_config = deepcopy(config)
        child_config.update(writing_mode='single_web', selection_mode='hot', min_sources=1,
                            article_limit=1, topic='', avoid_titles=list(titles))
        child = self.db.job(f"batch:{job['id']}:{article_id}", child_config)
        self.db.execute('INSERT OR IGNORE INTO batch_items VALUES(?,?,?)', (job['id'], article_id, child['id']))
        self.db.update_job(child['id'], selected=json.dumps([article_id]))
        for metric in self.db.rows('SELECT * FROM job_metrics WHERE job_id=? AND article_id=?',
                                   (job['id'], article_id)):
            self.db.execute('INSERT OR IGNORE INTO job_metrics VALUES(?,?,?,?,?)',
                            (child['id'], article_id, metric['reads'], metric['likes'], metric['observed_at']))
        self.db.execute('''INSERT OR IGNORE INTO metric_notes SELECT ?,article_id,source,reads_status,likes_status,
                           previous_reads,previous_observed_at FROM metric_notes WHERE job_id=? AND article_id=?''',
                        (child['id'], job['id'], article_id))
        runner = Engine(self.db, self.vault, self.notify, self.source_factory, self.model_factory,
                        self.research_factory, self.profile_dir)
        runner.cancel = self.cancel
        try:
            runner.run(child['run_key'], child_config, reset_cancel=False, shared_source=source)
        except Exception:
            # One article's unexpected failure must not abort the batch.
            logger.exception("单篇任务 %s 异常，继续处理其余文章", child['run_key'])
        return self.db.rows('SELECT * FROM jobs WHERE id=?', (child['id'],))[0]

    def run_separate(self, job, config, selected, source=None):
        # Each output owns a child job: a retry never rewrites completed outputs.
        target = config.get('draft_count', 3)
        selected = self._batch_queue(job, config, selected)
        primary_count = min(target, len(selected))
        done, titles, replacement_slots, replacements = 0, [], 0, []
        last_code = ErrorCode.MATERIAL if not selected else ''
        for rank_position, article_id in enumerate(selected, 1):
            is_replacement = rank_position > primary_count
            if is_replacement and (done >= target or replacement_slots <= 0):
                break
            self.check()
            label = f'热度第 {rank_position} 篇'
            self.db.update_job(job['id'], stage=(f'补位写作：{label}' if is_replacement else
                                                 f'逐篇写作 {rank_position}/{primary_count}'))
            child = self._run_child(job, config, article_id, titles, source)
            self.check()
            drafts = self.db.rows('SELECT title FROM drafts WHERE job_id=?', (child['id'],))
            if drafts:
                done += 1
                titles.append(drafts[0]['title'])
                if is_replacement:
                    replacement_slots -= 1
                    replacements.append(label)
                continue
            # Old (pre-0.10.1) child rows have no code; fall back to their message.
            replaceable = (child.get('error_code') == ErrorCode.MATERIAL or (
                not child.get('error_code') and child['stage'] == '正文与摘要'
                and child['message'].startswith('素材处理未完成：')))
            last_code = child.get('error_code') or ErrorCode.API
            if replaceable and not is_replacement:
                replacement_slots += 1
            elif not replaceable and is_replacement:
                # A shared failure (model, key, research) would hit every
                # replacement too; do not walk the whole queue.
                replacement_slots -= 1
        extra = ''
        if replacements:
            extra += f'；{len(replacements)} 篇素材提取失败，已由' + '、'.join(replacements) + '补位'
        if len(selected) < target or (done < target and replacement_slots > 0):
            extra += '；符合条件的新素材不足，可在运行记录中“按当前配置新建采集”或扩大时间范围'
            accounts = (load_funnel(self.db.rows('SELECT funnel FROM jobs WHERE id=?', (job['id'],))[0]['funnel'])
                        or {}).get('accounts', {})
            if any((a.get('coverage') or {}).get('mode') == 'latest_only' for a in accounts.values()):
                extra += '（降级为仅最新一篇的公众号不会因扩大时间范围恢复历史覆盖）'
        summary = self._batch_summary(job['id'], target, extra)
        self.db.update_job(job['id'], status=summary['status'], stage='完成', message=summary['message'],
                           error_code='' if summary['done'] else last_code,
                           warnings=json.dumps(summary['warnings'], ensure_ascii=False))
        self._fold_children(job['id'], summary['done'], summary['quality'])
        self.notify(summary['message'])
        return summary['status']
