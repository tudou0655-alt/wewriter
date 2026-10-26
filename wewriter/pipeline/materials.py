"""Materials stage: body text, image reading for image-only articles, body
de-duplication, and cached per-chunk summaries with bounded retries."""
import json
import time
from ..api import ApiError, Cancelled, digest
from ..logs import logger, redact
from .common import TaskError, error_code


def materials(engine, ctx, selected):
    """Extract body text (and images if allowed) and summarise each article."""
    config, db = ctx.config, engine.db
    sources, hashes = [], set()
    for number, article_id in enumerate(selected, 1):
        engine.check()
        article = db.rows("SELECT * FROM articles WHERE id=?", (article_id,))[0]
        if article['cleared'] and config.get('allow_recollect_cleared', False):
            db.execute('UPDATE articles SET cleared=0 WHERE id=?', (article_id,))
            article['cleared'] = 0
        if article['cleared']:
            ctx.funnel.material('material_cleared', article)
            ctx.warn(f"{article['title']}：原素材已清理，无法继续处理")
            continue
        engine.notify(f"处理素材 {number}/{len(selected)}：{article['title']}")
        ctx.stage("正文与摘要")
        body_ready = False
        try:
            content, image_analysis = engine._article_text(ctx, article)
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
            parts = engine._summaries(ctx, article, content)
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


def article_text(engine, ctx, article):
    content = article["content"] or ctx.source.content(article["url"])
    image_analysis = '【图片识别资料（AI 转写' in content
    if len(content) >= 100:
        return content, image_analysis
    if ctx.config.get('image_articles', 'skip') != 'vision':
        raise ApiError('正文以图片为主（文字不足 100 字），已按设置跳过；'
                       '如需识别图片内容，可在设置中开启“识别图片型文章”', kind='image_only')
    return engine._read_images(ctx, article, content), True


def read_images(engine, ctx, article, content):
    article_id = article['id']
    images = engine.db.article_images(article_id)
    if not images:
        raise ApiError('正文不足 100 字且未取得原文图片，无法可靠总结')
    image_root = engine.db.path.parent.resolve()
    paths, fingerprints = [], []
    for image in images:
        path = (engine.db.path.parent / image['local_path']).resolve()
        if not path.is_relative_to(image_root) or not path.is_file():
            continue
        paths.append(path)
        fingerprints.append([image['source_url'], path.stat().st_size])
    if not paths:
        raise ApiError('正文文字不足，已记录图片但本地文件不存在，请补采图片')
    key = digest(json.dumps(['image-reading-v1', ctx.config['providers']['openai'], article['title'],
                             content, fingerprints], ensure_ascii=False, sort_keys=True))

    def recognise():
        if not engine.vault.get('openai'):
            raise ApiError('图片型文章需要 OpenAI 图片识别，请先在设置中保存 OpenAI API Key')
        ctx.stage('图片识别', f"正文以图片为主，正在识别 {min(len(paths), 8)} 张原文图片…")
        try:
            return ctx.model('openai').analyze_article_images(article, paths, content)
        finally:
            ctx.stage('正文与摘要')
    result = ctx.cached(article_id, key, recognise)
    return (content.strip() + '\n\n' + result['content']).strip()


def summaries(engine, ctx, article, content):
    # Cache each chunk separately so a retry never repeats completed summaries.
    provider = ctx.config["summary_provider"]
    parts = []
    for start in range(0, len(content), 24000):
        engine.check()
        chunk = content[start:start + 24000]
        key = digest(json.dumps(["summary-v1", provider, ctx.config["providers"][provider], chunk],
                                ensure_ascii=False, sort_keys=True))
        parts.append(ctx.cached(article['id'], key,
                                lambda: engine._summary_attempts(ctx, article, chunk, start // 24000 + 1)))
    return parts


def summary_attempts(engine, ctx, article, chunk, part):
    """Retry only failed network calls; cache remains owned by the chunk caller."""
    transport = ctx.transport
    previous = getattr(transport, 'retry_attempts', 3)
    transport.retry_attempts = 1  # Avoid multiplying Transport and stage retries.
    provider = ctx.config['summary_provider']
    model = redact(str(ctx.config['providers'][provider]['model']))
    try:
        for attempt in range(1, 4):
            engine.check()
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
                if engine.cancel.wait(delay):
                    raise Cancelled('任务已停止')
    finally:
        transport.retry_attempts = previous
