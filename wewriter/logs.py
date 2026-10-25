"""Local diagnostic log. Never logs API keys, cookies or article bodies.

``redact`` is also the single redaction boundary for diagnostics persisted in the
database (task messages, warnings, article errors) and for error text shown in the UI.
"""
import json
import logging
import logging.handlers
import re
import sys
import threading
from pathlib import Path

logger = logging.getLogger('wewriter')
MASK = '[已隐藏]'
# Field names whose values are credentials: API/Skill keys, OAuth tokens and WeRead cookies.
_SECRET_FIELDS = ('api[_-]?key', 'x-api-key', 'skill[_-]?key', 'access[_-]?token',
                  'refresh[_-]?token', 'wr_skey', 'wr_rt', 'wr_vid',
                  # WeRead per-request verification headers and browser login headers.
                  'x-wr-ticket', 'x-wrpa-0', 'x-skey', 'password', 'passwd', 'client[_-]?secret')
# Quotes that may wrap a value, including the HTML-escaped forms seen in upstream error pages.
_QUOTE = r'"|\'|&#34;|&#x22;|&quot;|&#39;|&#x27;|&apos;'
# An unquoted value runs to whitespace, a quote, a pair separator (; , or Chinese
# punctuation) or the start of the next URL parameter / HTML entity. Any other character,
# such as ! : @ # or %, belongs to the value, so special-character secrets are fully masked.
_UNQUOTED = (r'(?:(?!&(?:#\d+;|#x[0-9a-f]+;|[a-z]+;|[A-Za-z_][\w.-]*=))'
             r'[^\s"\'；，。、<>;,])+')
_FIELD_VALUE = re.compile(
    r'(?im)(?<![A-Za-z0-9_-])((?:%s)(?:%s)?\s*[:=：]\s*)'
    r'(?:(%s)((?:\\.|(?!\2)[^\\\r\n])*)(\2|$)|(%s))' % ('|'.join(_SECRET_FIELDS), _QUOTE, _QUOTE, _UNQUOTED))
_VALUE = r'[A-Za-z0-9._~+/%=-]'
# Each pattern keeps group 1 (the label) and masks group 2 (the value).
_SECRET_PATTERNS = (
    # A whole Cookie / Set-Cookie header value: every pair in it can be a session credential.
    # It ends at a line break, a quote or Chinese punctuation that separates message parts.
    re.compile(r'(?i)(?<![A-Za-z0-9_-])((?:set-)?cookie["\']?\s*[:=]\s*["\']?)([^\r\n"\'；，。]+)'),
    # Generic ?key= / &token= URL parameters.
    re.compile(r'(?i)([?&](?:key|token)=)([^\s&#"\'<>]+)'),
    # Authorization header with or without an auth scheme.
    re.compile(r'(?i)(?<![A-Za-z0-9_-])(authorization["\']?\s*[:=]\s*["\']?'
               r'(?:(?:bearer|basic|token)\s+)?)(?!(?:bearer|basic|token)\s)(%s)' % _UNQUOTED),
    re.compile(r'(?i)\b(bearer\s+)((?:(?!&#|&quot;)[^\s"\'；，。、<>;,]){6,})'),
    # Bare sk-/sess- style keys; the prefix must start a token so "task-1" stays intact.
    re.compile(r'(?<![A-Za-z0-9_-])(sk-|sess-)([A-Za-z0-9_-]{16,})'),
)


def _mask_field(match):
    label, quote, _, closing, _ = match.groups()
    return label + (quote + MASK + closing if quote else MASK)


def redact(text):
    """Return text with credential values replaced by MASK. Never raises."""
    try:
        text = str(text)
        text = _FIELD_VALUE.sub(_mask_field, text)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + MASK, text)
        return text
    except Exception:
        return MASK


def redact_warnings(value):
    """Redact a ``jobs.warnings`` value (JSON list text or list); return JSON list text.

    Unparseable text is redacted as a single warning so it is never stored verbatim."""
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        items = [value]
    if not isinstance(items, list):
        items = [items]
    return json.dumps([redact(item) for item in items], ensure_ascii=False)


class _Redact(logging.Filter):
    _formatter = logging.Formatter()

    def filter(self, record):
        try:
            try:
                message = record.getMessage()
            except Exception:
                # Bad format args: keep the template only, so logging still succeeds.
                message = '%s（日志参数格式化失败）' % record.msg
            record.msg, record.args = redact(message), ()
            if record.exc_info and not record.exc_text:
                record.exc_text = redact(self._formatter.formatException(record.exc_info))
            elif record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            record.msg, record.args, record.exc_info, record.exc_text = MASK, (), None, None
        return True


def setup(data_dir):
    """Write rotating logs to <data_dir>/logs/wewriter.log and install crash hooks."""
    if getattr(setup, 'path', None):
        return setup.path
    directory = Path(data_dir) / 'logs'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / 'wewriter.log', maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8')
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s'))
    handler.addFilter(_Redact())
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    def excepthook(kind, value, tb):
        logger.critical('未捕获异常', exc_info=(kind, value, tb))
        sys.__excepthook__(kind, value, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        logger.critical('后台线程未捕获异常：%s', getattr(args.thread, 'name', '?'),
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    setup.path = directory / 'wewriter.log'
    return setup.path
