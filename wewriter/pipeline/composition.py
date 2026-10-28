"""Composition stage: style reference, writing, AI quality review, the material
report and saving a draft; also the "再写一篇" rewrite task."""
import json
import re
import uuid
from copy import deepcopy
from ..api import (ApiError, Model, Transport, digest, draft_shape, fact_identifiers, identifier_status,
                   is_verification_todo, merge_similar, split_todos)
from ..config import migrate
from ..db import stamp
from ..logs import redact
from .collect import METRIC_SOURCES
from .common import ErrorCode, TaskError, china_time
from .research import RESEARCH_DEGRADED_MARK

REVIEW_FAILED_NOTE = 'AI 二审未完成，已保留通过基础校验的初稿：'
REVIEW_MISSING = '缺少资料，无法补跑，请重新生成'
# Notes an earlier review pass produced about its own output; a new pass makes its own.
EARLIER_REVIEW_NOTES = (REVIEW_FAILED_NOTE, '二审新增或改写的句子未列入逐句核对', '二审逐句核对没有包含标题')


def article_chars(body):
    """Match the draft workbench's displayed body count."""
    return len(re.sub(r'\s|\[[SW]\d+\]|[#>*]', '', body or ''))


def length_floor(config):
    return int(config.get('target_words', 0) * 0.9)


def length_warning(output, config):
    floor = length_floor(config)
    actual = article_chars(output['body'])
    if floor and actual < floor:
        return (f'正文约 {actual} 字，低于目标 {config["target_words"]} 字的 90%（至少 {floor} 字）；'
                '已保留为待修改稿，请增加素材、手动补写或降低目标字数')
    return ''


def write_to_length(writer, sources, config):
    # Keep this stage to one writing request. A smaller, evidence-bound expansion
    # call runs only when the result is short, after the initial draft is cached.
    return writer.write(sources, config)


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
    # A finished initial draft is a stage result, not a published draft. Cache
    # it under this job and exact inputs so a later review failure cannot make
    # the next attempt pay for the same writing again.
    article_id = writing_sources[0]['article_id']
    # Recent siblings and the global style library can change while this task
    # is paused. Freeze those inputs once for this article/job; the job's own
    # topic and source content remain fingerprinted below.
    dynamic_fields = ('recent_drafts', 'style_card', 'example', 'account_names', 'style_note')
    snapshot_key = f"t040-input-v1:{ctx.job['id']}:snapshot"
    snapshot = ctx.cached(article_id, snapshot_key,
                          lambda: {field: ctx.config.get(field) for field in dynamic_fields})
    for field, value in snapshot.items():
        ctx.config[field] = deepcopy(value)
    def stage_key(stage, value):
        content_config = {key: value for key, value in ctx.config.items()
                          if key != 'max_paid_requests'}
        fingerprint = digest(json.dumps(['t040-stage-v1', ctx.job['id'], stage, value,
                                         writing_sources, content_config], ensure_ascii=False,
                                        sort_keys=True, default=str))
        return f"t040-stage-v1:{ctx.job['id']}:{stage}:{fingerprint}"
    output = deepcopy(ctx.cached(article_id, stage_key('write', 't048-length-v1'),
                                 lambda: (write_to_length(writer, writing_sources, ctx.config)
                                          if isinstance(writer, Model) else writer.write(writing_sources, ctx.config))))
    if isinstance(writer, Model) and length_warning(output, ctx.config):
        engine.check()
        ctx.stage('篇幅补充', '正在依据已有事实补充正文…')
        try:
            output = deepcopy(ctx.cached(article_id, stage_key('length-expand-v1', output),
                                         lambda: writer.expand(output, writing_sources, ctx.config)))
        except ApiError as exc:
            ctx.warn('自动补充篇幅未完成，保留已有稿件：' + redact(str(exc))[:120])
    quality_notes = []
    if ctx.config.get('draft_first_mode', False):
        ctx.review_state = 'off'
        ctx.verified = {'sources': [], 'report': '\n## 审核状态\n优先成稿模式：未执行自动二审、联网查证和风格改写；稿件需人工审核。\n'}
        ctx.styled = None
        return output, quality_notes
    review = getattr(writer, 'review', None)
    if ctx.config.get('quality_review_enabled', True) and callable(review):
        engine.check()
        ctx.stage('质量二审', '正在对照证据复核事实边界、结构和表达…')
        try:
            output = deepcopy(ctx.cached(article_id, stage_key('fact-review', output),
                                         lambda: review(output, writing_sources, ctx.config)))
            quality_notes = output.pop('quality_notes', [])
            ctx.review_state = 'done'
        except ApiError as exc:
            ctx.review_state = 'failed'
            message = REVIEW_FAILED_NOTE + redact(str(exc))
            output.setdefault('review_notes', []).append(message)
            ctx.warn(message)
    def verify_stage():
        verified_output = verify_claims(engine, ctx, output, writing_sources)
        return {'output': verified_output, 'verified': ctx.verified}
    # Failed or skipped checks caused by a transient service error are not
    # completion checkpoints: a retry may safely try them again.
    verify_key = stage_key('verification', output)
    verified = deepcopy(ctx.cached(article_id, verify_key, verify_stage))
    verify_report = (verified.get('verified') or {}).get('report') or ''
    if ('联网查证失败' in verify_report or '未查证：联网搜索' in verify_report
            or '修订未通过校验' in verify_report) and hasattr(ctx, 'db'):
        ctx.db.execute('DELETE FROM analyses WHERE article_id=? AND cache_key=?',
                       (article_id, verify_key))
    ctx.verified = verified['verified']
    output = verified['output']

    def style_stage():
        styled_output = style_pass(engine, ctx, output, writing_sources)
        return {'output': styled_output, 'styled': ctx.styled}
    style_key = stage_key('style-review', output)
    styled = deepcopy(ctx.cached(article_id, style_key, style_stage))
    if '风格审核未完成' in str((styled.get('styled') or {}).get('reason') or '') and hasattr(ctx, 'db'):
        ctx.db.execute('DELETE FROM analyses WHERE article_id=? AND cache_key=?',
                       (article_id, style_key))
    ctx.styled = styled['styled']
    return styled['output'], quality_notes


