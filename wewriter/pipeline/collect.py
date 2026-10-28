"""Collection stage: list each enabled account, filter by time window, rank, and
record the collection funnel with a reason for every skipped article."""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from ..api import ApiError, Dajiala, EveryInfra, digest, parse_date, wechat_url
from ..db import stamp
from ..hotspots import count, rank
from ..logs import redact
from .common import CHINA_TZ, ErrorCode, TaskError, _published_at, metric_status

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
        previous = load_funnel(job.get('funnel'))
        # Usage belongs to the lifetime of a job, unlike the attempt-scoped funnel.
        if previous and isinstance(previous.get('cost_events'), list):
            data['cost_events'] = deepcopy(previous['cost_events'])
        if reuse_collection and previous and previous.get('collection'):
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


def open_source(engine, transport):
    if engine.source_factory:
        return engine.source_factory(engine.vault.get("everyinfra"), transport)
    from ..weread import WeReadSource
    # EveryInfra supplies the article list and read counts when its key is
    # saved: the WeRead list is refused account-wide (-2041) for long periods.
    provider = (EveryInfra(engine.vault.get('everyinfra'), transport)
                if engine.vault.has('everyinfra') and engine.vault.get('everyinfra') else None)
    # 极致了 is tried first: its list carries read counts and it answered
    # every request in the 2026-10-25 trial where EveryInfra was mostly busy.
    primary = (Dajiala(engine.vault.get('dajiala'), transport)
               if engine.vault.has('dajiala') and engine.vault.get('dajiala') else None)
    return WeReadSource(engine.vault.get("weread"), engine.db, engine.cancel, provider=provider,
                        primary=primary,
                        state_saver=lambda value: engine.vault.set_many({'weread': value}),
                        profile_dir=engine.profile_dir,
                        skill_key=(engine.vault.get('weread_skill')
                                   if engine.vault.has('weread_skill') else None))


def collect(engine, ctx):
    """List recent articles of every enabled account and rank them."""
    config, job, db = ctx.config, ctx.job, engine.db
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
        engine.check()
        who = 'acct:' + account['identifier']
        funnel.account(who, account['name'], account['identifier'])
        engine.notify(f"正在同步：{account['name']}")
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
        funnel.account(who)['coverage'] = engine._coverage(ctx.source, items, config["per_account_limit"])
        if len(items) >= config["per_account_limit"]:
            collect_warn(f"{account['name']} 返回条数达到单账号上限，时间窗内可能仍有未覆盖文章")
        for item in items:
            engine.check()
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
                reads_status = engine._save_metrics(job['id'], article_id, item)
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
    used = engine._used_articles()
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


def saved_candidates(db, config):
    """Rank unconsumed, retained articles already in the local library.

    Listing an account is deliberately absent here. Measured read counts rank first.
    When the user set no minimum, older articles with unknown counts remain usable
    after measured articles, rather than making a populated library look empty.
    """
    names = {account['name'] for account in db.accounts(enabled=True)}
    if not names:
        return []
    used = used_articles(db)
    candidates = {}
    for row in db.rows('''SELECT a.id,a.account_name,a.published_at,m.reads,m.likes
            FROM articles a LEFT JOIN article_metrics m ON m.article_id=a.id
            WHERE a.cleared=0'''):
        if row['account_name'] not in names or row['id'] in used:
            continue
        try:
            published = parse_date(row['published_at'])
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        candidates[row['id']] = {'published': published, 'reads': row['reads'], 'likes': row['likes']}
    mode, minimum = config.get('selection_mode', 'hot'), config.get('min_reads', 0)
    ordered = rank(candidates, mode, minimum)
    if mode == 'hot' and minimum == 0:
        ordered += sorted((i for i, item in candidates.items() if item['reads'] is None),
                          key=lambda i: candidates[i]['published'], reverse=True)
    return ordered


def select_saved(engine, ctx):
    """Freeze a writing queue from saved metadata without calling a list API."""
    db, job = engine.db, ctx.job
    selected = saved_candidates(db, ctx.config)
    for article_id in selected:
        metric = db.rows('SELECT reads,likes,observed_at FROM article_metrics WHERE article_id=?', (article_id,))
        if metric:
            row = metric[0]
            db.execute('INSERT OR IGNORE INTO job_metrics VALUES(?,?,?,?,?)',
                       (job['id'], article_id, row['reads'], row['likes'], row['observed_at']))
    db.update_job(job['id'], selected=json.dumps(selected))
    return selected


def coverage(source, items, limit):
    """Coverage of one successful listing as reported by the source; never inferred as complete."""
    reported = getattr(source, 'last_coverage', None)
    coverage = {k: v for k, v in reported.items() if k in ('mode', 'end', 'reason')} \
        if isinstance(reported, dict) else {}
    coverage['mode'] = coverage.get('mode') or 'list'
    if coverage.get('reason'):
        coverage['reason'] = redact(coverage['reason'])[:200]
    coverage['limit_reached'] = len(items) >= limit
    return coverage


def save_metrics(db, job_id, article_id, item):
    """Store this job's exact observation and its provenance; return the read-count status.

    job_metrics always holds what this job observed (unknown stays NULL, never 0).
    article_metrics is the library's latest *measured* value of each count, decided per
    field: a valid read or like count (0 included) replaces the stored one, a missing or
    invalid one keeps it. article_metric_times records when each kept value was observed;
    article_metrics.observed_at is the last time any count was updated. A changed earlier
    read count is recorded in metric_notes instead of vanishing."""
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


def used_articles(db, exclude_jobs=()):
    used = set()
    for draft in db.rows("SELECT job_id,sources FROM drafts"):
        if draft['job_id'] in exclude_jobs:
            continue
        used.update(s["article_id"] for s in json.loads(draft["sources"]) if 'article_id' in s)
    return used
