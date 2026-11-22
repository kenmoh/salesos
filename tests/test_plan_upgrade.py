"""Plan upgrade pricing: monthly/annual, paid-to-paid credit, activation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.billing.upgrade import (
    ANNUAL_DAYS,
    MONTHLY_DAYS,
    build_quote,
    money,
    period_days,
    plan_price,
    resolve_plan,
)


def test_money_rounding():
    assert money("3500") == Decimal("3500.00")
    assert money(None) == Decimal("0.00")


def test_period_days():
    assert period_days("monthly") == MONTHLY_DAYS
    assert period_days("annual") == ANNUAL_DAYS
    assert period_days("other") == MONTHLY_DAYS


@pytest.mark.asyncio
async def test_plan_price_monthly_does_not_need_settings():
    session = MagicMock()
    plan = MagicMock()
    plan.monthly_price = Decimal("7500")
    assert await plan_price(session, plan, "monthly") == Decimal("7500.00")


@pytest.mark.asyncio
async def test_plan_price_annual_uses_discount_setting():
    session = MagicMock()
    plan = MagicMock()
    plan.id = uuid4()
    plan.monthly_price = Decimal("7500")
    setting = MagicMock()
    setting.value = "17"
    session.scalar = AsyncMock(return_value=setting)
    result = await plan_price(session, plan, "annual")
    assert result == Decimal("74700.00")


@pytest.mark.asyncio
async def test_resolve_plan_by_slug_or_id():
    session = MagicMock()
    plan = MagicMock()
    plan.slug = "basic"
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=plan)))
    assert await resolve_plan(session, "basic") is plan
    assert await resolve_plan(session, str(uuid4())) is plan


@pytest.mark.asyncio
async def test_quote_paid_to_paid_credits_unused_days():
    session = MagicMock()
    plan = MagicMock()
    plan.id = uuid4()
    plan.slug = "pro"
    plan.name = "Pro"
    plan.monthly_price = Decimal("19500")
    plan.currency = "NGN"

    current = MagicMock()
    current.id = uuid4()
    current.monthly_price = Decimal("7500")

    sub = MagicMock()
    sub.plan_id = current.id
    sub.billing_interval = "monthly"
    sub.current_period_end = datetime.now(UTC) + timedelta(days=20)
    sub.current_period_start = datetime.now(UTC) - timedelta(days=10)

    session.get = AsyncMock(return_value=current)
    setting = MagicMock()
    setting.value = "17"
    session.scalar = AsyncMock(return_value=setting)

    quote = await build_quote(session, uuid4(), plan, "monthly", sub)
    assert quote["gross"] == 19500.0
    assert quote["credit"] == pytest.approx(5000.0, abs=1)
    assert quote["amount_due"] == pytest.approx(14500.0, abs=1)


@pytest.mark.asyncio
async def test_quote_free_to_paid_has_no_credit():
    session = MagicMock()
    plan = MagicMock()
    plan.id = uuid4()
    plan.slug = "basic"
    plan.name = "Basic"
    plan.monthly_price = Decimal("7500")
    plan.currency = "NGN"

    current = MagicMock()
    current.id = uuid4()
    current.monthly_price = Decimal("0")

    sub = MagicMock()
    sub.plan_id = current.id
    sub.billing_interval = "monthly"
    sub.current_period_end = datetime.now(UTC) + timedelta(days=20)
    sub.current_period_start = datetime.now(UTC) - timedelta(days=10)

    session.get = AsyncMock(return_value=current)

    quote = await build_quote(session, uuid4(), plan, "monthly", sub)
    assert quote["credit"] == 0.0
    assert quote["amount_due"] == 7500.0