def verify_claims(engine, ctx, output, writing_sources):
    """Check the review's verify items automatically: at most one targeted web search for the
    official originals and one targeted revision per article. Found sources are cited, claims
    without a source are made cautious; nothing is left for the reader to go and check.
    Sets ctx.verified = {'sources': [...], 'report': '...'} for the draft and its report."""
    # Whatever model wrote the output, requests to check a source never reach the reader.
    items = split_todos(output).pop('verify_items', None) or []
    ctx.verified = {'sources': [], 'report': ''}
    if not items:
        return output
    # The search before writing already looked up the article's key identifiers. An item whose
    # identifiers it all answered (a cited original, or an explicit "not found", which the review
    # rules already turn into cautious wording) needs no second search.
    notes = next((s.get('web_research') for s in writing_sources if s.get('web_research')), '')
    # DeepSeek's structured Search returns snippets, not checked original documents.
    # Do not call an identifier verified merely because a snippet contains it.
    covered = ([] if ctx.config.get('search_provider') == 'deepseek' else
               [i for i in items if _answered_by_research(i, notes)])
    items = [i for i in items if i not in covered]
    done = ('\n已由写作前联网研究覆盖 {} 项（找到原件或明确未找到官方出处）：\n'.format(len(covered))
            + '\n'.join('- ' + i['claim'][:120] for i in covered) if covered else '')
    if not items:
        ctx.verified['report'] = ('\n\n## 自动查证\n待查证 0 项。' + done
                                  + '\n\n结果：已由写作前联网研究覆盖，未触发兜底搜索。\n')
        return output
    listed = '\n'.join('- ' + i['claim'] + (f"（{'、'.join(i['identifiers'])}）" if i['identifiers'] else '')
                       for i in items)

    def note(outcome, extra=''):
        ctx.verified['report'] = (f'\n\n## 自动查证\n待查证 {len(items)} 项：\n{listed}\n{done}\n\n'
                                  f'结果：{outcome}\n{extra}')
        return output
    if not ctx.config.get('auto_verify', True):
        return note('设置中已关闭“二审后自动查证存疑事实”，未查证。')
    revise = getattr(ctx.writer, 'revise_for_verification', None)
    try:
        from ..research import research_client
        searcher = research_client(engine, ctx)
    except (ValueError, RuntimeError, ApiError) as exc:
        return note('未查证：联网搜索不可用（' + redact(str(exc))[:80] + '）。')
    if not callable(revise) or not callable(getattr(searcher, 'verify', None)):
        return note('未查证：当前模型或搜索接口不支持自动查证。')
    engine.check()
    ctx.stage('自动查证', '正在联网查证存疑事实…')
    try:
        findings = searcher.verify(output['title'], items)
    except ApiError as exc:
        if exc.kind == 'network' or exc.fatal:
            # Silent for the reader: the draft is kept as it is and only the report says why.
            return note('联网查证失败或超时，已跳过，稿件未改动（' + redact(str(exc))[:120] + '）。')
        findings = None     # the search found no usable official source
    if findings:
        findings = _renumber(findings, writing_sources)
    engine.check()
    try:
        revised = revise(output, items, findings, writing_sources, ctx.config)
    except ApiError as exc:
        return note('查证后的修订未通过校验，未采用，稿件未改动（' + redact(str(exc))[:160] + '）。')
    checks = [x for x in revised.pop('quality_notes', []) if x.startswith('逐句核对｜')]
    if findings:
        ctx.verified['sources'] = findings['sources']
        extra = ('检索时间：' + findings['retrieved_at'] + '\n\n' + findings['notes'] + '\n\n'
                 + '\n'.join(f"[{s['source_id']}] {s['title']} — {s['url']}" for s in findings['sources']))
        note('已找到官方来源并补入引用；未找到来源的说法已改为审慎表述或删除。', extra)
    else:
        note('未找到官方来源，相关说法已改为审慎表述或删除。')
    if checks:
        ctx.verified['report'] += '\n\n' + check_table(checks).replace(CHECK_TABLE_HEADING, '### 查证修订（初稿 → 修订稿）')
    return revised


