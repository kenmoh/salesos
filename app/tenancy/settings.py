"""Tenant-scoped feature flags stored in ``tenants.settings``.

Tenant settings are written through ``PATCH /business/settings`` and read on
the sales hot path, so the price-override flag is cached and the cache is
invalidated on write.
"""

import json
from uuid import UUID

from sqlalchemy import select

from app.core.redis_client import cache_del, cache_get, cache_set
from app.tenancy.models import Tenant

PRICE_OVERRIDE_SETTING = "allow_price_override"

_PRICE_OVERRIDE_KEY = "sm:price_override:{business_id}"
_TTL_SECONDS = 300


def parse_tenant_settings(raw) -> dict:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return raw if isinstance(raw, dict) else {}


def price_override_enabled(raw_settings) -> bool:
    return bool(parse_tenant_settings(raw_settings).get(PRICE_OVERRIDE_SETTING, False))


def currency_code(raw_settings) -> str:
    """The tenant's currency code, defaulting to NGN."""
    from app.common.currency import DEFAULT_CURRENCY, normalize_currency

    return normalize_currency(parse_tenant_settings(raw_settings).get("currency")) or DEFAULT_CURRENCY


async def is_price_override_enabled(business_id: str, session=None) -> bool:
    """Whether the owner has enabled per-line price overrides for this tenant."""
    key = _PRICE_OVERRIDE_KEY.format(business_id=business_id)
    cached = await cache_get(key)
    if cached is not None:
        return cached == "1"

    async def _read(s) -> bool:
        result = await s.execute(
            select(Tenant.settings).where(Tenant.id == UUID(business_id))
        )
        return price_override_enabled(result.scalar_one_or_none())

    if session is not None:
        enabled = await _read(session)
    else:
        from app.core.dependencies import _get_session_factory

        factory = _get_session_factory()
        async with factory() as s:
            enabled = await _read(s)

    await cache_set(key, "1" if enabled else "0", _TTL_SECONDS)
    return enabled


async def invalidate_price_override(business_id: str) -> None:
    await cache_del(_PRICE_OVERRIDE_KEY.format(business_id=business_id))
