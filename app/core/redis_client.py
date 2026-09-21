import asyncio
import json
import logging
import time
from typing import Any

import redis.asyncio as redis

from app.core.config import settings

log = logging.getLogger(__name__)

REDIS_CONNECT_TIMEOUT = 1  # seconds; TCP+TLS connect budget
REDIS_SOCKET_TIMEOUT = 2  # seconds; per-command budget

# ── Circuit breaker ──────────────────────────────────────────────────────────
# Redis here is best-effort: caching, rate limiting, and token blacklisting all
# degrade gracefully. When the Redis endpoint is unreachable (network outage,
# expired subscription, DNS failure), waiting out the socket timeout on every
# call stalls every request. The breaker trips after consecutive failures and
# then fails fast — callers see the same "Redis unavailable" behavior they get
# today when an operation raises, just in microseconds instead of seconds.
#
# Half-open probing is singleflight: one caller per reset window may attempt a
# real command; everyone else fails fast until that probe succeeds or re-opens
# the breaker.

BREAKER_THRESHOLD = 2  # consecutive failures before the breaker opens
BREAKER_RESET_SECONDS = 30.0  # how long the breaker stays open before a probe

_breaker_failures = 0
_breaker_open_until = 0.0
_breaker_probe_inflight = False


class _BreakerOpen(Exception):
    """Raised instead of touching the socket while the breaker is open."""


def _breaker_allows() -> bool:
    """Return True if a real Redis command may be attempted now."""
    global _breaker_open_until, _breaker_probe_inflight
    if _breaker_open_until == 0.0:
        return True  # breaker closed
    now = time.monotonic()
    if now < _breaker_open_until:
        return False  # still open — fail fast
    # Half-open: allow exactly one in-flight probe per window.
    if _breaker_probe_inflight:
        return False
    _breaker_probe_inflight = True
    return True


def _record_success() -> None:
    global _breaker_failures, _breaker_open_until, _breaker_probe_inflight
    if _breaker_open_until != 0.0 or _breaker_failures:
        log.info("Redis circuit breaker closed — endpoint is healthy again")
    _breaker_failures = 0
    _breaker_open_until = 0.0
    _breaker_probe_inflight = False


def _record_failure() -> None:
    global _breaker_failures, _breaker_open_until, _breaker_probe_inflight
    _breaker_probe_inflight = False
    _breaker_failures += 1
    if _breaker_failures >= BREAKER_THRESHOLD and _breaker_open_until == 0.0:
        _breaker_open_until = time.monotonic() + BREAKER_RESET_SECONDS
        log.warning(
            "Redis unreachable after %d consecutive failures — circuit breaker "
            "open for %.0fs (failing fast until then)",
            _breaker_failures,
            BREAKER_RESET_SECONDS,
        )
    elif _breaker_open_until != 0.0:
        # Failed probe in half-open state: re-open for another window.
        _breaker_open_until = time.monotonic() + BREAKER_RESET_SECONDS


class _GuardedRedis:
    """Proxy that funnels every command through the circuit breaker.

    Async command methods (``get``, ``set``, ``exists``, ``ping``, …) are
    resolved via ``__getattr__`` and wrapped so successes/failures feed the
    breaker; ``execute_command`` is wrapped directly. Sync accessors and async
    generators (e.g. ``scan_iter``) pass through unwrapped. Callers' existing
    try/except blocks treat ``_BreakerOpen`` like any other Redis failure.
    """

    __slots__ = ("_client",)

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def guarded(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            # Awaitable results (command calls) go through the breaker; sync
            # results (pipelines, iterators) pass through untouched.
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                return self._guard(result)
            return result

        return guarded

    async def _guard(self, coro: Any) -> Any:
        if not _breaker_allows():
            raise _BreakerOpen("Redis circuit breaker is open")
        try:
            result = await coro
        except Exception:
            _record_failure()
            raise
        _record_success()
        return result

    async def execute_command(self, *args: Any, **kwargs: Any) -> Any:
        if not _breaker_allows():
            raise _BreakerOpen("Redis circuit breaker is open")
        try:
            result = await self._client.execute_command(*args, **kwargs)
        except Exception:
            _record_failure()
            raise
        _record_success()
        return result


_cache: redis.Redis | None = None
_session: redis.Redis | None = None


async def get_cache_redis() -> _GuardedRedis | None:
    """Return a guarded cache-Redis client, or None while the breaker is open."""
    if _breaker_open_until != 0.0 and time.monotonic() < _breaker_open_until:
        return None
    try:
        global _cache
        if _cache is None:
            _cache = redis.from_url(
                settings.redis_cache_url,
                decode_responses=True,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
            )
        return _GuardedRedis(_cache)
    except Exception:
        _record_failure()
        return None


async def get_session_redis() -> _GuardedRedis | None:
    """Return a guarded session-Redis client, or None while the breaker is open."""
    if _breaker_open_until != 0.0 and time.monotonic() < _breaker_open_until:
        return None
    try:
        global _session
        if _session is None:
            _session = redis.from_url(
                settings.redis_session_url,
                decode_responses=True,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
            )
        return _GuardedRedis(_session)
    except Exception:
        _record_failure()
        return None


async def cache_get(key: str) -> str | None:
    client = await get_cache_redis()
    if client is None:
        return None
    try:
        return await client.get(key)
    except Exception:
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
    global _cache, _session
    for client in (_cache, _session):
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
    _cache = None
    _session = None
