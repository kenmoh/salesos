import json
import logging
from typing import Any

import redis.asyncio as redis

from app.core.config import settings

log = logging.getLogger(__name__)

_cache: redis.Redis | None = None
_session: redis.Redis | None = None
_redis_available = True

REDIS_TIMEOUT = 2


async def get_cache_redis() -> redis.Redis | None:
    global _cache, _redis_available
    if not _redis_available:
        return None
    try:
        if _cache is None:
            _cache = redis.from_url(
                settings.redis_cache_url,
                decode_responses=True,
                socket_connect_timeout=REDIS_TIMEOUT,
                socket_timeout=REDIS_TIMEOUT,
            )
        return _cache
    except Exception:
        _redis_available = False
        return None


async def get_session_redis() -> redis.Redis | None:
    global _session, _redis_available
    if not _redis_available:
        return None
    try:
        if _session is None:
            _session = redis.from_url(
                settings.redis_session_url,
                decode_responses=True,
                socket_connect_timeout=REDIS_TIMEOUT,
                socket_timeout=REDIS_TIMEOUT,
            )
        return _session
    except Exception:
        _redis_available = False
        return None


async def cache_get(key: str) -> str | None:
    client = await get_cache_redis()
    if client is None:
        return None
    try:
        return await client.get(key)
    except Exception:
        global _redis_available
        _redis_available = False
        return None


async def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    client = await get_cache_redis()
    if client is None:
        return
    try:
        if not isinstance(value, str):
            value = json.dumps(value)
        await client.set(key, value, ex=ttl)
    except Exception:
        global _redis_available
        _redis_available = False
        pass


async def cache_del(key: str) -> None:
    client = await get_cache_redis()
    if client is None:
        return
    try:
        await client.delete(key)
    except Exception:
        pass


async def cache_del_pattern(pattern: str) -> None:
    client = await get_cache_redis()
    if client is None:
        return
    try:
        async for key in client.scan_iter(match=pattern):
            await client.delete(key)
    except Exception:
        pass


async def close_redis() -> None:
    for client in (_cache, _session):
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
