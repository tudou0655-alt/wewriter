"""Per-job request usage. Amounts are estimates, never provider invoices.

Only numeric usage and a short model identifier are persisted in the existing funnel JSON.
No prompts, URLs, request bodies, keys, or provider responses enter the ledger.
"""
import json
import hashlib
import re
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit


# USD prices per million tokens, checked against provider pages on 2026-10-27.
# Unknown models deliberately have no amount. The exchange rate is an explicit
# illustration, not a live foreign-exchange quote.
USD_CNY = Decimal('6.70')
DAJIALA_CNY = Decimal('0.16')  # User's provider contract figure; verify with invoice.
SEARCH_USD = Decimal('0.01')
PRICE_DATE = '2026-10-28'
MODEL_RATES = {
    'gpt-5-mini': ('0.25', '0.025', '2'),
    'gpt-5.6-terra': ('2', '0.2', '12'),
    'gpt-5.6-luna': ('0.2', '0.02', '1.2'),
    'gpt-6-luna': ('0.10', '0.01', '0.50'),
}
CACHE_WRITE_MODELS = frozenset(('gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-6-luna'))
DEEPSEEK_CNY_RATES = {
    'deepseek-flash': {'peak': ('2', '0.04', '8'), 'offpeak': ('1', '0.02', '4')},
    'deepseek-v4-pro': {'peak': ('9', '0.30', '27'), 'offpeak': ('4.5', '0.15', '13.5')},
}
BAILIAN_CNY_RATES = {'qwen3.8-flash': ('0.8', '0.1', '2.7')}
DEEPSEEK_SEARCH_CNY = Decimal('0.005')
BAILIAN_SEARCH_CNY = Decimal('0.004')  # Beijing Responses agent search
# State Council 2026 holiday schedule. Weekend make-up workdays remain offpeak
# under DeepSeek's published weekday rule; the holiday date ranges are offpeak.
CN_HOLIDAYS_2026 = ((1, 1, 1, 3), (2, 15, 2, 23), (4, 4, 4, 6),
                    (5, 1, 5, 5), (6, 19, 6, 21), (9, 25, 9, 27),
                    (10, 1, 10, 7))


def _deepseek_band(at):
    china = at.astimezone(ZoneInfo('Asia/Shanghai'))
    holiday = (china.year == 2026 and any(
        date(2026, start_month, start_day) <= china.date() <= date(2026, end_month, end_day)
        for start_month, start_day, end_month, end_day in CN_HOLIDAYS_2026))
    peak = (china.weekday() < 5 and not holiday
            and (9 <= china.hour < 12 or 14 <= china.hour < 18))
    return 'peak' if peak else 'offpeak'


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _kind(url, payload):
    parsed = urlsplit(url)
    path = parsed.path.rstrip('/')
    if payload is None:
        return None
    if path.endswith('/post_history'):
        return 'collection'
    if parsed.hostname == 'api.deepseek.com' and path.endswith('/v1/tools/search'):
        return 'search'
    if parsed.hostname == 'api.everyinfra.com' and path in ('/api/v1/social', '/api/v1/search'):
        # Contract price is unknown, but every sent call must still occupy a
        # request slot and appear as unknown in the ledger.
        return 'collection'
    if path.endswith('/chat/completions'):
        return 'model'
    if path.endswith('/responses'):
        if isinstance(payload, dict) and any(isinstance(t, dict) and t.get('type') == 'web_search'
               for t in (payload.get('tools') or [])):
            return 'search'
        return 'model'
    return None


def _usage(result):
    usage = result.get('usage') if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return None
    inp = _integer(usage.get('input_tokens', usage.get('prompt_tokens')))
    out = _integer(usage.get('output_tokens', usage.get('completion_tokens')))
    if inp is None or out is None:
        return None
    details = usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
    cached = _integer(details.get('cached_tokens')) if isinstance(details, dict) else None
    if cached is None:
        cached = _integer(usage.get('prompt_cache_hit_tokens')) or 0
    return inp, min(inp, cached), out


