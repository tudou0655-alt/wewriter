"""Research stage: reader comment analysis and web research for the lead source.

Not to be confused with ``wewriter.research`` (the web search client); that one
is reached here only through ``engine.research_factory``.
"""
import inspect
import json
from ..api import ApiError, EveryInfra, digest, key_facts
from ..db import stamp
from ..logs import redact
from ..research import research_client

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
            model_config = ctx.config['providers'][provider]
            selected = ctx.config.get('summary_model')
            if selected and selected != model_config['model']:
                model_config = {**model_config, 'model': selected}
            key = digest(json.dumps(['comments-analysis-v1', provider, model_config,
                                     comments], sort_keys=True))
            analysis = ctx.cached(aid, key, lambda: (ctx.summarizer.analyze_comments(comments)
                                                     if comments else {'insights': []}))
            reference['comment_analysis'] = {'status': '已分析' if comments else '接口未返回可见评论',
                                             'sample_count': len(comments), **analysis}
        except (ApiError, ValueError) as exc:
            reference['comment_analysis'] = {'status': '获取或分析失败', 'reason': redact(str(exc))}
            ctx.warn('评论分析未完成：' + str(exc))


def research(engine, ctx, sources):
    """Return (research, degraded). Only connection failures and the background wait limit degrade."""
    ctx.stage('联网研究', '正在联网补充该主题的信息并核对来源…')
    job_id = ctx.job['id']
    cached = engine.db.rows('SELECT result FROM research_cache WHERE job_id=?', (job_id,))
    if cached and json.loads(cached[0]['result']).get('provider') != ctx.config.get('search_provider', 'openai'):
        cached = []
    try:
        if cached:
            research = json.loads(cached[0]['result'])
        else:
            # The article's key identifiers go to this search, so their official originals are
            # found before writing (the copy keeps them out of the writing material).
            facts = key_facts(sources[0].get('analysis'))
            research = _search(engine, ctx, {**sources[0], 'key_facts': facts})
            research['provider'] = ctx.config.get('search_provider', 'openai')
            research['key_facts'] = facts
            engine.db.execute('INSERT OR REPLACE INTO research_cache VALUES(?,?)',
                              (job_id, json.dumps(research, ensure_ascii=False)))
            engine.db.execute('DELETE FROM research_pending WHERE job_id=?', (job_id,))
        sources[0]['web_research'] = research['notes']
        return research, False
    except ApiError as exc:
        # A finished, failed or timed-out (and cancelled) search is not fetched again. After a
        # key or permission error the search may still finish, so a retry tries it first.
        if not exc.fatal:
            engine.db.execute('DELETE FROM research_pending WHERE job_id=?', (job_id,))
        if exc.kind != 'network':
            raise
        ctx.config['research_degraded'] = True
        sources[0]['web_research'] = RESEARCH_DEGRADED_MARK + '或等待超时，已降级为仅基于公众号原文写作。'
        ctx.warn('联网搜索连接失败或超时，已降级为仅基于现有公众号内容生成；本稿未经联网补充核实')
        return None, True


def _search(engine, ctx, article):
    """Start a background search, or fetch the one an earlier attempt of this task started."""
    job_id = ctx.job['id']
    searcher = research_client(engine, ctx)
    # Test doubles and custom factories may take only the article.
    if 'on_created' not in inspect.signature(searcher.search).parameters:
        return searcher.search(article)
    pending = engine.db.rows('SELECT response_id FROM research_pending WHERE job_id=?', (job_id,))
    if pending:
        ctx.stage('联网研究', '正在取回上次已提交的联网搜索结果…')

    def remember(response_id):
        engine.db.execute('INSERT OR REPLACE INTO research_pending VALUES(?,?,?)', (job_id, response_id, stamp()))
    return searcher.search(article, response_id=pending[0]['response_id'] if pending else None,
                           on_created=remember)
