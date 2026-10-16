"""Local diagnostic log. Never logs API keys, cookies or article bodies."""
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
                  'refresh[_-]?token', 'wr_skey', 'wr_rt', 'wr_vid')
_VALUE = r'[A-Za-z0-9._~+/%=-]'
# Each pattern keeps group 1 (the label) and masks group 2 (the value).
_SECRET_PATTERNS = (
    # key=value, key: value, "key": "value", URL query and Cookie pairs.
    re.compile(r'(?i)(?<![A-Za-z0-9_-])((?:%s)["\']?\s*[:=]\s*["\']?)(%s+)'
               % ('|'.join(_SECRET_FIELDS), _VALUE)),
    # Generic ?key= / &token= URL parameters.
    re.compile(r'(?i)([?&](?:key|token)=)(%s+)' % _VALUE),
    # Authorization header with or without an auth scheme.
    re.compile(r'(?i)(?<![A-Za-z0-9_-])(authorization["\']?\s*[:=]\s*["\']?'
               r'(?:(?:bearer|basic|token)\s+)?)(?!(?:bearer|basic|token)\s)(%s+)' % _VALUE),
    re.compile(r'(?i)\b(bearer\s+)(%s{6,})' % _VALUE),
    # Bare sk-/sess- style keys; the prefix must start a token so "task-1" stays intact.
    re.compile(r'(?<![A-Za-z0-9_-])(sk-|sess-)([A-Za-z0-9_-]{16,})'),
)


def redact(text):
    """Return text with credential values replaced by MASK. Never raises."""
    try:
        text = str(text)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + MASK, text)
        return text
    except Exception:
        return MASK


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