def _estimate(event):
    try:
        if event['kind'] == 'collection':
            return Decimal(event['provider_cny']) if event.get('status') == 'measured' and event.get('provider_cny') else None
        if event.get('search_cny') is not None and event.get('search_only'):
            calls = _integer(event.get('search_calls'))
            return Decimal(calls) * Decimal(event['search_cny']) if calls is not None else None
        numbers = event.get('tokens')
        rates = event.get('rates')
        if (not isinstance(numbers, (list, tuple)) or len(numbers) != 3
                or not all(_integer(n) is not None for n in numbers)
                or not isinstance(rates, (list, tuple)) or len(rates) != 3):
            return None
        inp, cached, out = numbers
        writes = event.get('cache_writes', [0, 0])
        if (cached > inp or not isinstance(writes, list) or len(writes) != 2
                or any(_integer(n) is None for n in writes)
                or cached + sum(writes) > inp or event.get('unpriced_cache_write')):
            return None
        base, hit, output = map(Decimal, rates)
        if event.get('host') == 'openai' and event.get('model') in CACHE_WRITE_MODELS and inp > 272_000:
            base *= 2
            hit *= 2
            output *= Decimal('1.5')
        amount = ((Decimal(inp - cached - sum(writes)) * base + Decimal(cached) * hit
                + Decimal(writes[0]) * base * Decimal('1.25')
                + Decimal(writes[1]) * base * Decimal('2.5')
                + Decimal(out) * output) / Decimal(1_000_000))
        if event['kind'] == 'search':
            calls = _integer(event.get('search_calls'))
            if calls is None:
                return None
            amount += Decimal(calls) * Decimal(event.get('search_usd', '0'))
        if event.get('currency') == 'CNY':
            return amount + Decimal(_integer(event.get('search_calls')) or 0) * Decimal(event.get('search_cny', '0'))
        return amount * Decimal(event['fx'])
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None


