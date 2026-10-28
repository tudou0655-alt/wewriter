"""Style and truth kept apart (T-029, customer feedback 2026-10-26).

The writer gets three things: a style card learned from articles the customer likes, one or two
of those articles (to learn the writing, never the facts), and a fact sheet assembled here from the
material the task already holds. Facts carry a credibility level and the strongest wording that
level allows; the program checks the article against those levels, and a style-only rewrite is
adopted only when its facts are unchanged.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .api import _NOT_FOUND, _plain, _sentences, fact_identifiers

LEVELS = {
    'official': '官方原件（政府网站、采购平台公告、正式文件）',
    'reported': '转述或行业材料（公众号、媒体、整理稿）',
    'unverified': '未核实（来源不明、研究写明未找到官方出处）',
}
STRENGTH = {
    'official': '可以直接陈述，可写“规定”“要求”',
    'reported': '首次出现时用一句话交代出处和适用范围（如“某省的验收要求”），之后正常陈述；'
                '不能写成“官方明确”“国家规定”，不能加重原文没有的后果或程度',
    'unverified': '只能写成“有材料称……，截至发稿未查到官方原件”，不进标题，不作为结论依据',
}
# Words that make a claim official, mandatory or consequential. A source below "official" may use
# them only where its own text does.
STRONG_TERMS = re.compile(r'官方明确|明确规定|国家规定|必须|一律|严禁|不得|直接驳回|直接退回|不予通过|驳回|拉黑|取消资格|'
                          r'追回|列入黑名单|异常项目|优先认可|一票否决')
_OFFICIAL_HOSTS = ('.gov.cn', 'gov.cn', 'ccgp.gov.cn', 'ggzy.gov.cn', 'npc.gov.cn')


def source_level(source):
    """Credibility level of one writing source: web pages on government hosts are official;
    WeChat articles and other web pages are reported."""
    sid = str(source.get('source_id', ''))
    if sid.startswith('W'):
        host = (urlsplit(str(source.get('url', ''))).hostname or '').lower()
        # A substring such as "gov.example.com" does not establish a government
        # origin. Only the registrable Chinese government namespace is trusted.
        return 'official' if host == 'gov.cn' or host.endswith('.gov.cn') else 'reported'
    return 'reported'


def _scope(source):
    """Where a fact comes from, as the article may say it: WeChat originals are 行业资料 without
    any account name or title (the customer's iron rule); web pages keep their site."""
    from .anonymity import is_wechat
    if str(source.get('source_id', '')).startswith('S') or is_wechat(source.get('url')):
        return '行业资料（转述，非官方；文中只能写“行业消息”“有材料显示”，不写任何公众号）'
    return f"网页《{str(source.get('title') or '')[:40]}》（{urlsplit(str(source.get('url', ''))).hostname or '未知网站'}）"


def fact_sheet(sources, limit=60):
    """Facts the article may use, assembled from what the task already holds (no model call):
    summary facts with their quotes and uncertainties of each original article, and the web
    research sentences with their [W] citations. Each fact: id, fact, quote, sources, level,
    scope and the strength of wording its level allows."""
    by_id = {s.get('source_id'): s for s in sources if isinstance(s, dict)}
    facts = []

    def add(text, quote, sids, level, scope):
        text = re.sub(r'\s+', ' ', str(text or '')).strip()
        if text and len(facts) < limit:
            # 'cite' is what goes into the article; the F number only identifies the fact.
            facts.append({'id': f'F{len(facts) + 1}', 'cite': ''.join(f'[{s}]' for s in sids),
                          'fact': text[:240], 'quote': str(quote or '')[:200],
                          'sources': sids, 'level': level, 'scope': scope, 'strength': STRENGTH[level]})
    for source in sources:
        if not isinstance(source, dict) or not str(source.get('source_id', '')).startswith('S'):
            continue
        sid = source['source_id']
        for part in source.get('analysis') or []:
            if not isinstance(part, dict):
                continue
            for item in part.get('facts') or []:
                if isinstance(item, dict):
                    add(item.get('claim'), item.get('quote'), [sid], 'reported', _scope(source))
            for doubt in part.get('uncertainties') or []:
                add(doubt, '', [sid], 'unverified', _scope(source) + '中的待核实内容')
        notes = source.get('web_research') or ''
        # Research places source marks after sentence punctuation ("事实。[W1]").
        # Keep those marks with their sentence; splitting at the punctuation first
        # would turn them into empty, uncitable facts.
        for match in re.finditer(r'[^。！？\n]+[。！？](?:[ \t]*\[W\d+\])*|[^。！？\n]+', notes):
            sentence = match.group()
            marks = list(dict.fromkeys(re.findall(r'\[(W\d+)\]', sentence)))
            plain = re.sub(r'\[W\d+\]', '', sentence).strip()
            if not plain:
                continue
            if _NOT_FOUND.search(plain):
                add(plain, '', marks, 'unverified', '联网研究：' + ('、'.join(marks) or '未找到官方出处'))
            elif marks:
                levels = [source_level(by_id[m]) for m in marks if m in by_id]
                level = 'official' if 'official' in levels else 'reported'
                scope = '；'.join(_scope(by_id[m]) for m in marks if m in by_id) or '联网研究'
                add(plain, '', marks, level, scope)
    return facts


def cite_facts(text, facts):
    """Replace fact numbers a model cited by mistake ([F3]) with that fact's source marks ([S1][W2]).
    A number that is not on the fact sheet is dropped: it is no source, and readers must never see it."""
    marks = {f['id']: f['cite'] for f in facts if f.get('cite')}
    text = re.sub(r'\[(F\d+)\]', lambda m: marks.get(m.group(1), ''), str(text or ''))
    return re.sub(r'(\[[SW]\d+\])\1+', r'\1', text)          # [S16][S16] -> [S16]


def strength_problems(text, sources):
    """Sentences whose wording is stronger than their sources allow: an official/mandatory/
    consequence term (STRONG_TERMS) in a sentence that cites no official source and whose cited
    sources never use that term themselves."""
    by_id = {s.get('source_id'): s for s in sources if isinstance(s, dict)}
    evidence = {}
    for sid, source in by_id.items():
        parts = [str(source.get('web_research') or '')]
        for part in source.get('analysis') or []:
            if isinstance(part, dict):
                parts.append(str(part.get('summary') or ''))
                parts += [str(f.get('claim', '')) + str(f.get('quote', '')) for f in part.get('facts') or []
                          if isinstance(f, dict)]
        evidence[sid] = _plain(' '.join(parts))
    found = []
    for sentence, _ in _sentences(text):
        terms = [m.group() for m in STRONG_TERMS.finditer(sentence)]
        if not terms:
            continue
        marks = re.findall(r'\[([SW]\d+)\]', sentence)
        if any(by_id.get(m) and source_level(by_id[m]) == 'official' for m in marks):
            continue
        backed = ''.join(evidence.get(m, '') for m in marks) if marks else ''.join(evidence.values())
        loose = [t for t in terms if _plain(t) not in backed]
        if loose:
            found.append('说法强度超过来源可信等级（' + '、'.join(dict.fromkeys(loose)) + '）：' + sentence[:80])
    return found


_NUMBER = re.compile(r'\d+(?:\.\d+)?')
_QUOTE = re.compile(r'[“「]([^”」\n]{6,})[”」]')


def fact_signature(title, body):
    """What a style-only rewrite must keep: numbers, key identifiers, source marks and quotes."""
    text = str(title or '') + '\n' + str(body or '')
    clean = re.sub(r'\[[SW]\d+\]', '', text)
    return {'numbers': set(_NUMBER.findall(clean)), 'identifiers': set(fact_identifiers(clean)),
            'marks': set(re.findall(r'\[([SW]\d+)\]', text)), 'quotes': {_plain(q) for q in _QUOTE.findall(text)}}


def same_facts(before, after):
    """Problems ([] when none) if a rewrite changed the facts: numbers or source marks added or
    dropped, new identifiers, or new quotations."""
    a, b = fact_signature(before.get('title'), before.get('body')), fact_signature(after.get('title'), after.get('body'))
    problems = []
    for key, label in (('numbers', '数字'), ('marks', '来源标记')):
        if a[key] != b[key]:
            gone, new = sorted(a[key] - b[key]), sorted(b[key] - a[key])
            problems.append(f'{label}变了' + (f'，删去 {"、".join(gone[:6])}' if gone else '') + (f'，新增 {"、".join(new[:6])}' if new else ''))
    if b['identifiers'] - a['identifiers']:
        problems.append('新增了原稿没有的标识：' + '、'.join(sorted(b['identifiers'] - a['identifiers'])[:6]))
    if b['quotes'] - a['quotes']:
        problems.append('新增了原稿没有的引语')
    # Sets lose the relationship between a number and its subject. When a
    # style-only pass rewrites several numeric clauses, keep each clause's
    # subject/number pair together or let a human revise the wording instead.
    def numeric_relations(article):
        text = str(article.get('body') or '')
        clauses = re.split(r'[。！？；;，,:：\n]+', re.sub(r'\[[SW]\d+\]', '', text))
        return [(_plain(re.sub(_NUMBER, '#', clause)), tuple(_NUMBER.findall(clause)))
                for clause in clauses if _NUMBER.search(clause)]
    if numeric_relations(before) != numeric_relations(after):
        problems.append('数字与对应事实的关系变了，需人工复核')
    # A negation or permission flip can change a fact without touching any
    # number or source mark. Be conservative about such wording changes.
    modality = re.compile(r'不可以|不能|不得|未|没有|禁止|不予|可以|能够|允许|必须|取消')
    before_terms = sorted(modality.findall(str(before.get('body') or '')))
    after_terms = sorted(modality.findall(str(after.get('body') or '')))
    if before_terms != after_terms:
        problems.append('否定或许可表述变了，需人工复核')
    return problems


# Fallback writing methods distilled from customer-approved drafts. Their facts and exact wording
# are never evidence for another article; a customer's valid style card replaces this fallback.
DEFAULT_STYLE_CARD = {
    'reader': '正在判断业务消息与自己是否有关的企业负责人和执行人员；先说清影响谁、要判断什么',
    'task': '围绕一个读者问题，把已知事实、适用限制和可做的下一步连成一条主线，而不是逐条复述材料',
    'title': '只抓本篇最有价值的一件事，点出受影响对象或具体变化；数字、期限、风险只在事实支持时写，不制造紧迫感',
    'opening': '前两三句从已知事实或读者当下面临的判断切入，讲清是什么、为何与读者有关；不套万能招呼或虚构现场',
    'hierarchy': '每节回答一个读者真正需要判断的问题，先给事实再解释影响，必要时给有条件的行动；材料少就少设章节',
    'voice': '像懂业务的同行解释一件事：具体、克制、顺口；专业词用大白话解释，适量用“你”，不写审稿报告腔',
    'ending': '留下读者现在最该确认或准备的一件事；它必须有资料依据，不强制另起“最后提醒”或清单',
    'taboos': ['不把范文或风格示例当作本篇事实', '不夸大来源、传播范围、数字和后果',
               '不为了生动编造人物、现场、对话或期限', '不为凑字数重复事实或添加没有依据的动作'],
    'examples': [
        {'fact': '某类项目申报须提交书面申请', 'good': '申报这类项目，书面申请要交上去',
         'bad': '没交书面申请，就会被列入黑名单'},
        {'fact': '申请材料须在受理期内提交', 'good': '材料要在受理期内交，别等受理结束再准备提交',
         'bad': '受理期一过，企业以后都不能再申请'},
    ],
}
CARD_FIELDS = ('reader', 'task', 'title', 'opening', 'hierarchy', 'voice', 'ending')


def valid_style_card(card):
    """A complete style card: the text fields, taboos and examples (vivid vs invented wording)."""
    if not isinstance(card, dict) or any(not isinstance(card.get(k), str) or not card[k].strip() for k in CARD_FIELDS):
        return False
    taboos, examples = card.get('taboos'), card.get('examples')
    if not isinstance(taboos, list) or not taboos or not all(isinstance(t, str) and t.strip() for t in taboos):
        return False
    return isinstance(examples, list) and bool(examples) and all(
        isinstance(e, dict) and all(isinstance(e.get(k), str) and e[k].strip() for k in ('fact', 'good', 'bad'))
        for e in examples)


_QUOTED = re.compile(r'[“"「]([^”"」]{2,80})[”"」]')


def _bare(text):
    return re.sub(r'[\W_]+', '', str(text or ''))


def forbidden_wording(texts):
    """The quoted wordings in "do not learn" notes and taboos, each as its fragments
    (“私自变更……不予通过” matches when every fragment appears)."""
    out = []
    for text in texts:
        for quoted in _QUOTED.findall(str(text or '')):
            parts = [_bare(p) for p in re.split(r'…+|\.{3,}', quoted)]
            parts = [p for p in parts if len(p) >= 2]
            if parts and parts not in out:
                out.append(parts)
    return out


def _forbidden(text, wording):
    bare = _bare(text)
    return bool(bare) and any(all(p in bare for p in parts) for parts in wording)


def clean_style_card(card, avoid=()):
    """Remove positive examples that use a forbidden wording (from the examples' "do not learn"
    notes, the card's own taboos and its bad examples). Returns (card, fixes); a card left without
    examples is invalid and must be regenerated."""
    if not isinstance(card, dict):
        return card, []
    card = json.loads(json.dumps(card, ensure_ascii=False))
    wording = forbidden_wording(list(avoid) + list(card.get('taboos') or [])
                                + [f'“{e.get("bad", "")}”' for e in card.get('examples') or [] if isinstance(e, dict)])
    fixes = []
    for key in CARD_FIELDS:
        text = card.get(key)
        if not isinstance(text, str):
            continue
        cleaned = _QUOTED.sub(lambda m: '' if _forbidden(m.group(1), wording) else m.group(), text)
        if cleaned != text:
            cleaned = re.sub(r'[、，,]+(?=[）)。；，,]|$)', '', re.sub(r'(?<=[（(如])[、，,]+', '', cleaned))
            cleaned = re.sub(r'[（(]如?[）)]|[，,]?如(?=[。；]|$)', '', cleaned).strip()
            fixes.append(f'{key}：删去被禁止的举例')
            card[key] = cleaned
    kept = []
    for example in card.get('examples') or []:
        if isinstance(example, dict) and _forbidden(example.get('good', ''), wording):
            fixes.append(f'删去示例：{str(example.get("good", ""))[:30]}')
        else:
            kept.append(example)
    card['examples'] = kept
    return card, fixes


# Input budget (characters) per writing call: the style card is distilled from the whole library,
# and at most one example skeleton travels with it, so the library's size never grows the input.
CARD_MAX = 800
SKELETON_MAX = 1000
STYLE_BUDGET = 2500


def card_text(card, limit=CARD_MAX):
    """The style card as compact text for the prompt, at most `limit` characters."""
    card = card if valid_style_card(card) else DEFAULT_STYLE_CARD
    names = {'reader': '读者', 'task': '文章帮读者办成', 'title': '标题', 'opening': '开头', 'hierarchy': '层级',
             'voice': '口吻', 'ending': '收尾'}
    lines = [f'{names[k]}：{card[k].strip()}' for k in CARD_FIELDS]
    lines.append('禁忌：' + '；'.join(t.strip() for t in card['taboos']))
    lines += [f"示例｜事实：{e['fact']}｜可以写：{e['good']}｜不能写：{e['bad']}" for e in card['examples']]
    text = '\n'.join(lines)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def skeleton(title, body, avoid=''):
    """An example article reduced to what shows its writing: title, opening, headings and one or
    two representative paragraphs (lists first), at most SKELETON_MAX characters."""
    blocks = [b.strip() for b in re.split(r'\n{2,}', str(body or '')) if b.strip()]
    heads = [b for b in blocks if b.startswith('#')]
    paras = [b for b in blocks if not b.startswith('#')]
    opening = paras[0] if paras else ''
    listy = [p for p in paras[1:] if '\n' in p or re.match(r'^(?:[-*]|\d+[.、])', p)]
    sample = (listy or paras[1:])[:2]
    parts = ['标题：' + str(title or '').strip(), '开头：' + opening[:240],
             '小标题：' + ' / '.join(re.sub(r'^#+\s*', '', h) for h in heads)[:300]]
    parts += ['代表段落：' + p[:220] for p in sample]
    if avoid:
        parts.append('不要学：' + str(avoid).strip()[:200])
    text = '\n'.join(parts)
    return text if len(text) <= SKELETON_MAX else text[:SKELETON_MAX - 1] + '…'


def choose_example(examples, title):
    """The good example closest to this article (by title and headings), then by score."""
    from difflib import SequenceMatcher
    good = [e for e in examples or [] if e.get('kind', 'good') == 'good' and e.get('skeleton')]
    if not good:
        return None
    target = _plain(title)
    return max(good, key=lambda e: (round(SequenceMatcher(None, target, _plain(e['skeleton'][:400])).ratio(), 2),
                                    e.get('score') or 0, -int(e.get('id') or 0)))


def style_block(card, example=None, budget=STYLE_BUDGET):
    """(text, note) of the style part of the writing prompt; note says what the budget cut."""
    card_part = card_text(card)
    example_part = example.get('skeleton', '') if example else ''
    note = ''
    text = '【风格卡】\n' + card_part + ('\n\n【范文骨架（只学写法，不学其中的事实和数字）】\n' + example_part if example_part else '')
    if len(text) > budget:
        room = max(0, budget - len('【风格卡】\n' + card_part) - 40)
        cut = example_part[:room]
        text = '【风格卡】\n' + card_part + ('\n\n【范文骨架（只学写法，不学其中的事实和数字）】\n' + cut + '…' if cut else '')
        text = text[:budget]
        note = f'风格内容超过预算 {budget} 字，已截短范文骨架（原 {len(example_part)} 字，保留 {len(cut)} 字）'
    return text, note


_META = re.compile(r'^<!--\s*(score|avoid|kind)\s*:\s*(.*?)\s*-->\s*$', re.M)


def read_example_file(path):
    """(title, body, meta) of an example article from a .docx, .md or .txt file. A Markdown file
    may start with <!-- score: 80 -->, <!-- avoid: … --> and <!-- kind: good|bad --> lines."""
    path = Path(path)
    if path.suffix.lower() == '.docx':
        import docx
        document = docx.Document(str(path))
        lines = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name or '') if paragraph.style is not None else ''
            level = re.search(r'Heading (\d)', style)
            lines.append(('#' * int(level.group(1)) + ' ' if level else '') + text)
        text = '\n\n'.join(lines)
    elif path.suffix.lower() in ('.md', '.txt'):
        text = path.read_text(encoding='utf-8-sig')
    else:
        raise ValueError('范文只支持 .docx、.md 或 .txt 文件')
    meta = {key: value for key, value in _META.findall(text)}
    title, body = split_title(_META.sub('', text).strip(), path.stem)
    return title, body, meta


def split_title(text, fallback=''):
    """(title, body): a leading Markdown heading or first short line is the title."""
    text = str(text or '').strip()
    first, _, rest = text.partition('\n')
    if first.startswith('#') or (rest and len(first) <= 60):
        return first.lstrip('# ').strip(), rest.strip()
    return str(fallback or '')[:60], text
