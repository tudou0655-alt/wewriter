"""Research stage: reader comment analysis and web research for the lead source.

Not to be confused with ``wewriter.research`` (the web search client); that one
is reached here only through ``engine.research_factory``.
"""
import json
from ..api import ApiError, EveryInfra, digest
from ..logs import redact

RESEARCH_DEGRADED_MARK = '联网搜索连接失败'


def comments(engine, ctx, sources):
    provider = ctx.config['summary_provider']
    for reference in sources:
        engine.check()
        ctx.stage('评论分析', '正在通过 EveryInfra 获取评论并分析读者关注点…')
        aid = reference['article_id']
        try:
            comments = ctx.cached(aid, 'comments-v1:' + str(ctx.job['id']), lambda: EveryInfra(
                engine.vault.get('everyinfra'), ctx.transport).comments(reference['url'],
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


def research(engine, ctx, sources):
    """Return (research, degraded). Only connection failures degrade."""
    ctx.stage('联网研究', '正在联网补充该主题的信息并核对来源…')
    job_id = ctx.job['id']
    cached = engine.db.rows('SELECT result FROM research_cache WHERE job_id=?', (job_id,))
    if cached and json.loads(cached[0]['result']).get('provider') != 'openai':
        cached = []
    try:
        if cached:
            research = json.loads(cached[0]['result'])
        else:
            research = engine.research_factory(engine.vault.get('openai'), ctx.config,
                                               ctx.transport).search(sources[0])
            research['provider'] = 'openai'
            engine.db.execute('INSERT OR REPLACE INTO research_cache VALUES(?,?)',
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
