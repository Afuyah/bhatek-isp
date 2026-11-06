"""Small Redis-backed cache for hot read paths.

Everything degrades to a cache miss: if Redis is down, the caller just runs its
query. Nothing here may raise into a request.

Keys are namespaced ``isp:<area>:<...>`` so a whole area can be invalidated
with one SCAN pattern (``delete_area``).
"""

from __future__ import annotations

from functools import wraps

from app.core.database.redis_client import RedisClient
from app.core.logging.logger import logger

PREFIX = 'isp'
DEFAULT_TTL = 60


def key(*parts) -> str:
    """Build a namespaced cache key: ``isp:plans:<org>``."""
    return ':'.join([PREFIX] + [str(p) for p in parts if p not in (None, '')])


def get(cache_key):
    try:
        return RedisClient.get_json(cache_key)
    except Exception as exc:
        logger.debug("cache get failed for %s: %s", cache_key, exc)
        return None


def set(cache_key, value, ttl: int = DEFAULT_TTL):
    try:
        RedisClient.set_json(cache_key, value, ex=ttl)
    except Exception as exc:
        logger.debug("cache set failed for %s: %s", cache_key, exc)


def delete(*cache_keys):
    for cache_key in cache_keys:
        try:
            RedisClient.delete(cache_key)
        except Exception as exc:
            logger.debug("cache delete failed for %s: %s", cache_key, exc)


def delete_area(area: str):
    """Invalidate every key under ``isp:<area>:``."""
    try:
        RedisClient.delete_pattern(f'{PREFIX}:{area}:*')
    except Exception as exc:
        logger.debug("cache area purge failed for %s: %s", area, exc)


def remember(area: str, ttl: int = DEFAULT_TTL):
    """Cache a function's JSON-serialisable return value.

    The key includes the function's arguments, so per-tenant calls are cached
    independently::

        @remember('plans', ttl=120)
        def get_public_plans(self, organization_id): ...

    Invalidate with ``delete_area('plans')`` after a plan changes.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            cache_key = key(area, fn.__name__, *args[1:], *[f'{k}={v}' for k, v in sorted(kwargs.items())])
            hit = get(cache_key)
            if hit is not None:
                return hit
            value = fn(*args, **kwargs)
            if value is not None:
                set(cache_key, value, ttl=ttl)
            return value

        return wrapper

    return decorator
