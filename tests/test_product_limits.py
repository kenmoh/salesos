"""Per-store product quotas sourced from plan_features.limit_value."""

from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.billing.limits import (
    PlanLimitError,
    enforce_store_product_limit,
    parse_feature_limit,
    product_limit_for_tenant,
)
from app.core.redis_client import cache_del


def test_parse_feature_limit():
    assert parse_feature_limit("100 products") == 100
    assert parse_feature_limit("1,000 products/store") == 1000
    assert parse_feature_limit("10,000 products") == 10000
    assert parse_feature_limit("Unlimited") is None
    assert parse_feature_limit(None) is None


def _session_with(plan_id=None, limit_value=None, feature_value="100 products"):
    """Build a session mock whose execute() answers the two quota queries."""
    session = MagicMock()

    sub_result = MagicMock()
    sub_result.scalar_one_or_none.return_value = plan_id
    feature = MagicMock()
    feature.limit_value = limit_value
    feature.feature_value = feature_value
    feature_result = MagicMock()
    feature_result.scalar_one_or_none.return_value = feature if plan_id else None
    count_result = MagicMock()
    count_result.scalar.return_value = 0

    async def execute(stmt, *args, **kwargs):
        text = str(stmt)
        if "merchant_subscriptions" in text:
            return sub_result
        if "plan_features" in text:
            return feature_result
        if "store_products" in text:
            return count_result
        return MagicMock()

    session.execute = AsyncMock(side_effect=execute)
    return session


@pytest.mark.asyncio
async def test_product_limit_reads_numeric_column():
    tenant = str(uuid4())
    await cache_del(f"sm:product_limit:{tenant}")
    plan_id = uuid4()
    session = _session_with(plan_id=plan_id, limit_value=250, feature_value="100 products")
    assert await product_limit_for_tenant(tenant, session=session) == 250
    await cache_del(f"sm:product_limit:{tenant}")


@pytest.mark.asyncio
async def test_product_limit_falls_back_to_parsing_display_value():
    tenant = str(uuid4())
    await cache_del(f"sm:product_limit:{tenant}")
    plan_id = uuid4()
    session = _session_with(plan_id=plan_id, limit_value=None, feature_value="1,000 products")
    assert await product_limit_for_tenant(tenant, session=session) == 1000
    await cache_del(f"sm:product_limit:{tenant}")


@pytest.mark.asyncio
async def test_enforce_blocks_at_cap():
    tenant = str(uuid4())
    store = str(uuid4())
    await cache_del(f"sm:product_limit:{tenant}")
    plan_id = uuid4()
    session = _session_with(plan_id=plan_id, limit_value=2)
    count_result = MagicMock()
    count_result.scalar.return_value = 2

    original = session.execute.side_effect

    async def execute(stmt, *args, **kwargs):
        if "store_products" in str(stmt):
            return count_result
        return await original(stmt, *args, **kwargs)

    session.execute = AsyncMock(side_effect=execute)
    with pytest.raises(PlanLimitError) as exc:
        await enforce_store_product_limit(tenant, store, session)
    assert exc.value.limit == 2
    assert exc.value.used == 2
    await cache_del(f"sm:product_limit:{tenant}")


@pytest.mark.asyncio
async def test_enforce_allows_under_cap():
    tenant = str(uuid4())
    store = str(uuid4())
    await cache_del(f"sm:product_limit:{tenant}")
    plan_id = uuid4()
    session = _session_with(plan_id=plan_id, limit_value=5)
    count_result = MagicMock()
    count_result.scalar.return_value = 4

    original = session.execute.side_effect

    async def execute(stmt, *args, **kwargs):
        if "store_products" in str(stmt):
            return count_result
        return await original(stmt, *args, **kwargs)

    session.execute = AsyncMock(side_effect=execute)
    await enforce_store_product_limit(tenant, store, session)
    await cache_del(f"sm:product_limit:{tenant}")
