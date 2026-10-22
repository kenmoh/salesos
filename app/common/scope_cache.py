"""Granular, version-bumped caching for the hot read paths.

POS traffic is read-heavy: the product grid, categories, stores and the
report screen open constantly, while writes touch one store or scope. The
version bump costs a single INCR, so a write does not need to enumerate
what it invalidated: a read embeds the current version in its key, so after
a bump the pre-write keys are gone from view and their TTL expiry reclaims
the storage without a scan.
"""

import hashlib
import json
from typing import Any

from app.core.redis_client import get_cache_redis

CATALOG = "catalog"
STORES = "stores"
REPORTS = "reports"
CUSTOMERS = "customers"

# Catalog ties to a single store as its prices and stock are per store;
# stores/customers/reports are all-tenant scopes and pass "-" as store.
TENANT_SCOPE = "-"


def _ver_key(tenant_id: str, store_id: str, scope: str) -> str:
    return f"sf:cver:{tenant_id}:{store_id}:{scope}"


def _cache_key(tenant_id: str, store_id: str, scope: str, version: int, fields: dict) -> str:
    raw = json.dumps(fields, sort_keys=True, default=str)
    digest = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return f"sf:cc:{tenant_id}:{store_id}:{scope}:v{version}:{digest}"


async def scope_read(
    tenant_id: str,
    store_id: str,
    scope: str,
    fields: dict,
    ttl: int,
    fetch: Any,
) -> Any:
    """One cache-checked read: Redis says yes, or ``fetch()`` says what the DB
    says. Keys embed the scope version, so a bump drops the read from cache
    reading then a short TTL cleans the stale keys."""
    client = await get_cache_redis()
    payload = None
    if client is not None:
        try:
            raw_version = await client.get(_ver_key(tenant_id, store_id, scope))
            version = int(raw_version) if raw_version is not None else 0
            payload = await client.get(_cache_key(tenant_id, store_id, scope, version, fields))
        except Exception:
            payload = None
    if payload is not None:
        try:
            return json.loads(payload)
        except Exception:
            payload = None
    result = await fetch()
    if client is not None:
        try:
            # re-read after a slow DB trip: a write can bump meanwhile.
            raw_version = await client.get(_ver_key(tenant_id, store_id, scope))
            version = int(raw_version) if raw_version is not None else 0
            await client.set(
                _cache_key(tenant_id, store_id, scope, version, fields),
                json.dumps(result, default=str),
                ex=ttl,
            )
        except Exception:
            pass
    return result


async def scope_bump(tenant_id: str, store_id: str, scopes: tuple[str, ...] | list[str]) -> None:
    """Age every cached read of a scope in one server round-trip."""
    client = await get_cache_redis()
    if client is None:
        return
    try:
        for scope in scopes:
            await client.incr(_ver_key(tenant_id, store_id, scope))
    except Exception:
        pass