class CostMeter:
    """The running job's funnel owns the ledger, including retries of that job."""

    def __init__(self, funnel, max_requests=None):
        self.funnel = funnel
        self.events = funnel.data.setdefault('cost_events', [])
        self.max_requests = max_requests

    def _attempt_count(self):
        """Count the parent's and all children's attempts before another paid POST."""
        db, job_id = self.funnel.db, self.funnel.job_id
        parent = db.rows('SELECT parent_job_id FROM batch_items WHERE child_job_id=?', (job_id,))
        parent_id = parent[0]['parent_job_id'] if parent else job_id
        ids = [parent_id] + [r['child_job_id'] for r in db.rows(
            'SELECT child_job_id FROM batch_items WHERE parent_job_id=?', (parent_id,))]
        total = 0
        for row in db.rows('SELECT funnel FROM jobs WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids):
            try:
                events = json.loads(row['funnel'] or '{}').get('cost_events', [])
            except (ValueError, TypeError, AttributeError):
                continue
            if isinstance(events, list):
                total += sum(isinstance(event, dict) and event.get('kind') in
                             ('collection', 'model', 'search') for event in events)
        return total

    def begin(self, url, payload):
        kind = _kind(url, payload)
        if kind is None:
            return None
        if self.max_requests is not None and self._attempt_count() >= self.max_requests:
            raise ValueError('本任务已达到付费请求次数上限；已发出但费用未知的请求也计入上限。'
                             '请查看金币用量，到设置中提高上限，再从运行记录继续原任务')
        model = str(payload.get('model') or '') if isinstance(payload, dict) else ''
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', model):
            model = ''
        hostname = urlsplit(url).hostname
        host = {'api.deepseek.com': 'deepseek', 'api.openai.com': 'openai',
                'dashscope.aliyuncs.com': 'bailian',
                'www.dajiala.com': 'dajiala', 'api.everyinfra.com': 'everyinfra'}.get(hostname, 'other')
        if isinstance(hostname, str) and hostname.endswith('.cn-beijing.maas.aliyuncs.com'):
            host = 'bailian'
        if kind == 'search' and host == 'deepseek' and urlsplit(url).path.endswith('/v1/tools/search'):
            model = 'deepseek-search'
        event = {'id': uuid.uuid4().hex, 'kind': kind, 'model': model, 'host': host,
                 'at': datetime.now(timezone.utc).isoformat(), 'status': 'unknown',
                 'price_date': PRICE_DATE}
        if kind == 'collection' and host == 'dajiala':
            event['provider_cny'] = str(DAJIALA_CNY)
        elif kind == 'search' and model == 'deepseek-search':
            event.update(search_only=True, search_cny=str(DEEPSEEK_SEARCH_CNY), currency='CNY')
        elif kind in ('model', 'search'):
            if host == 'deepseek' and model in DEEPSEEK_CNY_RATES:
                event['band'] = _deepseek_band(datetime.now(timezone.utc))
                event['rates'] = DEEPSEEK_CNY_RATES[model][event['band']]
                event['currency'] = 'CNY'
            elif host == 'bailian' and model in BAILIAN_CNY_RATES:
                event['rates'] = BAILIAN_CNY_RATES[model]
                event['currency'] = 'CNY'
                if kind == 'search':
                    event['search_cny'] = str(BAILIAN_SEARCH_CNY)
            elif host == 'openai' and model in MODEL_RATES:
                event['rates'] = MODEL_RATES[model]
                event['fx'] = str(USD_CNY)
                # This app sends no per-breakpoint TTL overrides. Keep the
                # request's default so an aggregate-only usage response can
                # still price cache writes without guessing their lifetime.
                options = payload.get('prompt_cache_options') or {}
                if model in CACHE_WRITE_MODELS and isinstance(options, dict):
                    ttl = options.get('ttl', '30m')
                    if ttl in ('30m', '12h'):
                        event['cache_write_ttl'] = ttl
                if kind == 'search':
                    event['search_usd'] = str(SEARCH_USD)
        self.events.append(event)
        self.funnel.save()  # Save before sending: timeout/HTTP retry may still be billed.
        return event['id']

    def finish(self, event_id, result):
        if not event_id or not isinstance(result, dict):
            return
        event = next((e for e in reversed(self.events) if e['id'] == event_id), None)
        if event is None:
            return
        if event['kind'] == 'collection':
            if str(result.get('code')) == '0' and isinstance(result.get('data'), list):
                event['status'] = 'measured'
        else:
            numbers = _usage(result)
            if numbers is not None:
                event['tokens'] = numbers
                event['status'] = 'measured'
                if event.get('host') == 'openai' and event.get('model') in CACHE_WRITE_MODELS:
                    usage = result.get('usage') or {}
                    details = usage.get('input_tokens_details') or {}
                    if isinstance(details, dict):
                        total = _integer(details.get('cache_write_tokens'))
                        by_ttl = details.get('cache_write_tokens_details') or {}
                        thirty = _integer(by_ttl.get('ttl_30m_tokens')) if isinstance(by_ttl, dict) else None
                        twelve = _integer(by_ttl.get('ttl_12h_tokens')) if isinstance(by_ttl, dict) else None
                        if (thirty is not None and twelve is not None
                                and (total is None or thirty + twelve == total)):
                            event['cache_writes'] = [thirty, twelve]
                        elif (total is not None and not by_ttl
                              and event.get('cache_write_ttl') in ('30m', '12h')):
                            event['cache_writes'] = [total, 0] if event['cache_write_ttl'] == '30m' else [0, total]
                        elif total:
                            event['unpriced_cache_write'] = True
                        # No write count means ordinary input and cached reads
                        # are still priced from the recorded token totals.
            if event['kind'] == 'search':
                if event.get('search_only'):
                    rows = result.get('results')
                    if isinstance(rows, list):
                        event['search_calls'] = 1 if rows else 0
                        event['status'] = 'measured'
                response_id = result.get('id')
                if isinstance(response_id, str) and response_id:
                    event['response_id_hash'] = hashlib.sha256(response_id.encode()).hexdigest()
                if result.get('status') in ('completed', 'failed', 'cancelled', 'incomplete'):
                    self._search_calls(event, result)
        self.funnel.save()

    def finish_search(self, result):
        """Background polling is free; record the one completed search by response id."""
        if not isinstance(result, dict) or not isinstance(result.get('id'), str):
            return
        response_hash = hashlib.sha256(result['id'].encode()).hexdigest()
        event = next((e for e in reversed(self.events)
                      if e['kind'] == 'search' and e.get('response_id_hash') == response_hash), None)
        if event is not None:
            self.finish(event['id'], result)

    @staticmethod
    def _search_calls(event, result):
        usage = result.get('usage')
        if event.get('host') == 'bailian' and isinstance(usage, dict):
            tools = usage.get('x_tools')
            web = tools.get('web_search') if isinstance(tools, dict) else None
            count = _integer(web.get('count')) if isinstance(web, dict) else None
            if count is not None:
                event['search_calls'] = count
                return
        output = result.get('output')
        if isinstance(output, list):
            event['search_calls'] = sum(isinstance(item, dict)
                and item.get('type') == 'web_search_call' and item.get('status') == 'completed'
                and (not isinstance(item.get('action'), dict)
                     or item['action'].get('type', 'search') == 'search') for item in output)


class UtilityFunnel:
    """Separate ledger for paid settings actions, which have no writing job."""

    def __init__(self, db):
        self.db = db
        rows = db.rows("SELECT value FROM settings WHERE key='utility_cost_events'")
        try:
            events = json.loads(rows[0]['value']) if rows else []
        except (TypeError, ValueError):
            events = []
        self.data = {'cost_events': events if isinstance(events, list) else []}

    def save(self):
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('utility_cost_events',?)",
                        (json.dumps(self.data['cost_events'], ensure_ascii=False),))