def _answered_by_research(item, notes):
    ids = item.get('identifiers') or fact_identifiers(item.get('claim', ''))
    return bool(ids) and all(identifier_status(i, notes) for i in ids)


def key_fact_lines(research):
    """The key-identifier section of the material report: what the search before writing found."""
    facts = (research or {}).get('key_facts') or []
    ids = list(dict.fromkeys(i for f in facts for i in f.get('identifiers', [])))
    if not ids:
        return ''
    label = {'found': '已找到官方原件', 'not_found': '研究写明未找到官方出处', None: '研究未涉及'}
    return ('\n## 关键标识（写作前联网核对）\n'
            + '\n'.join(f"- {i}：{label[identifier_status(i, research.get('notes'))]}" for i in ids)
            + '\n二审后如仍有未覆盖的事实，才触发兜底搜索，结果见“自动查证”；没有该节表示未触发。\n')


def _renumber(findings, writing_sources):
    """Number the search's sources after the article's own [W] sources and rewrite its marks."""
    taken = [int(s['source_id'][1:]) for s in writing_sources
             if str(s.get('source_id', '')).startswith('W') and s['source_id'][1:].isdigit()]
    start = max(taken, default=0)
    mapping = {s['source_id']: f'W{start + n}' for n, s in enumerate(findings['sources'], 1)}
    notes = re.sub(r'\[(W\d+)\]', lambda m: '[' + mapping.get(m.group(1), m.group(1)) + ']', findings['notes'])
    sources = [{**s, 'source_id': mapping[s['source_id']]} for s in findings['sources']]
    return {**findings, 'notes': notes, 'sources': sources}


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
    # The writer now uses the saved style card and example skeleton. An
    # additional per-article style-model call is no longer used by generation.
    # EveryInfra comment analysis was withdrawn in T-010: old snapshots with
    # comments_enabled=True (retries, schedules) must not fetch comments.
    research, degraded = (engine._research(ctx, sources) if config.get('writing_mode') == 'single_web'
                          else (None, False))
    # Recent drafts (batch siblings included, as they are saved one by one) so this one reads differently.
    config['recent_drafts'] = recent_shapes(engine.db)
    use_style(engine.db, config, sources[0].get('title', ''))
    output, quality_notes = engine._compose(ctx, sources + (research['sources'] if research else []))
    engine.check()
    verified = getattr(ctx, 'verified', None) or {}
    output, leak_text = leak_guard(output, sources + (research['sources'] if research else [])
                                   + verified.get('sources', []), config)
    for note in output.get('review_notes', []):
        if note.startswith('【来源提示】'):
            ctx.warn(note)
    short_note = length_warning(output, config) if isinstance(ctx.writer, Model) else ''
    if short_note:
        output.setdefault('review_notes', []).append(short_note)
        ctx.warn(short_note)
    report = engine._report(config, sources, output, quality_notes, research, degraded)
    if research:
        sources += research['sources']
    report += verified.get('report', '') + style_report(config, getattr(ctx, 'styled', None)) + leak_text
    sources += verified.get('sources', [])
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
        if sources[0].get('style_reference'):
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
    if output.get('structure'):
        report += f"## 写作结构\n{output['structure']}" + (f"：{output['structure_reason']}" if output.get('structure_reason') else '') + "\n\n"
    report += "## 写作大纲\n" + "\n".join("- " + x for x in output["outline"])
    report += "\n\n## 审核提示\n" + "\n".join("- " + x for x in output["review_notes"])
    if quality_notes:
        checks = [x for x in quality_notes if x.startswith('逐句核对｜')]
        others = [x for x in quality_notes if not x.startswith('逐句核对｜')] or ['已完成逐句核对，见下表']
        report += "\n\n## AI 二审\n" + "\n".join("- " + x for x in others)
        if checks:
            report += "\n\n" + check_table(checks)
    report += ("\n\n优先成稿模式：引用仅做基本整理，事实、来源及文章观点需人工审核。\n"
               if config.get('draft_first_mode', False) else
               "\n\n来源标记与引用片段已做格式校验，事实及文章观点仍需人工审核。\n")
    if degraded:
        report += '\n## 联网研究状态\n联网搜索连接失败或超过等待上限，本稿已降级为仅基于公众号原文生成，未使用联网补充资料。\n'
    if research:
        report += '\n## 联网补充资料\n检索时间：' + research['retrieved_at'] + '\n\n' + research['notes'] + '\n\n'
        report += '\n'.join(f"[{s['source_id']}] {s['title']} — {s['url']}" for s in research['sources'])
        report += key_fact_lines(research)
    return report


