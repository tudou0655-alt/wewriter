from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

DEFAULTS = {
    "writing_mode": "separate_web", "draft_count": 3,
    "search_url": "https://api.deepseek.com/v1/tools/search",
    "search_provider": "deepseek", "search_model": "deepseek-flash",
    "comments_enabled": True, "allow_recollect_cleared": False,
    # Optional cap on paid request attempts (including retries) in one task.
    "max_paid_requests": None,
    "quality_review_enabled": True, "draft_first_mode": True, "comment_limit": 30,
    # After the review, search once for official originals of claims it could not source.
    "auto_verify": True,
    # T-029: the style card distilled from the example library (None: the built-in default card),
    # and whether a style review may rewrite the wording of a draft.
    "style_card": None, "style_review_enabled": True,
    "selection_mode": "hot", "min_reads": 0,
    "source_provider": "weread",
    "schedule_enabled": False, "schedule_time": "09:00", "timezone": "Asia/Shanghai",
    "schedule_start": "", "article_limit": 10, "lookback_hours": 24,
    # 'today': the China-time calendar day of the task; 'rolling': last lookback_hours.
    "lookback_mode": "rolling",
    "export_format": "docx", "export_dir": "",
    "per_account_limit": 24, "min_sources": 2, "target_words": 1500,
    "topic": "", "style": "面向企业老板和申报负责人：像懂行的朋友当面讲事，短句、说人话，结论先行；用具体数字和例子讲清政策变化和该做什么，不写公文腔和论文腔，不夸张营销。",
    "style_preset": "政策申报实务",
    "summary_provider": "deepseek", "writer_provider": "deepseek",
    "summary_model": "deepseek-flash",
    # 'skip': articles whose text is under 100 characters are skipped (next
    # ranked article takes the slot). 'vision': send images to OpenAI.
    "image_articles": "skip",
    "providers": {
        "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5-mini"},
        "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
        "bailian": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.8-flash"},
    },
}


LEGACY_DEFAULT_STYLE = '面向普通读者，清晰、客观、有逻辑，避免夸张营销和空泛套话。'
# The 政策申报实务 preset text before the 2026-10-26 readability change (T-027).
PREVIOUS_POLICY_STYLE = '面向企业决策者和申报负责人：专业但好懂，结论先行，像懂行的同行讲清政策变化和申报要点；用数据和往年对比说话，避免公文套话和夸张营销。'


def defaults():
    return deepcopy(DEFAULTS)


def migrate(config):
    result = defaults()
    result.update(deepcopy(config))
    if 'search_provider' not in config:
        result['search_provider'] = 'openai' if 'search_model' in config else 'deepseek'
    result['source_provider'] = 'weread'
    providers = result.get('providers', {})
    result['providers'] = {name: deepcopy(providers.get(name, DEFAULTS['providers'][name]))
                           for name in ('openai', 'deepseek', 'bailian')}
    for field in ('summary_provider', 'writer_provider'):
        if result[field] == 'qwen':
            result[field] = 'openai'
    if 'summary_model' not in config:
        name = result.get('summary_provider')
        result['summary_model'] = result['providers'].get(name, {}).get('model', '')
    if result['search_provider'] != 'bailian' and 'dashscope' in result.get('search_url', ''):
        result['search_url'] = 'https://api.openai.com/v1/responses' if result['search_provider'] == 'openai' else DEFAULTS['search_url']
    # v0.10.3 removed title-based promotion filtering. Drop legacy settings so
    # old databases cannot keep filtering articles invisibly.
    result.pop('skip_promotions', None)
    result.pop('promotion_keywords', None)
    # T-010: EveryInfra comment analysis is withdrawn; an old true value must
    # not trigger comment fetching or comment-model calls.
    result['comments_enabled'] = False
    # T-013: the old built-in default (never chosen by the user) moves to the customer's
    # policy-application voice; any style the user picked or wrote stays as it is.
    if 'style_preset' not in config:
        # Saved before presets were stored: the preset is recovered from the text when shown.
        result['style_preset'] = ''
    # T-027: the policy preset text was rewritten for readability. A style built on the old preset
    # text (with or without the user's extra requirements) moves to the new text; anything else stays.
    if str(result.get('style', '')).startswith(PREVIOUS_POLICY_STYLE):
        result['style'] = DEFAULTS['style'] + result['style'][len(PREVIOUS_POLICY_STYLE):]
    if result.get('style') == LEGACY_DEFAULT_STYLE and not config.get('style_preset'):
        result['style'] = DEFAULTS['style']
        result['style_preset'] = DEFAULTS['style_preset']
    return result


