from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

DEFAULTS = {
    "writing_mode": "separate_web", "draft_count": 3,
    "search_url": "https://api.openai.com/v1/responses",
    "search_model": "gpt-5-mini", "comments_enabled": True, "allow_recollect_cleared": False,
    "quality_review_enabled": True, "comment_limit": 30,
    "selection_mode": "hot", "min_reads": 0,
    "source_provider": "weread",
    "schedule_enabled": False, "schedule_time": "09:00", "timezone": "Asia/Shanghai",
    "schedule_start": "", "article_limit": 10, "lookback_hours": 24,
    "per_account_limit": 24, "min_sources": 2, "target_words": 1500,
    "topic": "", "style": "面向普通读者，清晰、客观、有逻辑，避免夸张营销和空泛套话。",
    "summary_provider": "openai", "writer_provider": "deepseek",
    # 'skip': articles whose text is under 100 characters are skipped (next
    # ranked article takes the slot). 'vision': send images to OpenAI.
    "image_articles": "skip",
    "providers": {
        "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5-mini"},
        "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
    },
}


def defaults():
    return deepcopy(DEFAULTS)


def migrate(config):
    result = defaults()
    result.update(deepcopy(config))
    result['source_provider'] = 'weread'
    providers = result.get('providers', {})
    result['providers'] = {name: deepcopy(providers.get(name, DEFAULTS['providers'][name]))
                           for name in ('openai', 'deepseek')}
    for field in ('summary_provider', 'writer_provider'):
        if result[field] == 'qwen':
            result[field] = 'openai'
    if 'dashscope' in result.get('search_url', ''):
        result['search_url'] = DEFAULTS['search_url']
    # v0.10.3 removed title-based promotion filtering. Drop legacy settings so
    # old databases cannot keep filtering articles invisibly.
    result.pop('skip_promotions', None)
    result.pop('promotion_keywords', None)
    return result


def validate(config):
    if config.get('image_articles', 'skip') not in ('skip', 'vision'):
        raise ValueError('图片型文章处理方式无效')
    if config.get('writing_mode', 'combined') not in ('combined', 'separate_web', 'single_web'):
        raise ValueError('写作模式无效')
    if config.get('draft_count', 3) not in (2, 3):
        raise ValueError('独立文章数量请选择 2 或 3')
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
    ZoneInfo(config["timezone"])
    datetime.strptime(config["schedule_time"], "%H:%M")
    if len(config["schedule_time"]) != 5:
        raise ValueError("执行时间请使用 HH:MM，例如 09:00")
    for key, low, high in [("article_limit", 1, 100), ("lookback_hours", 1, 720),
                           ("per_account_limit", 1, 160), ("min_sources", 1, 20),
                           ("target_words", 300, 5000)]:
        if not isinstance(config[key], int) or not low <= config[key] <= high:
            raise ValueError(f"{key} 必须在 {low}–{high} 之间")
    if config["min_sources"] > config["article_limit"]:
        raise ValueError("最少素材篇数不能大于采集上限")
    for key in ("summary_provider", "writer_provider"):
        if config[key] not in config["providers"]:
            raise ValueError("请选择有效模型供应商")
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