CHECK_TABLE_HEADING = '## 逐句核对（初稿 → 修订稿）'


def check_table(lines):
    """Sentence checks (api.check_line) as a two-column draft/revision table."""
    rows = [CHECK_TABLE_HEADING, '| 处理 | 初稿 | 修订稿 | 证据 | 说明 |', '|---|---|---|---|---|']
    for line in lines:
        parts = line.split('｜')
        verdict, sentence = parts[1], parts[2]
        evidence = parts[3].removeprefix('证据：') if len(parts) > 3 else ''
        rest = parts[4:]
        draft = next((p.removeprefix('初稿：') for p in rest if p.startswith('初稿：')), None)
        note = ' / '.join(p for p in rest if not p.startswith('初稿：'))
        if verdict == '修正' and draft is None:
            before, after = sentence, '（见说明）'      # older checks named only the draft sentence
        else:
            before, after = {'保留': (sentence, sentence), '删除': (sentence, '（已删除）'),
                             '新增': ('（新增）', sentence)}.get(verdict, (draft, sentence))
        rows.append('| ' + ' | '.join(c.replace('|', '/') for c in (verdict, before, after, evidence, note)) + ' |')
    return '\n'.join(rows)


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


def review_state(report):
    """AI review state read from a draft's machine report: 'done', 'failed', or 'missing'
    (switched off, or never reached)."""
    report = report or ''
    if re.search(r'(?m)^## AI 二审$', report):
        return 'done'
    return 'failed' if REVIEW_FAILED_NOTE in report else 'missing'


