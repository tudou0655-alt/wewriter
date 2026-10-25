"""Article-quality benchmark: offline rule checks plus an optional, explicitly authorised model run.

Offline (default; no network, no credentials):

    .\\.venv\\Scripts\\python.exe scripts\\evaluate_article_quality.py check --baseline 基准.md \\
        [--candidate 候选.md ...] [--package tests\\fixtures\\article_quality\\package.json] [--out DIR]

Writes ``rules.json`` and a blank human scoring sheet ``scoring.md`` to ``--out`` (default
``.local-data\\article-quality``, ignored by Git). Rule checks only measure report-style
structure and mobile layout signals; they never score an article as passing.

Paid model comparison (only after the user authorises API calls):

    ... generate --package PACKAGE --provider deepseek --runs 3 --allow-paid-calls [--baseline-rev 2c94c2a]
        [--max-calls N]
    ... reveal --run .local-data\\article-quality\\run-...   (only after every sample is scored)

Runs write + review with the current prompts (and, with --baseline-rev, the prompts of that Git
revision) on the same fixed package and full effective configuration, in random order. Samples get
neutral ids; the id -> version mapping, the hash of the prompts actually sent, durations and call
counts go to a sealed key file. The run stops at the HTTP request limit (every attempt including
Transport retries is charged before it is sent; default samples x 3), on a fatal
key/balance/permission error, or after two failed samples in a row. Keys are read through the
application's DPAPI vault and are never printed or written; error text is redacted.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / '.local-data' / 'article-quality'
CHINA_TZ = timezone(timedelta(hours=8))
# Evidence-class vocabulary that the old prompts used and drafts echoed as structure.
REPORT_TERMS = ('可核实', '可证事实', '已核实事实', '证据与局限', '证据边界', '证据类型', '合理推断', '编辑判断',
                '经验性观察', '核心变化', '价值点', '数据空白', '表述建议', '现象描述', '可能解释')
META_PATTERN = re.compile(r'本文(?:把|将|区分|分开)|文中区分|标注了|来源标记|证据来源|每一?相关句|本稿(?:已|未)|'
                          r'可直接证实的结论|以上均有')
REFERENCE_HEADING = re.compile(r'(?m)^#{1,6}\s*(?:参考资料网站|参考来源|参考资料|资料来源|引用来源)\s*$')
SCORE_DIMENSIONS = (('自然表达（无报告腔）', 20), ('叙事连贯', 20), ('信息价值', 20), ('风格匹配', 15),
                    ('事实与引用可靠', 15), ('手机阅读', 10))
VETO_ITEMS = ('编造事实、数字或人物经历', '伪造引语', '来源不支持关键结论')


def article_body(text):
    """Drop a BOM, the title line and the exported website list; keep the article itself."""
    text = text.lstrip('﻿')
    match = REFERENCE_HEADING.search(text)
    if match:
        text = text[:match.start()]
    lines = text.strip().splitlines()
    if lines and lines[0].startswith('# '):
        lines = lines[1:]
    return '\n'.join(lines).strip()


def rule_checks(text):
    """Deterministic structure signals. Lower report_* / meta / list numbers are better; none
    of them proves an article is good, and no threshold here means "passed"."""
    body = article_body(text)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    list_lines = [line for line in lines if re.match(r'^(?:[-*+]|\d+[.、])\s', line)]
    label_items = [line for line in list_lines if re.match(r'^(?:[-*+]|\d+[.、])\s*[^：:\s]{1,14}[：:]', line)]
    headings = [line for line in lines if line.startswith('#') or (
        len(line) <= 30 and not re.search(r'[。！？；]', line) and line not in list_lines)]
    report_headings = [h for h in headings if any(term in h for term in REPORT_TERMS)]
    report_labels = [line for line in label_items if any(term in line.split('：')[0] for term in REPORT_TERMS)]
    chars = len(re.sub(r'\s', '', body))
    text_paragraphs = [p for p in paragraphs if p not in headings]
    long_paragraphs = [p for p in text_paragraphs if len(re.sub(r'\s', '', p)) > 150]
    hedges = len(re.findall(r'可能|仍需|有待|尚需|不能直接|无法用', body))
    return {
        'characters': chars,
        'paragraphs': len(text_paragraphs),
        'headings': len(headings),
        'report_style_headings': report_headings,
        'report_style_labels': len(report_labels),
        'meta_statements': [m.group() for m in META_PATTERN.finditer(body)],
        'list_line_ratio': round(len(list_lines) / len(lines), 2) if lines else 0,
        'labelled_list_items': len(label_items),
        'long_paragraphs_over_150_chars': len(long_paragraphs),
        'max_paragraph_chars': max((len(re.sub(r'\s', '', p)) for p in text_paragraphs), default=0),
        'hedges_per_1000_chars': round(hedges * 1000 / chars, 1) if chars else 0,
        'internal_citation_marks': len(re.findall(r'\[[SW]\d+\]', body)),
    }


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_package(path):
    """A fixed input package: writer sources (with analysis, style_reference, web_research) + config."""
    package = json.loads(Path(path).read_text(encoding='utf-8'))
    sources = package.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('资料包缺少 sources')
    ids = [s.get('source_id') for s in sources]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[SW]\d+', str(i)) for i in ids):
        raise ValueError('资料包 source_id 重复或格式无效')
    marks = set(re.findall(r'\[([SW]\d+)\]', json.dumps(sources, ensure_ascii=False)))
    if not marks <= set(ids):
        raise ValueError('资料包引用了不存在的来源：' + '、'.join(sorted(marks - set(ids))))
    if not any(isinstance(s.get('style_reference'), dict) for s in sources):
        raise ValueError('资料包缺少 style_reference')
    for source in sources:
        if source['source_id'].startswith('S'):
            for part in source.get('analysis', []):
                for fact in part.get('facts', []):
                    if not fact.get('quote'):
                        raise ValueError('资料包事实缺少原文证据')
    if not isinstance(package.get('config'), dict):
        raise ValueError('资料包缺少 config')
    return package


def scoring_sheet(entries):
    lines = ['# 文章质量人工评分表', '',
             '评分由人工填写，规则检查结果只作参考；任一否决项成立时该稿不得验收。', '',
             '| 文件 | SHA-256 | ' + ' | '.join(f'{name}（{full}）' for name, full in SCORE_DIMENSIONS)
             + ' | 总分（100） | 否决项 | 评审人 | 理由 |',
             '|---|---|' + '---|' * (len(SCORE_DIMENSIONS) + 4)]
    for name, digest in entries:
        lines.append(f'| {name} | {digest[:12]} |' + ' |' * (len(SCORE_DIMENSIONS) + 4))
    lines += ['', '否决项：' + '；'.join(VETO_ITEMS) + '。',
              '建议门槛：候选稿总分 ≥ 75，且比基准高至少 20 分；以多次运行的人工盲评为准。']
    return '\n'.join(lines) + '\n'


def run_check(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {'generated_at': datetime.now(CHINA_TZ).isoformat(timespec='seconds'), 'files': []}
    entries = []
    for role, paths in (('baseline', [args.baseline]), ('candidate', args.candidate or [])):
        for path in paths:
            path = Path(path)
            digest = sha256(path)
            if role == 'baseline' and args.baseline_sha256 and digest != args.baseline_sha256:
                raise ValueError('基准文件哈希不一致，基准可能已被修改')
            report['files'].append({'role': role, 'name': path.name, 'sha256': digest,
                                    'rules': rule_checks(path.read_text(encoding='utf-8'))})
            entries.append((path.name, digest))
    if args.package:
        package = load_package(args.package)
        report['package'] = {'name': Path(args.package).name, 'sha256': sha256(args.package),
                             'sources': len(package['sources']), 'fictional': bool(package.get('fictional'))}
    (out / 'rules.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'scoring.md').write_text(scoring_sheet(entries), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _load_api(revision):
    """Import wewriter/api.py from the working tree, a Git revision, or ``file:<path>`` (a saved
    snapshot of an uncommitted prompt version) for the baseline."""
    if not revision:
        sys.path.insert(0, str(ROOT))
        from wewriter import api
        return api
    if revision.startswith('file:'):
        source = Path(revision[5:]).read_bytes()
        revision = 'file-' + hashlib.sha256(source).hexdigest()[:12]
    else:
        source = subprocess.run(['git', 'show', revision + ':wewriter/api.py'], cwd=ROOT, capture_output=True,
                                check=True).stdout
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='api-' + revision + '-', dir=DEFAULT_OUT))
    (directory / 'api_baseline.py').write_bytes(source)
    spec = importlib.util.spec_from_file_location('api_baseline_' + revision, directory / 'api_baseline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Normal model requests per sample: write (1) + review (1); a format repair adds one more.
# The budget counts every actual HTTP request, including Transport's own 429/502/503 retries,
# because a retried request may still have been processed or billed by the provider.
CALLS_PER_SAMPLE = 3
CONSECUTIVE_FAILURE_STOP = 2


class CallLimitReached(RuntimeError):
    pass


def _spend(budget):
    """Reserve one HTTP request from the run-wide budget, before it is sent."""
    if budget['used'] >= budget['max']:
        raise CallLimitReached('已达到本次授权的 HTTP 请求上限 %d 次，停止生成' % budget['max'])
    budget['used'] += 1


class _BudgetOpener:
    """Wraps Transport.opener, so each HTTP attempt (retries included) is charged before it is sent."""

    def __init__(self, opener, budget, counter):
        self.opener, self.budget, self.counter = opener, budget, counter

    def open(self, *args, **kwargs):
        _spend(self.budget)
        self.counter['http'] += 1
        return self.opener.open(*args, **kwargs)


class _CountingTransport:
    """Keeps the prompts sent and charges the shared budget for every HTTP request.

    Real Transports (current and baseline revisions alike) send through ``self.opener``; it is
    wrapped so retries cannot bypass the budget. A transport without an opener (test doubles) is
    charged once per model request instead."""

    def __init__(self, transport, budget):
        self.transport, self.budget, self.calls, self.prompts = transport, budget, 0, []
        self.counter = {'http': 0}
        self.metered = hasattr(transport, 'opener')
        if self.metered:
            transport.opener = _BudgetOpener(transport.opener, budget, self.counter)

    def request(self, url, key, payload=None, **kwargs):
        if not self.metered:
            _spend(self.budget)
            self.counter['http'] += 1
        elif self.budget['used'] >= self.budget['max']:
            raise CallLimitReached('已达到本次授权的 HTTP 请求上限 %d 次，停止生成' % self.budget['max'])
        self.calls += 1
        self.prompts.append(_sent_prompt(payload))
        return self.transport.request(url, key, payload, **kwargs)


def _sent_prompt(payload):
    """System text and the full request body actually sent (both providers)."""
    payload = payload or {}
    system = payload.get('instructions')
    if system is None:
        messages = payload.get('messages') or [{}]
        system = messages[0].get('content', '')
    return {'system': str(system), 'request': json.dumps(payload, ensure_ascii=False, sort_keys=True)}


def _digest(parts):
    return hashlib.sha256('\n--\n'.join(parts).encode('utf-8')).hexdigest()


def _redact(text):
    sys.path.insert(0, str(ROOT))
    from wewriter.logs import redact
    return redact(text)


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def generate_samples(package, config, provider, key, variants, runs, max_calls, out,
                     load_api=None, rng=None):
    """Generate blind samples and return the public run summary.

    out/samples/<neutral id>.md   articles only, generated in random order
    out/scoring.md                blank sheet listing neutral ids only
    out/sealed/blind-key.json     id -> variant, revision, prompt hashes, rules, notes; open after scoring
    out/run.json                  package hash, full effective config, call budget, stop reason
    Stops at the call budget, on a fatal key/balance/permission error, or after
    CONSECUTIVE_FAILURE_STOP failed samples in a row. Error text and notes are redacted."""
    import random
    import secrets as token_source
    if not isinstance(max_calls, int) or max_calls <= 0:
        raise ValueError('调用上限必须是正整数')
    load_api = load_api or _load_api
    rng = rng or random.SystemRandom()
    out = Path(out)
    (out / 'samples').mkdir(parents=True, exist_ok=True)
    (out / 'sealed').mkdir(exist_ok=True)
    tasks = [(label, revision, run) for label, revision in variants for run in range(1, runs + 1)]
    rng.shuffle(tasks)
    modules = {revision: load_api(revision) for _, revision in variants}
    settings = config['providers'][provider]
    budget = {'used': 0, 'max': max_calls}
    sealed, failures_in_row, stop_reason = {}, 0, '全部完成'
    for label, revision, run in tasks:
        api = modules[revision]
        sample = 'sample-' + token_source.token_hex(3)
        while sample in sealed:
            sample = 'sample-' + token_source.token_hex(3)
        transport = _CountingTransport(api.Transport(), budget)
        model = api.Model(provider, settings, key, transport)
        started = time.monotonic()
        record = {'variant': label, 'revision': revision or 'working-tree', 'run': run,
                  'provider': provider, 'model': settings['model']}
        fatal = False
        try:
            draft = model.write(_copy(package['sources']), _copy(config))
            # The first draft and the review's sentence-level checks are sealed with the version
            # mapping, so after unblinding one can see whether the review actually corrected errors.
            record['draft'] = {'title': draft.get('title', ''), 'body': draft.get('body', ''),
                               'review_notes': [_redact(n) for n in draft.get('review_notes', [])]}
            final = model.review(dict(draft), _copy(package['sources']), _copy(config))
            record['quality_notes'] = [_redact(n) for n in final.pop('quality_notes', [])]
            text = '# ' + final['title'] + '\n\n' + final['body']
            (out / 'samples' / (sample + '.md')).write_text(text, encoding='utf-8')
            record.update(file='samples/' + sample + '.md', rules=rule_checks(text),
                          review_notes=[_redact(n) for n in final.get('review_notes', [])])
            failures_in_row = 0
        except Exception as exc:  # failed samples are kept, redacted
            record['error'] = _redact(type(exc).__name__ + '：' + str(exc))[:300]
            failures_in_row += 1
            fatal = isinstance(exc, CallLimitReached) or bool(getattr(exc, 'fatal', False))
        record.update(seconds=round(time.monotonic() - started, 1), api_calls=transport.calls,
                      http_requests=transport.counter['http'],
                      prompt_sha256=_digest(p['system'] for p in transport.prompts),
                      request_sha256=_digest(p['request'] for p in transport.prompts))
        sealed[sample] = record
        if fatal or failures_in_row >= CONSECUTIVE_FAILURE_STOP:
            stop_reason = ('已停止：' + record['error'] if fatal else
                           '已停止：连续 %d 个样本失败' % failures_in_row)
            break
    done = sorted(s for s, r in sealed.items() if r.get('file'))
    summary = {'generated_at': datetime.now(CHINA_TZ).isoformat(timespec='seconds'),
               'package_sha256': package.get('_sha256', ''), 'config': config, 'provider': provider,
               'model': settings['model'], 'max_calls': max_calls, 'calls_used': budget['used'],
               'planned_samples': len(tasks), 'samples': done, 'failed_samples': len(sealed) - len(done),
               'stop_reason': stop_reason}
    (out / 'sealed' / 'blind-key.json').write_text(json.dumps(sealed, ensure_ascii=False, indent=2) + '\n',
                                                     encoding='utf-8')
    (out / 'run.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'scoring.md').write_text(scoring_sheet([(s + '.md', sha256(out / 'samples' / (s + '.md')))
                                                   for s in done]), encoding='utf-8')
    return summary


def run_generate(args):
    sys.path.insert(0, str(ROOT))
    from wewriter.config import defaults
    package = load_package(args.package)
    package['_sha256'] = sha256(args.package)
    config = defaults()
    config.update(package['config'])
    variants = [('current', None)] + ([('baseline', args.baseline_rev)] if args.baseline_rev else [])
    planned = len(variants) * args.runs
    max_calls = planned * CALLS_PER_SAMPLE if args.max_calls is None else args.max_calls
    if max_calls <= 0:
        print('[拒绝] --max-calls 必须是正整数')
        return 2
    print('计划生成 %d 个样本：正常约 %d 次模型请求；HTTP 请求（含重试）硬上限 %d 次；'
          '遇到 Key/余额/权限错误、达到上限或连续 %d 个样本失败即停止'
          % (planned, planned * 2, max_calls, CONSECUTIVE_FAILURE_STOP))
    if not args.allow_paid_calls:
        print('[拒绝] 生成对照稿会调用付费模型接口；确认获得授权后加 --allow-paid-calls')
        return 2
    from wewriter.secrets import Vault
    data_dir = Path(args.data_dir) if args.data_dir else Path.home() / 'AppData' / 'Local' / 'WeWriter'
    key = Vault(data_dir / 'keys.dpapi.json').get(args.provider)
    if not key:
        print('[失败] 未在本机密钥库中找到所选模型的 Key')
        return 1
    out = Path(args.out) / datetime.now(CHINA_TZ).strftime('run-%Y%m%d-%H%M%S')
    summary = generate_samples(package, config, args.provider, key, variants, args.runs, max_calls, out)
    print('已生成 %d 个样本（失败 %d 个，调用 %d/%d 次，%s）。请按 scoring.md 盲评，评分完成后再运行 reveal。'
          % (len(summary['samples']), summary['failed_samples'], summary['calls_used'], max_calls,
             summary['stop_reason']))
    return 0


def run_reveal(args):
    """Unblind only after every listed sample has a total score."""
    run = Path(args.run)
    header = [cell.strip() for cell in next(line for line in (run / 'scoring.md').read_text(
        encoding='utf-8').splitlines() if line.startswith('| 文件')).split('|')]
    total = next(i for i, cell in enumerate(header) if cell.startswith('总分'))
    rows = [[cell.strip() for cell in line.split('|')] for line in
            (run / 'scoring.md').read_text(encoding='utf-8').splitlines() if line.startswith('| sample-')]
    unscored = [row[1] for row in rows if not row[total]]
    if unscored and not args.force:
        print('[拒绝] 以下样本还没有总分，评分完成后再揭盲：' + '、'.join(unscored))
        return 2
    key = json.loads((run / 'sealed' / 'blind-key.json').read_text(encoding='utf-8'))
    for sample, record in sorted(key.items()):
        print('%s：%s（%s，第 %d 次）%s' % (sample, record['variant'], record['revision'], record['run'],
                                        '，失败' if record.get('error') else ''))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    parser = argparse.ArgumentParser(description='文章质量基准：离线规则检查与经授权的模型对照')
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='离线规则检查，生成人工评分表')
    check.add_argument('--baseline', required=True)
    check.add_argument('--baseline-sha256')
    check.add_argument('--candidate', action='append')
    check.add_argument('--package')
    check.add_argument('--out', default=str(DEFAULT_OUT))
    generate = commands.add_parser('generate', help='调用模型生成对照稿（付费，需授权）')
    generate.add_argument('--package', required=True)
    generate.add_argument('--provider', choices=('openai', 'deepseek'), default='deepseek')
    generate.add_argument('--runs', type=int, default=3)
    generate.add_argument('--baseline-rev', help='对照版本：Git 提交，或 file:<api.py 快照路径>')
    generate.add_argument('--data-dir')
    generate.add_argument('--allow-paid-calls', action='store_true')
    generate.add_argument('--max-calls', type=int,
                          help='本次授权的 HTTP 请求硬上限（含重试，正整数），默认 样本数 x %d' % CALLS_PER_SAMPLE)
    generate.add_argument('--out', default=str(DEFAULT_OUT))
    reveal = commands.add_parser('reveal', help='评分完成后揭盲')
    reveal.add_argument('--run', required=True)
    reveal.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    return {'check': run_check, 'generate': run_generate, 'reveal': run_reveal}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