def validate(config):
    if config.get('task_action') not in (None, 'collect', 'write'):
        raise ValueError('任务类型无效')
    cap = config.get('max_paid_requests')
    if cap is not None and (type(cap) is not int or not 1 <= cap <= 10000):
        raise ValueError('付费请求次数上限必须为 1 到 10000 的整数，或留空')
    if config.get('image_articles', 'skip') not in ('skip', 'vision'):
        raise ValueError('图片型文章处理方式无效')
    if config.get('writing_mode', 'combined') not in ('combined', 'separate_web', 'single_web'):
        raise ValueError('写作模式无效')
    count = config.get('draft_count', 3)
    if type(count) is not int or not 1 <= count <= 5:
        raise ValueError('生成篇数请选择 1 到 5 篇')
    if config.get('lookback_mode', 'rolling') not in ('today', 'rolling'):
        raise ValueError('文章时间范围无效')
    if config.get('export_format', 'docx') not in ('docx', 'md', 'txt'):
        raise ValueError('导出格式请选择 Word、Markdown 或 TXT')
    if not isinstance(config.get('export_dir', ''), str):
        raise ValueError('导出位置无效')
    if config.get('writing_mode') in ('separate_web', 'single_web'):
        u = urlsplit(config.get('search_url', ''))
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('联网研究接口必须是无密钥参数的 HTTPS 地址')
    if config.get('selection_mode', 'recent') not in ('hot', 'recent'):
        raise ValueError('选文模式无效')
    if type(config.get('min_reads', 0)) is not int or not 0 <= config.get('min_reads', 0) <= 1000000000:
        raise ValueError('最低阅读量必须为 0 到 10 亿之间的整数')
    if config.get("source_provider", "weread") != "weread":
        raise ValueError("采集来源无效")
    if type(config.get("comment_limit", 30)) is not int or not 1 <= config.get("comment_limit", 30) <= 260:
        raise ValueError("评论数量必须为 1–260")
    if not str(config.get("search_model", "")).strip():
        raise ValueError("联网模型 ID 不能为空")
    if config.get('search_provider') not in ('deepseek', 'openai', 'bailian'):
        raise ValueError('联网搜索服务商无效')
    if config['search_provider'] == 'deepseek' and config['search_model'] not in ('deepseek-flash', 'deepseek-v4-pro'):
        raise ValueError('DeepSeek 联网搜索只支持已验证的 DeepSeek 模型')
    ZoneInfo(config["timezone"])
    datetime.strptime(config["schedule_time"], "%H:%M")
    if len(config["schedule_time"]) != 5:
        raise ValueError("执行时间请使用 HH:MM，例如 09:00")
    for key, low, high in [("article_limit", 1, 100), ("lookback_hours", 1, 720),
                           ("per_account_limit", 1, 160), ("min_sources", 1, 20),
                           ("target_words", 200, 20000)]:
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} 必须在 {low}–{high} 之间")
    if config["min_sources"] > config["article_limit"]:
        raise ValueError("最少素材篇数不能大于采集上限")
    for key in ("summary_provider", "writer_provider"):
        if config[key] not in config["providers"]:
            raise ValueError("请选择有效模型供应商")
    if not str(config.get('summary_model') or '').strip():
        raise ValueError('摘要模型 ID 不能为空')
    for p in config["providers"].values():
        u = urlsplit(p["base_url"])
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("模型接口地址必须是 HTTPS 地址，且不能包含密钥、查询参数或用户名")
        if not p["model"].strip():
            raise ValueError("模型 ID 不能为空")


def due_key(config, now=None):
    """Coalesce missed days to the most recent scheduled date, never before opt-in."""
    if not config["schedule_enabled"] or not config["schedule_start"]:
        return None
    now = (now or datetime.now(ZoneInfo(config["timezone"]))).astimezone(ZoneInfo(config["timezone"]))
    hour, minute = map(int, config["schedule_time"].split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due > now:
        due -= timedelta(days=1)
    if due.date().isoformat() < config["schedule_start"]:
        return None
    # One automatic run per local date even when the schedule is edited.
    return "daily:" + due.date().isoformat()