def _report_items(report, heading):
    """The "- " lines of one report section."""
    match = re.search(r'(?m)^## ' + re.escape(heading) + r'\n((?:- [^\n]*(?:\n|$))*)', report or '')
    return [line[2:] for line in match.group(1).splitlines()] if match else []


def rerun_review(engine, draft_id):
    """Run the AI review once more on a saved draft: one whose review failed or never ran, or
    one reviewed before the current sentence-check rules, to check it again under them.

    Uses only what the draft stored (sources with their analyses, style reference and web
    research) and the task's frozen configuration; no collection or research is repeated.
    A revision that passes the usual checks becomes a new version; on any failure the draft
    is left as it was. Returns the new revision number."""
    db = engine.db
    rows = db.rows('''SELECT d.id, d.revision, d.title, d.body, d.sources, d.report, d.job_id, j.config
                      FROM drafts d JOIN jobs j ON j.id=d.job_id WHERE d.id=? AND d.cleared=0''', (int(draft_id),))
    if not rows:
        raise ValueError('稿件不存在或已清理')
    draft = rows[0]
    try:
        config = migrate(json.loads(draft['config']))
        sources = json.loads(draft['sources'])
    except (ValueError, TypeError):
        raise ValueError(REVIEW_MISSING) from None
    current_cap = db.config().get('max_paid_requests')
    old_cap = config.get('max_paid_requests')
    if old_cap is not None and (current_cap is None or current_cap > old_cap):
        config['max_paid_requests'] = current_cap
    sources = [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []
    originals = [s for s in sources if str(s.get('source_id', '')).startswith('S')]
    web = [s for s in sources if str(s.get('source_id', '')).startswith('W')]
    if not originals or any(not isinstance(s.get('analysis'), list) or not s['analysis'] for s in originals):
        raise ValueError(REVIEW_MISSING)
    research, degraded = None, False
    if config.get('writing_mode') == 'single_web':
        notes = originals[0].get('web_research')
        degraded = not web or str(notes or '').startswith(RESEARCH_DEGRADED_MARK)
        if not degraded:
            if not isinstance(notes, str) or not notes.strip():
                raise ValueError(REVIEW_MISSING)
            cached = db.rows('SELECT result FROM research_cache WHERE job_id=?', (draft['job_id'],))
            retrieved = json.loads(cached[0]['result']).get('retrieved_at') if cached else None
            research = {'notes': notes, 'sources': web,
                        'retrieved_at': retrieved or web[0].get('retrieved_at') or '未记录'}
        config['research_degraded'] = degraded
    try:
        # The material report is rebuilt after the review; check now that the stored sources can
        # still produce it, before paying for a model call.
        report(config, originals, {'outline': [], 'review_notes': []}, [], research, degraded)
    except (KeyError, TypeError, AttributeError, IndexError):
        raise ValueError(REVIEW_MISSING) from None
    first = {'title': draft['title'], 'body': draft['body'],
             'outline': _report_items(draft['report'], '写作大纲'),
             'review_notes': [n for n in _report_items(draft['report'], '审核提示')
                              if not n.startswith(EARLIER_REVIEW_NOTES)]}
    name = config['writer_provider']
    transport = Transport(engine.cancel)
    from ..costs import CostMeter
    from .collect import Funnel, load_funnel
    job_row = db.rows('SELECT id,funnel FROM jobs WHERE id=?', (draft['job_id'],))[0]
    transport.cost_meter = CostMeter(Funnel(db, job_row['id'], load_funnel(job_row['funnel']) or Funnel.empty()),
                                     config.get('max_paid_requests'))
    writer = engine.model_factory(name, config['providers'][name], engine.vault.get(name), transport)
    review = getattr(writer, 'review', None)
    if not callable(review):
        raise ValueError('当前写作模型不支持 AI 二审')
    output = review(first, sources, config)
    quality_notes = [f"补跑二审：{china_time(stamp())}，基于第 {draft['revision']} 版"] + output.pop('quality_notes', [])
    # The old draft's source-check requests became verify items in the review; check them now,
    # exactly as a new task would.
    # A re-run is a new action: the switch as it is set now applies, not the task's old snapshot.
    config['auto_verify'] = db.config().get('auto_verify', True)
    config['recent_drafts'] = recent_shapes(db, exclude=draft['id'])
    use_style(db, config, originals[0].get('title', ''))
    run = _RerunContext(config, writer, transport)
    output = verify_claims(engine, run, output, sources)
    output = style_pass(engine, run, output, sources)
    output, leak_text = leak_guard(output, sources + run.verified['sources'], config)
    short_note = length_warning(output, config) if isinstance(writer, Model) else ''
    if short_note:
        output.setdefault('review_notes', []).append(short_note)
    manual = [line for line in (draft['report'] or '').splitlines() if line.startswith('【人工审核记录】')]
    text = (report(config, originals, output, quality_notes, research, degraded) + run.verified['report']
            + style_report(config, run.styled) + leak_text)
    text += ''.join('\n' + line for line in manual)
    added = run.verified['sources']
    return db.revise_draft(draft['id'], draft['revision'], output['title'], output['body'], text,
                           sources + added if added else None)


class _RerunContext:
    """The parts of a task run that verify_claims uses, for a review re-run outside a task."""

    def __init__(self, config, writer, transport):
        self.config, self.writer, self.transport = config, writer, transport
        self.verified = {'sources': [], 'report': ''}
        self.styled = None

    def stage(self, name, message=None):
        pass


def use_style(db, config, title):
    """Put the current style card and the example closest to this article into the task config.
    Only the writer and the style review read them; the fact review and the checks never do."""
    from ..style import choose_example, style_block
    config['style_card'] = db.config().get('style_card')
    # Every managed account is blocked from articles, not only this article's source.
    config['account_names'] = [a['name'] for a in db.accounts()]
    example = choose_example(db.examples(), title)
    config['example'] = ({'id': example['id'], 'title': example['title'], 'skeleton': example['skeleton']}
                         if example else None)
    config['style_note'] = style_block(config['style_card'], config['example'])[1]


def leak_guard(output, sources, config):
    """Last check before a draft is saved: no other WeChat account, its post titles or authors
    (the customer's iron rule). Every model step already enforces it; anything that still slips
    through is rewritten to a neutral attribution or removed here. Returns (output, report text);
    the report is internal and may name the sources."""
    from ..anonymity import block_list, hits, scrub
    blocked = block_list(sources, config.get('account_names', []))
    fixes = list(output.pop('leak_fixes', []))
    if hits(output['title'], blocked):
        raise TaskError('稿件标题出现了其他公众号或其文章，未保存；请重试该篇任务', ErrorCode.MODEL_OUTPUT)
    if hits(output['body'], blocked):
        output['body'], more = scrub(output['body'], blocked)
        fixes += more
        if hits(output['body'], blocked):
            raise TaskError('稿件仍出现其他公众号或其文章，未保存；请重试该篇任务', ErrorCode.MODEL_OUTPUT)
    output['outline'] = [o for o in output.get('outline', []) if not hits(o, blocked)]
    lines = ['', '', '## 来源保密检查',
             '结果：' + ('程序已处理以下句子，最终稿件不含其他公众号名称、文章标题或“公众号/推文”等说法' if fixes
                       else '未发现其他公众号名称、文章标题或“公众号/推文”等说法')]
    lines += [f'- {kind}：{text}' for kind, text in fixes]
    return output, '\n'.join(lines) + '\n'


def style_pass(engine, ctx, output, writing_sources):
    """Review every draft when enabled; deterministic signals guide, but do not gate, the model.
    A rewrite gets structural checks before adoption, not a semantic guarantee of factuality."""
    from ..api import readability_problems
    from ..style import same_facts, strength_problems
    config = ctx.config
    signals = readability_problems(output, config.get('recent_drafts') or [])
    ctx.styled = {'signals': signals, 'scores': None, 'adopted': False, 'reason': ''}
    review = getattr(ctx.writer, 'style_review', None)
    if not config.get('style_review_enabled', True) or not callable(review):
        ctx.styled['reason'] = '未做风格审核（已关闭或模型不支持）'
        return output
    engine.check()
    ctx.stage('风格审核', '正在对照风格卡检查标题、开头和写法…')
    try:
        verdict = review(output, writing_sources, config, signals)
    except ApiError as exc:
        ctx.styled['reason'] = '风格审核未完成，保留原稿：' + redact(str(exc))[:120]
        return output
    ctx.styled['scores'] = verdict['scores']
    needs_rewrite = any(verdict['scores'][key] < (4 if key in ('title', 'opening', 'hierarchy', 'voice') else 3)
                        for key in ('title', 'opening', 'hierarchy', 'voice', 'usefulness', 'distinctness'))
    rewrite = verdict.get('rewrite')
    if not needs_rewrite:
        ctx.styled['reason'] = '标题、开头、层级、口吻不低于 4 分，其余不低于 3 分，未改写'
        return output
    if not rewrite:
        ctx.styled['reason'] = '写法评分未达门槛但未返回有效改写，保留原稿待人工复核'
        return output
    candidate = {**output, 'title': rewrite['title'], 'body': rewrite['body'],
                 'outline': rewrite.get('outline') if isinstance(rewrite.get('outline'), list) else output.get('outline', []),
                 'review_notes': list(output.get('review_notes', []))}
    try:
        candidate = ctx.writer._finish_draft(candidate, writing_sources, config)
    except (ApiError, AttributeError) as exc:
        ctx.styled['reason'] = '风格改写未通过来源校验，未采用：' + redact(str(exc))[:120]
        return output
    problems = same_facts(output, candidate) + strength_problems(
        candidate['title'] + '\n' + candidate['body'], writing_sources)
    if problems:
        ctx.styled['reason'] = '风格改写改动了事实，未采用：' + '；'.join(problems[:4])
        return output
    original_chars = article_chars(output['body'])
    floor = length_floor(config)
    if floor and original_chars >= floor and article_chars(candidate['body']) < max(floor, int(original_chars * 0.8)):
        ctx.styled['reason'] = '风格改写明显缩短正文，未采用'
        return output
    ctx.styled.update(adopted=True, reason='风格改写通过数字、来源标记等静态检查，已采用；语义仍需人工复核')
    return candidate


def style_report(config, styled):
    """The material report's account of the style inputs and the style review."""
    if not config.get('example') and not styled and not config.get('style_note'):
        return ''
    card = '范文库生成的风格卡' if config.get('style_card') else '默认风格卡（范文库尚未生成风格卡）'
    lines = ['', '', '## 写作风格', f'风格卡：{card}',
             '范文骨架：' + (f"《{config['example']['title']}》" if config.get('example') else '无')]
    if config.get('style_note'):
        lines.append(config['style_note'])
    if styled:
        if styled.get('signals'):
            lines.append('参考信号：' + '；'.join(styled['signals'][:5]))
        if styled.get('scores'):
            lines.append('风格审核分数：' + '，'.join(f'{k} {v}' for k, v in styled['scores'].items()))
        lines.append('结果：' + styled.get('reason', ''))
    return '\n'.join(lines) + '\n'


def recent_shapes(db, limit=8, exclude=None):
    """Title, opening and headings of the latest drafts, for the writer and reviewer to avoid."""
    rows = db.rows('SELECT id, title, body FROM drafts WHERE cleared=0 AND id<>? ORDER BY id DESC LIMIT ?',
                   (exclude or 0, limit))
    return [draft_shape(r['title'], r['body']) for r in rows]


def unverified_count(report):
    """Facts an older draft still has unchecked: notes asking a person to check a source (no longer
    shown) plus verify items an automatic check skipped (switched off, failed or not adopted)."""
    notes = [n for n in _report_items(report, '审核提示')
             if is_verification_todo(n) and not n.startswith(EARLIER_REVIEW_NOTES)]
    count = len(merge_similar(notes))
    section = re.search(r'(?m)^## 自动查证\n待查证 (\d+) 项：[\s\S]*?^结果：(.*)$', report or '')
    if section and not section.group(2).startswith(('已找到', '未找到', '已由写作前联网研究覆盖')):
        count += int(section.group(1))
    return count
