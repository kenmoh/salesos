"""Per-store product quota from plan_features.

The numeric cap lives in ``plan_features.limit_value`` (backfilled from the
human-readable ``feature_value``). The tenant's current plan comes from
``merchant_subscriptions.plan_id`` — staged ``pending_plan_id`` changes do
not take effect until the billing period rolls over.
"""

import re
from uuid import UUID

from sqlalchemy import func, select

from app.billing.models import MerchantSubscription
from app.core.redis_client import cache_del, cache_get, cache_set

PRODUCTS_FEATURE_KEY = "products"
_CACHE_TTL = 300
_CACHE_KEY = "sm:product_limit:{tenant_id}"


class PlanLimitError(Exception):
    """Raised when a store has reached its plan's product cap."""

    def __init__(self, limit: int, used: int):
        self.limit = limit
        self.used = used
        super().__init__(f"product_limit_reached:{limit}")


def parse_feature_limit(value: str | None) -> int | None:
    """Parse a display value like '1,000 products/store' into 1000."""
    if not value:
        return None
    match = re.search(r"\d[\d,]*", str(value))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


async def _read_limit(tenant_id: str, session) -> int | None:
    from uuid import UUID as _UUID

    from app.billing.models import PlanFeature

    try:
        result = await session.execute(
            select(MerchantSubscription.plan_id).where(
                MerchantSubscription.tenant_id == _UUID(tenant_id)
            )
        )
        plan_id = result.scalar_one_or_none()
        if not plan_id:
            return None
        feature_result = await session.execute(
            select(PlanFeature).where(
                PlanFeature.plan_id == plan_id,
                PlanFeature.feature_key == PRODUCTS_FEATURE_KEY,
            )
        )
        feature = feature_result.scalar_one_or_none()
        if not feature:
            return None
        value = feature.limit_value or parse_feature_limit(feature.feature_value)
        return value if isinstance(value, int) else None
    except Exception:
        # A failed limit lookup fails open — creation is never blocked by an
        # infrastructure hiccup. The TTL keeps this rare.
        return None


async def product_limit_for_tenant(tenant_id: str, session=None) -> int | None:
    """The per-store product cap for a tenant's current plan (None = unlimited)."""
    key = _CACHE_KEY.format(tenant_id=tenant_id)
    cached = await cache_get(key)
    if cached == "none":
        return None
    if cached is not None:
        try:
            return int(cached)
        except ValueError:
            pass

    async def _read(s) -> int | None:
        return await _read_limit(tenant_id, s)

    if session is not None:
        limit = await _read(session)
    else:
        from app.core.dependencies import _get_session_factory

        factory = _get_session_factory()
        async with factory() as s:
            limit = await _read(s)

    await cache_set(key, "none" if limit is None else str(limit), _CACHE_TTL)
    return limit


async def invalidate_product_limit(tenant_id: str) -> None:
    await cache_del(_CACHE_KEY.format(tenant_id=tenant_id))


async def invalidate_product_limit_for_plan(plan_id: UUID, session) -> None:
    """Call when a plan feature is edited — invalidates every tenant on that plan."""
    from app.billing.models import MerchantSubscription

    rows = await session.execute(
        select(MerchantSubscription.tenant_id).where(
            MerchantSubscription.plan_id == plan_id
        )
    )
    for (tenant_id,) in rows.all():
        await invalidate_product_limit(str(tenant_id))


async def count_store_products(tenant_id: str, store_id: str, session) -> int:
    from app.stores.models import StoreProduct

    result = await session.execute(
        select(func.count(StoreProduct.id)).where(
            StoreProduct.tenant_id == UUID(tenant_id),
            StoreProduct.store_id == UUID(store_id),
        )
    )
    return result.scalar() or 0


async def enforce_store_product_limit(tenant_id: str, store_id: str, session) -> None:
    """Raise PlanLimitError when this store has reached its product cap."""
    limit = await product_limit_for_tenant(tenant_id, session=session)
    if not isinstance(limit, int):
        return
    used = await count_store_products(tenant_id, store_id, session)
    if used >= limit:
        raise PlanLimitError(limit=limit, used=used)
