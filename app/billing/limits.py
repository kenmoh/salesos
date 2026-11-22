"""Plan-feature-based quotas (products, users, stores, documents).

The numeric cap lives in ``plan_features.limit_value`` when populated, else
it is parsed from the human-readable ``feature_value`` (e.g. "20 users",
"35/mo"). The tenant's current plan comes from
``merchant_subscriptions.plan_id`` — staged ``pending_plan_id`` changes do
not take effect until the billing period rolls over.
"""

import re
from uuid import UUID

from datetime import UTC

from sqlalchemy import func, select

from app.billing.models import MerchantSubscription
from app.core.redis_client import cache_del, cache_get, cache_set

PRODUCTS_FEATURE_KEY = "products"
STAFF_FEATURE_KEY = "staff"
STORES_FEATURE_KEY = "stores"
DOCUMENTS_FEATURE_KEY = "documents"

_ALL_FEATURE_KEYS = (
    PRODUCTS_FEATURE_KEY,
    STAFF_FEATURE_KEY,
    STORES_FEATURE_KEY,
    DOCUMENTS_FEATURE_KEY,
)

_CACHE_TTL = 300
_CACHE_KEY = "sm:plan_limit:{tenant_id}:{feature_key}"
# Legacy key used by the original product-only implementation.
_PRODUCT_LEGACY_CACHE_KEY = "sm:product_limit:{tenant_id}"


class PlanLimitError(Exception):
    """Raised when a tenant has reached a plan cap."""

    def __init__(self, limit: int, used: int, resource: str = "item"):
        self.limit = limit
        self.used = used
        self.resource = resource
        super().__init__(f"{resource}_limit_reached:{limit}")


def parse_feature_limit(value: str | None) -> int | None:
    """Parse a display value like '1,000 products/store' or '35/mo' into an int."""
    if not value:
        return None
    match = re.search(r"\d[\d,]*", str(value))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


async def feature_limit_for_tenant(
    tenant_id: str, feature_key: str, session=None
) -> int | None:
    """The cap for a feature on the tenant's current plan (None = unlimited)."""
    key = _CACHE_KEY.format(tenant_id=tenant_id, feature_key=feature_key)
    cached = await cache_get(key)
    if cached == "none":
        return None
    if cached is not None:
        try:
            return int(cached)
        except ValueError:
            pass

    async def _read(s) -> int | None:
        from app.billing.models import PlanFeature
        from uuid import UUID as _UUID

        try:
            result = await s.execute(
                select(MerchantSubscription.plan_id).where(
                    MerchantSubscription.tenant_id == _UUID(tenant_id)
                )
            )
            plan_id = result.scalar_one_or_none()
            if not plan_id:
                return None
            feature_result = await s.execute(
                select(PlanFeature).where(
                    PlanFeature.plan_id == plan_id,
                    PlanFeature.feature_key == feature_key,
                )
            )
            feature = feature_result.scalar_one_or_none()
            if not feature:
                return None
            value = feature.limit_value or parse_feature_limit(feature.feature_value)
            return value if isinstance(value, int) else None
        except Exception:
            # A failed limit lookup fails open — creation is never blocked by
            # an infrastructure hiccup. The TTL keeps this rare.
            return None

    if session is not None:
        limit = await _read(session)
    else:
        from app.core.dependencies import _get_session_factory

        factory = _get_session_factory()
        async with factory() as s:
            limit = await _read(s)

    await cache_set(key, "none" if limit is None else str(limit), _CACHE_TTL)
    return limit


async def product_limit_for_tenant(tenant_id: str, session=None) -> int | None:
    """The per-store product cap (backwards-compatible wrapper)."""
    return await feature_limit_for_tenant(tenant_id, PRODUCTS_FEATURE_KEY, session)


async def invalidate_plan_limits(tenant_id: str) -> None:
    """Invalidate every cached feature limit for a tenant."""
    for key in _ALL_FEATURE_KEYS:
        await cache_del(_CACHE_KEY.format(tenant_id=tenant_id, feature_key=key))
    await cache_del(_PRODUCT_LEGACY_CACHE_KEY.format(tenant_id=tenant_id))


async def invalidate_product_limit(tenant_id: str) -> None:
    await invalidate_plan_limits(tenant_id)


async def invalidate_plan_limits_for_plan(plan_id: UUID, session) -> None:
    """Call when a plan feature is edited — invalidates every tenant on that plan."""
    rows = await session.execute(
        select(MerchantSubscription.tenant_id).where(
            MerchantSubscription.plan_id == plan_id
        )
    )
    for (tenant_id,) in rows.all():
        await invalidate_plan_limits(str(tenant_id))


async def invalidate_product_limit_for_plan(plan_id: UUID, session) -> None:
    """Backwards-compatible alias."""
    await invalidate_plan_limits_for_plan(plan_id, session)


# ── Counters ────────────────────────────────────────────────────────────────


async def count_store_products(tenant_id: str, store_id: str, session) -> int:
    from app.stores.models import StoreProduct

    result = await session.execute(
        select(func.count(StoreProduct.id)).where(
            StoreProduct.tenant_id == UUID(tenant_id),
            StoreProduct.store_id == UUID(store_id),
        )
    )
    return result.scalar() or 0


async def count_tenant_users(tenant_id: str, session) -> int:
    from app.identity.models import User

    result = await session.execute(
        select(func.count(User.id)).where(User.tenant_id == UUID(tenant_id))
    )
    return result.scalar() or 0


async def count_tenant_stores(tenant_id: str, session) -> int:
    from app.stores.models import Store

    result = await session.execute(
        select(func.count(Store.id)).where(
            Store.tenant_id == UUID(tenant_id),
            Store.status != "deleted",
        )
    )
    return result.scalar() or 0


async def count_tenant_documents(tenant_id: str, session) -> int:
    """Documents created in the tenant's current 30-day billing period, all types."""
    from app.documents.models import Document

    try:
        result = await session.execute(
            select(MerchantSubscription.current_period_start, MerchantSubscription.current_period_end).where(
                MerchantSubscription.tenant_id == UUID(tenant_id)
            )
        )
        period = result.one_or_none()
        if not period or not isinstance(period, tuple) or not period[0] or not period[1]:
            return 0
        start, end = period[0], period[1]
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)

        doc_result = await session.execute(
            select(func.count(Document.id)).where(
                Document.tenant_id == UUID(tenant_id),
                Document.created_at >= start,
                Document.created_at < end,
            )
        )
        return doc_result.scalar() or 0
    except Exception:
        # A failed count fails open — document creation is never blocked by an
        # infrastructure hiccup.
        return 0


# ── Enforcement ────────────────────────────────────────────────────────────


async def enforce_limit(
    tenant_id: str,
    feature_key: str,
    resource: str,
    used: int,
    session=None,
) -> None:
    """Raise PlanLimitError when used has reached the cap."""
    limit = await feature_limit_for_tenant(tenant_id, feature_key, session=session)
    if not isinstance(limit, int):
        return
    if used >= limit:
        raise PlanLimitError(limit=limit, used=used, resource=resource)


async def enforce_store_product_limit(tenant_id: str, store_id: str, session) -> None:
    limit = await product_limit_for_tenant(tenant_id, session=session)
    if not isinstance(limit, int):
        return
    used = await count_store_products(tenant_id, store_id, session)
    if used >= limit:
        raise PlanLimitError(limit=limit, used=used, resource="product")
