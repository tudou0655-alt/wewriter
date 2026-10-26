"""Error codes and time helpers shared by the engine and the pipeline stages."""
from datetime import datetime, timedelta, timezone
from ..api import ApiError, Cancelled


class ErrorCode:
    MATERIAL = 'material'          # this article cannot provide usable source text -> next article may replace it
    NO_ACCOUNTS = 'no_accounts'
    COLLECT = 'collect'            # every account failed to list articles
    AUTH = 'auth'                  # WeRead login / API key / balance / permission
    NETWORK = 'network'
    MODEL_OUTPUT = 'model_output'  # model output failed validation even after one repair
    API = 'api'
    CONFIG = 'config'
    CANCELLED = 'cancelled'
    INTERNAL = 'internal'


class TaskError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def error_code(exc):
    if isinstance(exc, Cancelled):
        return ErrorCode.CANCELLED
    if getattr(exc, 'code', None):
        return exc.code
    if isinstance(exc, ApiError):
        if exc.kind in ('weread_auth', 'weread_ticket') or exc.fatal:
            return ErrorCode.AUTH
        if exc.kind == 'network':
            return ErrorCode.NETWORK
        if exc.kind == 'validation':
            return ErrorCode.MODEL_OUTPUT
        return ErrorCode.API
    if isinstance(exc, ValueError):
        return ErrorCode.CONFIG
    return ErrorCode.INTERNAL


# China Standard Time has no DST, so a fixed offset avoids depending on the
# customer's local zone or on tzdata.
CHINA_TZ = timezone(timedelta(hours=8))


def china_time(value):
    """Format a stored ISO time as China Standard Time; '未知' when absent or invalid."""
    when = _published_at(value)
    return f'{when.astimezone(CHINA_TZ):%Y-%m-%d %H:%M}（中国时间）' if when else '未知'


def metric_status(raw, value):
    """'measured' (a valid count, 0 included), 'missing' (field absent/empty) or 'invalid'."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 'missing'
    return 'measured' if value is not None else 'invalid'


def _published_at(value):
    try:
        when = datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None
    return when if when and when.tzinfo else None