def utility_summary(db):
    events = UtilityFunnel(db).data['cost_events']
    amounts = [_estimate(event) for event in events if isinstance(event, dict)]
    known = sum((amount for amount in amounts if amount is not None), Decimal(0))
    return {'requests': len(amounts), 'known_cny': str(known.quantize(Decimal('0.0001'))),
            'unknown_requests': sum(amount is None for amount in amounts)}


def summary(db, job_id):
    """Aggregate parent + child ledgers once; older records remain explicitly unknown."""
    ids = [job_id] + [row['child_job_id'] for row in db.rows(
        'SELECT child_job_id FROM batch_items WHERE parent_job_id=?', (job_id,))]
    rows = db.rows('SELECT id,funnel FROM jobs WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)
    events, tracked = [], False
    for row in rows:
        try:
            funnel = json.loads(row['funnel'] or '{}')
        except (ValueError, TypeError):
            continue
        if isinstance(funnel.get('cost_events'), list):
            tracked = True
            events.extend(e for e in funnel['cost_events'] if isinstance(e, dict))
    groups = {}
    for event in events:
        kind = event.get('kind')
        if kind not in ('collection', 'model', 'search'):
            continue
        model = event.get('model') or ''
        key = (kind, model if kind != 'collection' else '')
        group = groups.setdefault(key, {'kind': kind, 'model': model, 'requests': 0,
                                         'input_tokens': 0, 'cached_tokens': 0,
                                         'output_tokens': 0, 'search_calls': 0,
                                         'unknown': 0, 'cache_unknown': 0,
                                         'cny': Decimal(0)})
        group['requests'] += 1
        numbers = event.get('tokens')
        if isinstance(numbers, list) and len(numbers) == 3 and all(_integer(n) is not None for n in numbers):
            group['input_tokens'] += numbers[0]
            group['cached_tokens'] += numbers[1]
            group['output_tokens'] += numbers[2]
        group['search_calls'] += _integer(event.get('search_calls')) or 0
        amount = _estimate(event)
        if amount is None:
            group['unknown'] += 1
            if event.get('unpriced_cache_write'):
                group['cache_unknown'] += 1
        else:
            group['cny'] += amount
    result = [{**{k: v for k, v in g.items() if k != 'cny'},
               'cny': str(g['cny'].quantize(Decimal('0.0001')))} for g in groups.values()]
    total = sum((g['cny'] for g in groups.values()), Decimal(0))
    return {'tracked': tracked, 'groups': result,
            'known_cny': str(total.quantize(Decimal('0.0001'))),
            'unknown_requests': sum(g['unknown'] for g in groups.values()),
            'cache_unknown_requests': sum(g['cache_unknown'] for g in groups.values()),
            'fx': str(USD_CNY), 'provider_cny': str(DAJIALA_CNY),
            'price_date': PRICE_DATE}
