import logging
import time

from django.conf import settings
from django_redis.cache import RedisCache
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

logger = logging.getLogger("planner.cache")

# django-redis re-raises the original redis error when IGNORE_EXCEPTIONS is off,
# so catch both the wrapper and the underlying types.
_FAILURES = (ConnectionInterrupted, RedisConnectionError, RedisTimeoutError)

_open_until = 0.0  # per process (each gunicorn worker keeps its own breaker)


def reset_breaker():
    global _open_until
    _open_until = 0.0


def _cooldown():
    return getattr(settings, "CACHE_BREAKER_SECONDS", 30)


def _get_default(args, kwargs):
    if "default" in kwargs:
        return kwargs["default"]
    return args[1] if len(args) > 1 else None


def _guarded(name, fallback):
    def method(self, *args, **kwargs):
        global _open_until
        if time.monotonic() < _open_until:
            return fallback(args, kwargs)
        try:
            return getattr(super(BreakerRedisCache, self), name)(*args, **kwargs)
        except _FAILURES as exc:
            _open_until = time.monotonic() + _cooldown()
            logger.warning(
                "Redis unavailable (%s); cache bypassed for %ss", exc, _cooldown()
            )
            return fallback(args, kwargs)

    method.__name__ = name
    return method


class BreakerRedisCache(RedisCache):
    """Redis cache that stops calling Redis for a cooldown after a connection failure."""

    get = _guarded("get", _get_default)
    set = _guarded("set", lambda a, k: False)
    add = _guarded("add", lambda a, k: False)
    delete = _guarded("delete", lambda a, k: False)
    get_many = _guarded("get_many", lambda a, k: {})
    set_many = _guarded("set_many", lambda a, k: [])
    delete_many = _guarded("delete_many", lambda a, k: None)
    has_key = _guarded("has_key", lambda a, k: False)
    touch = _guarded("touch", lambda a, k: False)