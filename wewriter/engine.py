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
import threading
import uuid
from copy import deepcopy
# Original module-level names stay importable (tests patch e.g. wewriter.engine.EveryInfra.comments).
from .api import ApiError, Cancelled, Dajiala, EveryInfra, Model, Transport, digest, parse_date, wechat_url  # noqa: F401
from .config import validate, migrate
from .db import stamp
from .hotspots import count, rank  # noqa: F401
from .logs import logger, redact
from .costs import CostMeter
from .pipeline import collect, composition, materials, research
# Re-exported so ``from wewriter.engine import ...`` keeps working after the split.
from .pipeline.collect import (COLLECT_KEYS, COVERAGE_ENDS, FUNNEL_LABELS, MATERIAL_KEYS, METRIC_SOURCES,  # noqa: F401
                               Funnel, format_coverage, format_funnel, format_quality, load_funnel, window_start)
from .pipeline.common import (CHINA_TZ, ErrorCode, TaskError, _published_at, china_time,  # noqa: F401
                              error_code, metric_status)
from .pipeline.research import RESEARCH_DEGRADED_MARK


FINISHED = ("已完成", "无新文章", "已清理")


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
        provider = self.config['summary_provider']
        model = self.config.get('summary_model') or self.config['providers'][provider]['model']
        if model == self.config['providers'][provider]['model']:
            return self.model(provider)
        if '_summary' not in self._models:
            settings = {**self.config['providers'][provider], 'model': model}
            self._models['_summary'] = self.engine.model_factory(
                provider, settings, self.engine.vault.get(provider), self.transport)
        return self._models['_summary']

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
            # A user may raise the paid-request cap in Settings and resume the
            # original job, keeping its already-paid stage checkpoints.
            current_cap = self.db.config().get('max_paid_requests')
            old_cap = config.get('max_paid_requests')
            if old_cap is not None and (current_cap is None or current_cap > old_cap):
                config['max_paid_requests'] = current_cap
            if self.db.rows("SELECT id FROM drafts WHERE job_id=?", (job["id"],)):
                self.db.update_job(job["id"], status="已完成", message="已有稿件，未重复生成", error_code='')
                return "已完成"
            # A retry is a new attempt: previous warnings stay in history only.
            self.db.update_job(job["id"], status="运行中", message="正在准备任务", warnings="[]", error_code='')
            # Likewise the funnel describes this attempt only and is rebuilt, never summed.
            funnel = Funnel.for_attempt(self.db, job, reuse_collection=bool(json.loads(job["selected"])))
            transport = Transport(self.cancel)
            transport.cost_meter = CostMeter(funnel, config.get('max_paid_requests'))
            if source is None:
                source = self._open_source(transport)
            ctx = _Run(self, job, config, transport, source)
            ctx.funnel = funnel
            # Collection notes of a reused selection (coverage, list limits) remain valid and
            # are carried once; warnings of the earlier material/writing attempt are not.
            ctx.warnings = funnel.collection_warnings()
            if ctx.warnings:
                self.db.update_job(job["id"], warnings=json.dumps(ctx.warnings, ensure_ascii=False))
            selected = json.loads(job["selected"])
            action = config.get('task_action')
            if action == 'collect':
                if not selected:
                    selected = self._collect(ctx)
                funnel_data = ctx.funnel.data
                found = (funnel_data.get('counts') or {}).get('candidates', 0)
                status = '部分完成' if ctx.warnings else '已完成' if found else '无新文章'
                message = f'已采集并保存 {found} 篇文章信息；其中 {len(selected)} 篇未写，可在“生成文章”中使用。'
                if not found:
                    message = '本次没有采集到时间范围内的文章；已保存的旧素材不受影响。'
                self.db.update_job(job['id'], status=status, stage='完成', message=message,
                                   error_code=ErrorCode.COLLECT if ctx.warnings else '')
                self.notify(message)
                return status
            # Legacy combined tasks still check keys before paid collection. A library-only
            # task checks its local selection first, then resolves keys before body fetching.
            if action != 'write':
                ctx.summarizer, ctx.writer
            if not selected:
                selected = (collect.select_saved(self, ctx) if action == 'write' else self._collect(ctx))
                if not selected:
                    status = "失败" if ctx.warnings else "无新文章"
                    message = ('素材库没有符合当前阅读量门槛、未写过的文章；请先采集素材或调整门槛' if action == 'write'
                               else "采集存在异常且没有可用新文章，请查看警告后重试" if ctx.warnings
                               else "时间范围内没有未使用的新文章")
                    self.db.update_job(job["id"], status=status, message=message,
                                       error_code=ErrorCode.MATERIAL if action == 'write' else ErrorCode.COLLECT if ctx.warnings else '')
                    return status
            if action == 'write':
                ctx.summarizer, ctx.writer
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
        return composition.article_drafts(self.db, article_id)

    def rewrite_job(self, article_id, config, note=''):
        """A new single-article task from a source that may already have drafts; not started here."""
        return composition.rewrite_job(self, article_id, config, note)

    def rerun_review(self, draft_id):
        """Re-run the AI review of one saved draft; never alongside a running task."""
        if not self.lock.acquire(blocking=False):
            raise ValueError("已有任务运行中，请等它完成后再补跑二审")
        try:
            self.cancel.clear()
            return composition.rerun_review(self, draft_id)
        finally:
            self.lock.release()

    def new_collection(self, config):
        """Explicitly collect again with the current configuration.

        Always a new task (new key, so a new time window ending now); earlier tasks and their
        drafts are untouched, and articles used in saved drafts stay excluded by _collect."""
        return self.run('manual:' + uuid.uuid4().hex, config)

    def _open_source(self, transport):
        return collect.open_source(self, transport)

    # ------------------------------------------------------------ stages
    # Thin entry points kept on Engine so callers and tests can use or patch them;
    # the logic lives in wewriter.pipeline.

    def _collect(self, ctx):
        """List recent articles of every enabled account and rank them."""
        return collect.collect(self, ctx)

    @staticmethod
    def _coverage(source, items, limit):
        return collect.coverage(source, items, limit)

    def _save_metrics(self, job_id, article_id, item):
        return collect.save_metrics(self.db, job_id, article_id, item)

    def _used_articles(self, exclude_jobs=()):
        return collect.used_articles(self.db, exclude_jobs)

    def _materials(self, ctx, selected):
        return materials.materials(self, ctx, selected)

    def _article_text(self, ctx, article):
        return materials.article_text(self, ctx, article)

    def _read_images(self, ctx, article, content):
        return materials.read_images(self, ctx, article, content)

    def _summaries(self, ctx, article, content):
        return materials.summaries(self, ctx, article, content)

    def _summary_attempts(self, ctx, article, chunk, part):
        return materials.summary_attempts(self, ctx, article, chunk, part)

    def _style(self, ctx, reference):
        return composition.style(self, ctx, reference)

    def _comments(self, ctx, sources):
        return research.comments(self, ctx, sources)

    def _research(self, ctx, sources):
        """Return (research, degraded). Only connection failures degrade."""
        return research.research(self, ctx, sources)

    def _compose(self, ctx, writing_sources):
        return composition.compose(self, ctx, writing_sources)

    def _write_one(self, ctx, selected):
        return composition.write_one(self, ctx, selected)

    @staticmethod
    def _report(config, sources, output, quality_notes, research, degraded):
        return composition.report(config, sources, output, quality_notes, research, degraded)

    @staticmethod
    def _metrics_line(metrics):
        return composition.metrics_line(metrics)

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

    # A single article that failed on a timeout or dropped connection may be replaced, but a
    # network outage would fail every replacement too, so at most this many per batch.
    NETWORK_REPLACEMENTS = 2

    def run_separate(self, job, config, selected, source=None):
        # Each output owns a child job: a retry never rewrites completed outputs.
        target = config.get('draft_count', 3)
        selected = self._batch_queue(job, config, selected)
        # Collection is paid, but research and writing are still avoidable here.
        # When the ranked queue cannot reach the requested count, do not spend
        # model calls on a batch that is already guaranteed to be short.
        saved = self.db.rows('''SELECT 1 FROM drafts d JOIN batch_items b ON b.child_job_id=d.job_id
                                WHERE b.parent_job_id=? LIMIT 1''', (job['id'],))
        if len(selected) < target and not saved:
            origin = ('素材库' if config.get('task_action') == 'write' else '采集完成')
            message = (f'{origin}：目标 {target} 篇，只有 {len(selected)} 篇未使用且符合选文条件；'
                       '未启动联网研究或模型写作。'
                       + ('本次未重新采集；请先采集素材、降低篇数或调整阅读量门槛' if config.get('task_action') == 'write'
                          else '采集请求已经发生，可能产生采集费用；请调整时间范围、公众号或篇数后新建采集任务'))
            self.db.update_job(job['id'], status='失败', stage='完成', message=message,
                               error_code=ErrorCode.MATERIAL)
            self.notify(message)
            return '失败'
        primary_count = min(target, len(selected))
        done, titles, replacement_slots = 0, [], 0
        # Failures waiting for a replacement, then (failed label, reason, replacement label) pairs.
        pending, replacements, network_grants = [], [], 0
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
                    replacements.append((*pending.pop(0), label))
                continue
            # Old (pre-0.10.1) child rows have no code; fall back to their message.
            code = child.get('error_code')
            reason = None
            if code == ErrorCode.MATERIAL or (not code and child['stage'] == '正文与摘要'
                                              and child['message'].startswith('素材处理未完成：')):
                reason = '素材提取失败'
            elif code == ErrorCode.NETWORK and network_grants < self.NETWORK_REPLACEMENTS:
                # Timeouts and connection failures only; key, balance, permission and request
                # errors (AUTH / API codes) would hit every replacement and are not replaced.
                network_grants += 1
                reason = '因超时或网络中断失败'
            last_code = code or ErrorCode.API
            if reason and not is_replacement:
                replacement_slots += 1
                pending.append((label, reason))
            elif not reason and is_replacement:
                # A shared failure (model, key, research) would hit every
                # replacement too; do not walk the whole queue.
                replacement_slots -= 1
            # A replacement that failed in a replaceable way leaves its slot to the next article.
        extra = ''
        if replacements:
            extra += ''.join(f'；{failed}{reason}，已由{used}补位' for failed, reason, used in replacements)
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
