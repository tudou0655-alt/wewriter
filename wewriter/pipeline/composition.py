"""Composition stage: style reference, writing, AI quality review, the material
report and saving a draft; also the "再写一篇" rewrite task."""
import json
import re
import uuid
from copy import deepcopy
from ..api import ApiError, digest
from ..logs import redact
from .collect import METRIC_SOURCES
from .common import ErrorCode, TaskError, china_time


def style(engine, ctx, reference):
    ctx.stage('热点风格分析', '正在分析最高阅读量有效素材的写作风格…')
    provider = ctx.config['summary_provider']
    content = engine.db.rows('SELECT content FROM articles WHERE id=?',
                             (reference['article_id'],))[0]['content'][:24000]
    key = digest(json.dumps(['style-v2', provider, ctx.config['providers'][provider],
                             reference['title'], content], sort_keys=True))
    reference['style_reference'] = ctx.cached(
        reference['article_id'], key, lambda: ctx.summarizer.analyze_style(content, reference['title']))


def compose(engine, ctx, writing_sources):
    ctx.stage("综合写作", "正在综合材料、组织大纲并生成正文…")
    writer = ctx.writer
    output = writer.write(writing_sources, ctx.config)
    quality_notes = []
    review = getattr(writer, 'review', None)
    if ctx.config.get('quality_review_enabled', True) and callable(review):
        engine.check()
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


def write_one(engine, ctx, selected):
    config, job = ctx.config, ctx.job
    sources = engine._materials(ctx, selected)
    if len(sources) < config["min_sources"]:
        if config.get('writing_mode') == 'single_web' and ctx.warnings:
            raise TaskError("素材处理未完成：" + ctx.warnings[-1], ErrorCode.MATERIAL)
        detail = "最近错误：" + ctx.warnings[-1] if ctx.warnings else "请增加时间范围或调整最少素材数"
        raise TaskError(f"素材处理未完成：通过校验 {len(sources)} 篇，需要 {config['min_sources']} 篇；{detail}",
                        ErrorCode.MATERIAL)
    if len(json.dumps(sources, ensure_ascii=False)) > 180000:
        raise TaskError("写作素材过长，请减少单次文章数量", ErrorCode.CONFIG)
    engine.check()
    if config.get('selection_mode') == 'hot' or config.get('writing_mode') == 'single_web':
        engine._style(ctx, sources[0])
    # EveryInfra comment analysis was withdrawn in T-010: old snapshots with
    # comments_enabled=True (retries, schedules) must not fetch comments.
    research, degraded = (engine._research(ctx, sources) if config.get('writing_mode') == 'single_web'
                          else (None, False))
    output, quality_notes = engine._compose(ctx, sources + (research['sources'] if research else []))
    engine.check()
    report = engine._report(config, sources, output, quality_notes, research, degraded)
    if research:
        sources += research['sources']
    engine.db.save_draft(job["id"], output["title"], output["body"], sources, report)
    ctx.funnel.data['drafts'] = 1
    ctx.funnel.data['quality'] = {
        'research': 'degraded' if degraded else ('done' if research else 'not_used'),
        'review': ctx.review_state, 'warnings': len(ctx.warnings)}
    ctx.funnel.save()
    status = "部分完成" if ctx.warnings else "已完成"
    engine.db.update_job(job["id"], stage="完成", status=status, error_code='',
                         message=f"已生成 1 篇草稿，使用 {len(sources)} 篇素材，请人工审核")
    engine.notify("稿件已保存，可在稿件工作台查看与编辑")
    return status


def report(config, sources, output, quality_notes, research, degraded):
    report = "# 每日素材报告\n\n"
    if config.get('selection_mode') == 'hot':
        report += ('选文规则：时间窗内按接口返回阅读量降序，点赞量及发布时间作为同阅读量时的排序依据。'
                   '缺失阅读量不参与热点排名。仅代表已管理公众号及本轮查询范围。\n\n')
        report += (f"风格参考：{sources[0]['title']}\n\n"
                   + json.dumps(sources[0]['style_reference'], ensure_ascii=False, indent=2) + '\n\n')
    for s in sources:
        report += f"## [{s['source_id']}] {s['title']}\n公众号：{s['account_name']}\n原文：{s['url']}\n\n"
        report += metrics_line(s.get('metrics') or {}) + '\n\n'
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


def metrics_line(metrics):
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


def article_drafts(db, article_id):
    """Drafts already written from one source article, oldest first."""
    return [row for row in db.rows('SELECT id,title,body,sources FROM drafts WHERE cleared=0 ORDER BY id')
            if any(s.get('article_id') == int(article_id) for s in json.loads(row['sources'] or '[]'))]


def rewrite_job(engine, article_id, config, note=''):
    """A new single-article task from a source that may already have drafts (customer
    2026-10-25: a past hit is written again). Earlier titles are passed as avoid_titles so
    the new draft takes another angle; earlier drafts are untouched. Not started here."""
    db = engine.db
    article_id = int(article_id)
    if not db.rows('SELECT id FROM articles WHERE id=?', (article_id,)):
        raise ValueError('文章不存在或已被清理')
    # One pending rewrite per article: repeated clicks used to queue near-identical drafts.
    for row in db.rows("SELECT status FROM jobs WHERE run_key LIKE ?", (f'rewrite:{article_id}:%',)):
        if row['status'] in ('等待中', '运行中'):
            raise ValueError('这篇文章已经在“再写一篇”的队列里，等它完成后再写下一篇')
    drafts = engine.article_drafts(article_id)
    # Earlier angles = title + section headings, so the writer can see the structure to avoid.
    angles = [d['title'] + '｜小标题：' + ' / '.join(re.findall(r'(?m)^#{1,6}\s*(.+?)\s*$', d['body'] or '')[:8])
              for d in drafts]
    config = deepcopy(config)
    config.update(writing_mode='single_web', selection_mode='hot', min_sources=1, article_limit=1,
                  draft_count=1, topic=str(note or '').strip()[:300], avoid_titles=[d['title'] for d in drafts],
                  avoid_angles=angles)
    job = db.job(f'rewrite:{article_id}:{uuid.uuid4().hex}', config)
    db.update_job(job['id'], selected=json.dumps([article_id]))
    return db.rows('SELECT * FROM jobs WHERE id=?', (job['id'],))[0]
