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
from copy import deepcopy
from datetime import datetime, timedelta
from .api import ApiError, Cancelled, EveryInfra, Model, Transport, digest, parse_date, wechat_url
from .config import validate, migrate, is_promotion
from .db import stamp
from .hotspots import count, rank
from .logs import logger


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


class _Run:
    """Per-execution state shared by the stages of one job."""

    def __init__(self, engine, job, config, transport, source):
        self.engine, self.job, self.config = engine, job, config
        self.transport, self.source = transport, source
        self.warnings = []
        self._models = {}

    @property
    def db(self):
        return self.engine.db

    def warn(self, message):
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
            transport = Transport(self.cancel)
            if source is None:
                source = self._open_source(transport)
            ctx = _Run(self, job, config, transport, source)
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
                logger.warning("任务 %s 失败 [%s]：%s", run_key, code, exc)
            status = "已停止" if isinstance(exc, Cancelled) else "失败"
            if job:
                self.db.update_job(job["id"], status=status, message=str(exc), error_code=code)
            self.notify(str(exc))
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

    def _open_source(self, transport):
        if self.source_factory:
            return self.source_factory(self.vault.get("everyinfra"), transport)
        from .weread import WeReadSource
        return WeReadSource(self.vault.get("weread"), self.db, self.cancel,
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
        earliest = now - timedelta(hours=config["lookback_hours"])
        candidates, successes, recollect, promotions = {}, 0, set(), 0
        for account in accounts:
            self.check()
            self.notify(f"正在同步：{account['name']}")
            try:
                items = ctx.source.articles(account["identifier"], config["per_account_limit"])
                successes += 1
            except ApiError as exc:
                if exc.fatal:
                    raise
                ctx.warn(f"{account['name']}：{exc}")
                continue
            if len(items) >= config["per_account_limit"]:
                ctx.warn(f"{account['name']} 返回条数达到单账号上限，时间窗内可能仍有未覆盖文章")
            for item in items:
                self.check()
                try:
                    if not isinstance(item, dict):
                        raise ValueError("文章条目格式无效")
                    if item.get('collection_warning'):
                        ctx.warn(f"{account['name']}：{item['collection_warning']}")
                    if item.get("is_deleted") in (True, 1, "true"):
                        continue
                    published = parse_date(item.get("posted_at"))
                    if not earliest <= published <= now:
                        continue
                    url = wechat_url(item.get("url", ""))
                    title = str(item.get("title") or "").strip()
                    if not title:
                        raise ValueError("标题缺失")
                    if is_promotion(title, config):
                        promotions += 1
                        continue
                    article_id = db.save_article({"source_key": digest(url), "account_name": account["name"],
                        "title": title, "url": url, "published_at": published.isoformat()})
                    if db.rows('SELECT cleared FROM articles WHERE id=?', (article_id,))[0]['cleared']:
                        if not config.get('allow_recollect_cleared', False):
                            continue
                        recollect.add(article_id)
                    reads, likes = count(item.get('read_count')), count(item.get('like_count'))
                    observed = stamp()
                    db.execute('INSERT OR REPLACE INTO article_metrics VALUES(?,?,?,?)',
                               (article_id, reads, likes, observed))
                    db.execute('INSERT OR REPLACE INTO job_metrics VALUES(?,?,?,?,?)',
                               (job['id'], article_id, reads, likes, observed))
                    candidates[article_id] = {'published': published, 'reads': reads, 'likes': likes}
                except (ValueError, OverflowError, OSError):
                    ctx.warn(f"{account['name']}：一条文章的标题、URL 或发布时间无效，已跳过")
        if not successes:
            raise TaskError("所有公众号采集失败；" + (ctx.warnings[0] if ctx.warnings else "请检查登录或接口配置"),
                            ErrorCode.COLLECT)
        if promotions:
            self.notify(f"已按设置跳过 {promotions} 篇促销/直播类文章")
            logger.info("任务 %s 跳过促销文章 %d 篇", job['run_key'], promotions)
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
        if config.get('selection_mode') == 'hot' and candidates and not eligible:
            raise TaskError('没有达到阅读量门槛的文章，或接口未提供阅读量；请查看文章库，调整门槛或时间范围',
                            ErrorCode.CONFIG)
        db.update_job(job["id"], selected=json.dumps(selected))
        return selected

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
                ctx.warn(f"{article['title']}：原素材已清理，无法继续处理")
                continue
            self.notify(f"处理素材 {number}/{len(selected)}：{article['title']}")
            ctx.stage("正文与摘要")
            try:
                content, image_analysis = self._article_text(ctx, article)
                content_hash = digest(content)
                db.execute("UPDATE articles SET content=?,content_hash=?,error='' WHERE id=?",
                           (content, content_hash, article_id))
                if content_hash in hashes:
                    ctx.warn(f"{article['title']}：正文与其他素材相同，已去重")
                    continue
                hashes.add(content_hash)
                parts = self._summaries(ctx, article, content)
                metrics = db.rows('SELECT reads,likes,observed_at FROM job_metrics WHERE job_id=? AND article_id=?',
                                  (ctx.job['id'], article_id))
                sources.append({"source_id": f"S{article_id}", "article_id": article_id, "title": article["title"],
                                "url": article["url"], "account_name": article["account_name"],
                                "published_at": article["published_at"], "analysis": parts,
                                "metrics": metrics[0] if metrics else {},
                                "image_analysis": image_analysis})
            except ApiError as exc:
                if exc.fatal:
                    raise
                db.execute("UPDATE articles SET error=? WHERE id=?", (str(exc), article_id))
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
            parts.append(ctx.cached(article['id'], key, lambda: ctx.summarizer.summary(article, chunk)))
        return parts

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
                reference['comment_analysis'] = {'status': '获取或分析失败', 'reason': str(exc)}
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
            sources[0]['web_research'] = '联网搜索连接失败或超过 300 秒，已降级为仅基于公众号原文写作。'
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
            except ApiError as exc:
                message = 'AI 二审未完成，已保留通过基础校验的初稿：' + str(exc)
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
        if config.get('comments_enabled', False):
            self._comments(ctx, sources)
        research, degraded = (self._research(ctx, sources) if config.get('writing_mode') == 'single_web'
                              else (None, False))
        output, quality_notes = self._compose(ctx, sources + (research['sources'] if research else []))
        self.check()
        report = self._report(config, sources, output, quality_notes, research, degraded)
        if research:
            sources += research['sources']
        self.db.save_draft(job["id"], output["title"], output["body"], sources, report)
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
            metrics = s.get('metrics', {})
            report += (f"阅读量：{metrics.get('reads', '未知')}；点赞量：{metrics.get('likes', '未知')}；"
                       f"数据采集时间：{metrics.get('observed_at', '未知')}\n\n")
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
        done = self.db.rows('''SELECT COUNT(DISTINCT d.job_id) n FROM drafts d
            JOIN batch_items b ON b.child_job_id=d.job_id WHERE b.parent_job_id=?''', (parent_id,))[0]['n']
        status = '已完成' if done >= target else ('部分完成' if done else '失败')
        self.db.update_job(parent_id, status=status, error_code='',
                           message=f'已生成 {min(done, target)}/{target} 篇独立主题文章（含单篇重试结果）')

    def _batch_queue(self, job, config, selected):
        """Ranked article queue: the stored choices first, then ranked backups."""
        selected = list(dict.fromkeys(selected))
        # v0.9.6 and earlier stored only the first N choices; rebuild backups
        # from the parent's captured metrics so a retry can still use them.
        candidates = {}
        for row in self.db.rows('''SELECT m.article_id,m.reads,m.likes,a.published_at,a.title
                FROM job_metrics m JOIN articles a ON a.id=m.article_id WHERE m.job_id=?''', (job['id'],)):
            if is_promotion(row['title'], config):
                continue
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
        done, failures, titles, replacement_slots, replacements = 0, [], [], 0, []
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
            failures.append(f"{label}：{child['message']}")
            last_code = child.get('error_code') or ErrorCode.API
            if replaceable and not is_replacement:
                replacement_slots += 1
            elif not replaceable and is_replacement:
                # A shared failure (model, key, research) would hit every
                # replacement too; do not walk the whole queue.
                replacement_slots -= 1
        status = '已完成' if done == target else ('部分完成' if done else '失败')
        message = f'已生成 {done}/{target} 篇独立主题文章，每篇分别联网研究和写作'
        if replacements:
            message += f'；{len(replacements)} 篇素材提取失败，已由' + '、'.join(replacements) + '补位'
        if len(selected) < target or (done < target and replacement_slots > 0):
            message += '；符合条件的新素材不足，请扩大时间范围后创建新任务'
        self.db.update_job(job['id'], status=status, stage='完成', message=message,
                           error_code='' if done else last_code,
                           warnings=json.dumps(failures, ensure_ascii=False))
        self.notify(message)
        return status
