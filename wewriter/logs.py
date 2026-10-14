"""Local diagnostic log. Never logs API keys, cookies or article bodies."""
import logging
import logging.handlers
import re
import sys
import threading
from pathlib import Path

logger = logging.getLogger('wewriter')
_SECRET = re.compile(r'(?i)(bearer\s+|sk-|sess-|wr_skey=|wr_rt=|wr_vid=)[A-Za-z0-9._%~+/=-]{6,}')


class _Redact(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = _SECRET.sub(lambda m: m.group(1) + '[已隐藏]', message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
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
