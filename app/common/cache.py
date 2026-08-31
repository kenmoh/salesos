"""Redis-backed caching service with tenant-aware keys and decorator.

Provides a unified cache layer for the application with:
- Tenant-scoped cache keys to prevent cross-tenant data leakage
- TTL-based expiration as a safety net
- Decorator for easy caching of bridge/analytics functions
- Soft failure: all operations return None on Redis errors

Usage:
    from app.common.cache import cache, cached

    # Manual usage
    await cache.set("stores:list", data, ttl=300)
    data = await cache.get("stores:list")

    # Decorator usage
    @cached(prefix="stores:list", ttl=300, key_func=lambda tenant_id, **kw: tenant_id)
    async def list_stores(tenant_id: str):
        ...
"""

import json
import logging
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger("app.cache")

_prefix = "sf"


class CacheService:
    """Redis-backed cache with tenant-aware key namespacing."""

    def __init__(self):
        self._client = None

    async def _get_client(self):
        if self._client is None:
            try:
                from app.core.redis_client import get_cache_redis
                self._client = await get_cache_redis()
            except Exception:
                return None
        return self._client

    async def get(self, key: str) -> Any | None:
        client = await self._get_client()
        if client is None:
            return None
        try:
            raw = await client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: int = 300):
        client = await self._get_client()
        if client is None:
            return
        try:
            serialized = json.dumps(value, default=str)
            await client.set(key, serialized, ex=ttl)
        except Exception:
            pass

    async def delete(self, key: str):
        client = await self._get_client()
        if client is None:
            return
        try:
            await client.delete(key)
        except Exception:
            pass

    async def delete_pattern(self, pattern: str):
        """Delete all keys matching a glob pattern."""
        client = await self._get_client()
        if client is None:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = await client.scan(cursor, match=pattern, count=100)
                if keys:
                    await client.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    async def exists(self, key: str) -> bool:
        client = await self._get_client()
        if client is None:
            return False
        try:
            return await client.exists(key) > 0
        except Exception:
            return False


cache = CacheService()


def _build_key(prefix: str, tenant_id: str | None, suffix: str) -> str:
    parts = [f"{_prefix}:cache:{prefix}"]
    if tenant_id:
        parts.append(tenant_id)
    if suffix:
        parts.append(suffix)
    return ":".join(parts)


def cached(
    prefix: str,
    ttl: int = 300,
    key_func: Callable[..., str] | None = None,
):
    """Decorator that caches async function results in Redis.

    Args:
        prefix: Cache key prefix (e.g., "stores:list").
        ttl: Time-to-live in seconds (default: 300).
        key_func: Function that generates a suffix from the wrapped
            function's arguments. Defaults to empty suffix.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tenant_id = kwargs.get("tenant_id") or (
                args[0] if args and isinstance(args[0], str) else None
            )
            if key_func:
                suffix = key_func(*args, **kwargs)
            else:
                suffix = ""
            key = _build_key(prefix, str(tenant_id) if tenant_id else None, suffix)

            cached_val = await cache.get(key)
            if cached_val is not None:
                return cached_val

            result = await func(*args, **kwargs)

            if result is not None:
                await cache.set(key, result, ttl=ttl)

            return result
        return wrapper
    return decorator


async def invalidate_tenant(tenant_id: str, prefix: str | None = None):
    """Invalidate all cache keys for a tenant, optionally filtered by prefix."""
    pattern = f"{_prefix}:cache:{prefix or ''}*:{tenant_id}*"
    await cache.delete_pattern(pattern)
