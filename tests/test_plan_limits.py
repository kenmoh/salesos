"""Tests for the generic feature-limit helpers and the three new quota counters."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.billing.limits import (
    DOCUMENTS_FEATURE_KEY,
    STAFF_FEATURE_KEY,
    STORES_FEATURE_KEY,
    PlanLimitError,
    count_tenant_documents,
    count_tenant_stores,
    count_tenant_users,
    enforce_limit,
    feature_limit_for_tenant,
    parse_feature_limit,
)
from app.core.redis_client import cache_del


def test_parse_all_feature_value_formats():
    assert parse_feature_limit("2 users") == 2
    assert parse_feature_limit("20 users") == 20
    assert parse_feature_limit("1") == 1
    assert parse_feature_limit("5") == 5
    assert parse_feature_limit("35/mo") == 35
    assert parse_feature_limit("250/mo") == 250
    assert parse_feature_limit("1000/mo") == 1000
    assert parse_feature_limit("Unlimited") is None


def _session(plan_id=None, limits=None, count=0):
    """Session mock answering plan/feature/count queries by SQL fragment."""
    session = MagicMock()

    sub_result = MagicMock()
    sub_result.scalar_one_or_none.return_value = plan_id
    sub_result.one_or_none.return_value = None

    feature_result = MagicMock()
    feature = MagicMock()
    feature.limit_value = None
    feature.feature_value = "Unlimited"
    if limits and plan_id in limits:
        feature.limit_value = limits[plan_id]
    feature_result.scalar_one_or_none.return_value = feature if plan_id else None

    count_result = MagicMock()
    count_result.scalar.return_value = count
    count_result.one_or_none.return_value = None

    async def execute(stmt, *a, **k):
        text = str(stmt)
        if "merchant_subscriptions" in text:
            return sub_result
        if "plan_features" in text:
            return feature_result
        return count_result

    session.execute = AsyncMock(side_effect=execute)
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "feature_key",
    [STAFF_FEATURE_KEY, STORES_FEATURE_KEY, DOCUMENTS_FEATURE_KEY],
)
async def test_feature_limit_for_each_key(feature_key):
    tenant = str(uuid4())
    key = f"sm:plan_limit:{tenant}:{feature_key}"
    await cache_del(key)
    plan_id = uuid4()
    session = _session(plan_id=plan_id, limits={plan_id: 7})
    assert await feature_limit_for_tenant(tenant, feature_key, session=session) == 7
    await cache_del(key)


@pytest.mark.asyncio
async def test_enforce_limit_blocks_and_reports_resource():
    tenant = str(uuid4())
    key = f"sm:plan_limit:{tenant}:{STAFF_FEATURE_KEY}"
    await cache_del(key)
    plan_id = uuid4()
    session = _session(plan_id=plan_id, limits={plan_id: 2})
    with pytest.raises(PlanLimitError) as exc:
        await enforce_limit(tenant, STAFF_FEATURE_KEY, "user", 2, session)
    assert exc.value.limit == 2
    assert exc.value.resource == "user"
    await cache_del(key)


@pytest.mark.asyncio
async def test_enforce_limit_allows_under_cap():
    tenant = str(uuid4())
    key = f"sm:plan_limit:{tenant}:{STORES_FEATURE_KEY}"
    await cache_del(key)
    plan_id = uuid4()
    session = _session(plan_id=plan_id, limits={plan_id: 5})
    await enforce_limit(tenant, STORES_FEATURE_KEY, "store", 4, session)
    await cache_del(key)


@pytest.mark.asyncio
async def test_count_tenant_users():
    tenant = str(uuid4())
    session = _session(count=3)
    assert await count_tenant_users(tenant, session) == 3


@pytest.mark.asyncio
async def test_count_tenant_stores():
    tenant = str(uuid4())
    session = _session(count=2)
    assert await count_tenant_stores(tenant, session) == 2


@pytest.mark.asyncio
async def test_count_tenant_documents_uses_billing_period():
    tenant = str(uuid4())
    now = datetime.now(UTC)
    start = now - timedelta(days=5)
    end = now + timedelta(days=25)

    session = MagicMock()
    period_result = MagicMock()
    period_result.one_or_none.return_value = (start, end)
    doc_result = MagicMock()
    doc_result.scalar.return_value = 12

    async def execute(stmt, *a, **k):
        if "merchant_subscriptions" in str(stmt):
            return period_result
        return doc_result

    session.execute = AsyncMock(side_effect=execute)
    assert await count_tenant_documents(tenant, session) == 12
