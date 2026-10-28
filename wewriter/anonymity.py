"""No other WeChat account may appear in an article (customer's iron rule, 2026-10-26).

The customer runs WeChat accounts too: naming another account, its article titles or its authors,
or even saying "a WeChat account reported", exposes where the material came from. This module
builds the block list from the task's sources and the managed accounts, finds hits in a text, and
as a last resort rewrites or removes the sentences that still hit after the model's own rewrite.
Standalone (no imports from api) so the draft checks in api can use it.
"""
import re
import unicodedata
from urllib.parse import urlsplit

WECHAT_HOSTS = ('mp.weixin.qq.com', 'weixin.qq.com')
# Ways of saying "a WeChat account / post said …" without naming one. Generic words like 文件原文
# stay allowed; only wording that points at another account or its post is blocked.
GENERIC = re.compile(r'(?:微信)?公众号|推文|原文作者|据某号|小编|原文(?:称|提到|写道|指出|表示)|这篇(?:原文|推送)')
# A shared policy name is not an article title. Only a near-complete headline,
# or a quoted fragment explicitly presented as an article title, identifies it.
MIN_TITLE_QUOTE = 10
NEAR_TITLE_MIN = 16
NEAR_TITLE_RATIO = 0.85
NEUTRAL = '有行业消息称'
RULE = ('铁律：成稿里绝对不能出现任何公众号——不写公众号名称、作者、其他公众号文章的完整标题或明显点名该文章，'
        '不写“某公众号、有公众号、微信公众号、推文、原文作者、原文称、据某号”这类说法。'
        '公共政策名称和主题词可以正常使用；转述类事实只交代最终来源，例如“某省发改委文件”“行业消息”“公开信息显示”。')


def _plain(text):
    return ''.join(c.casefold() for c in str(text)
                   if not c.isspace() and not unicodedata.category(c).startswith(('P', 'S')))


def is_wechat(url):
    host = (urlsplit(str(url or '')).hostname or '').lower()
    return host.endswith(WECHAT_HOSTS)


def block_list(sources, account_names=()):
    """{'names': [...], 'titles': [...]}: managed account names, the sources' account names, and
    the titles of WeChat articles among the sources (originals and WeChat pages found online)."""
    names, titles = [], []
    for name in account_names:
        if str(name or '').strip():
            names.append(str(name).strip())
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        wechat = str(source.get('source_id', '')).startswith('S') or is_wechat(source.get('url'))
        if wechat:
            if source.get('account_name'):
                names.append(str(source['account_name']).strip())
            if source.get('title'):
                titles.append(str(source['title']).strip())
            if source.get('author'):
                names.append(str(source['author']).strip())
    names = [n for n in dict.fromkeys(names) if len(_plain(n)) >= 2]
    titles = [t for t in dict.fromkeys(titles) if len(_plain(t)) >= 4]
    return {'names': names, 'titles': titles}


def _title_hit(sentence, title):
    t = _plain(title)
    if not t:
        return ''
    from difflib import SequenceMatcher
    for line in str(sentence).splitlines():
        s = _plain(line)
        if len(t) >= 8 and s == t:
            return title
        if len(t) >= NEAR_TITLE_MIN:
            match = SequenceMatcher(None, t, s, autojunk=False).find_longest_match(0, len(t), 0, len(s))
            if match.size >= max(NEAR_TITLE_MIN, int(len(t) * NEAR_TITLE_RATIO + 0.999)):
                return t[match.a:match.a + match.size]
        # A shorter shared phrase matters only when the draft actually calls
        # it an article/post title. Document names and ordinary topic wording
        # do not count as references to another account's article.
        for quote in re.finditer(r'[《“]([^》”]{4,120})[》”]', line):
            context = line[max(0, quote.start() - 18):quote.start()] + line[quote.end():quote.end() + 12]
            if not re.search(r'文章|推文|原文|公众号|一篇|题为|标题|报道', context):
                continue
            phrase = _plain(quote.group(1))
            match = SequenceMatcher(None, t, phrase, autojunk=False).find_longest_match(0, len(t), 0, len(phrase))
            if match.size >= min(MIN_TITLE_QUOTE, len(t)):
                return t[match.a:match.a + match.size]
    return ''


def hits(text, blocked):
    """What in the text gives the source away: [(kind, matched)], kind 名称 / 标题 / 说法."""
    text = str(text or '')
    found = []
    plain = _plain(text)
    for name in blocked.get('names', []):
        if _plain(name) and _plain(name) in plain:
            found.append(('名称', name))
    for title in blocked.get('titles', []):
        part = _title_hit(text, title)
        if part:
            found.append(('标题', part))
    found += [('说法', m.group()) for m in GENERIC.finditer(text)]
    return list(dict.fromkeys(found))


_SENTENCE = re.compile(r'[^。！？!?\n]+[。！？!?]?(?:\[[SW]\d+\])*')
# "有公众号“X”在一篇题为《Y》的文章中称……" and its variants: the attribution part before the claim.
_ATTRIBUTION = re.compile(
    r'^(?:(?:另)?有|据|某|一个|一家|该)?(?:微信)?(?:公众号|推文|自媒体)?[^，。]{0,30}?'
    r'(?:在一篇[^，。]{0,40}?(?:文章|推文)中|的(?:文章|推文)(?:中)?|发文|撰文)?'
    r'(?:称|提到|指出|表示|写道|介绍|透露)[，：:]?')


def scrub(text, blocked):
    """Last resort after the model's rewrite still hits: rewrite each sentence that hits into a
    neutral attribution, or drop it when that is not enough. Numbers and source marks in kept
    sentences stay as they were. Returns (text, fixes)."""
    fixes = []

    # Titles first: a quoted post title may itself contain 。！？ and would split the sentence.
    # Document names (《……行动方案》) stay; only blocked titles and "an article titled 《…》" go.
    def drop_title(match):
        if match.group('ctx') or any(_title_hit(match.group('t'), t) for t in blocked.get('titles', [])):
            fixes.append(('删除标题', match.group()[:80]))
            return ''
        return match.group()
    text = re.sub(r'(?P<ctx>(?:在)?一篇(?:题为|名为|标题为)?)?《(?P<t>[^》]*)》(?:的(?:文章|推文))?(?:中)?',
                  drop_title, str(text or ''))

    def fix(match):
        sentence = match.group()
        if not hits(sentence, blocked):
            return sentence
        claim = _ATTRIBUTION.sub('', sentence.strip(), count=1)
        candidate = NEUTRAL + '，' + claim.lstrip('，,：: ') if claim != sentence.strip() else claim
        if not hits(candidate, blocked) and len(_plain(claim)) >= 6:
            fixes.append(('改写', sentence.strip()[:80]))
            return candidate
        fixes.append(('删除', sentence.strip()[:80]))
        return ''
    out = '\n'.join(_SENTENCE.sub(fix, line) for line in str(text or '').split('\n'))
    out = re.sub(r'\n{3,}', '\n\n', out).strip()
    return out, fixes
