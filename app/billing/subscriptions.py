"""Subscription lifecycle with 30-day billing periods.

Proration policy: **none**. A plan change requested during a billing period
is staged in ``pending_plan_id`` and applied when the period rolls over, so the
subscription charge for the current period is never altered.

Transaction fees always follow the plan that was effective when the sale was
completed, captured at fee-registration time.
"""

from datetime import datetime, timedelta, UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import (
    BillingPeriod,
    MerchantSubscription,
    SubscriptionPlan,
)

PERIOD_DAYS = 30
GRACE_DAYS = 7
DUE_DAYS = 7


async def ensure_subscription(
    session: AsyncSession, tenant_id: UUID
) -> MerchantSubscription:
    """Get or create a Free-tier 30-day subscription for a tenant."""
    sub = await get_tenant_subscription(session, tenant_id)
    if sub:
        return sub

    result = await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.slug == "free")
    )
    free = result.scalar_one()
    now = datetime.now(UTC)
    end = now + timedelta(days=PERIOD_DAYS)
    sub = MerchantSubscription(
        tenant_id=tenant_id,
        plan_id=free.id,
        status="active",
        billing_interval="monthly",
        current_period_start=now,
        current_period_end=end,
        next_billing_date=end,
        auto_debit_enabled=True,
    )
    session.add(sub)
    await session.flush()
    await open_period_for_subscription(session, sub)
    return sub


async def change_plan(
    session: AsyncSession,
    tenant_id: UUID,
    new_plan_slug: str,
    actor_id: UUID | None = None,
) -> MerchantSubscription | None:
    """Stage a plan change to take effect at the start of the next period.

    Returns the subscription, or None if the plan slug is invalid. The current
    period's charge is unaffected — no proration is applied.
    """
    result = await session.execute(
        select(SubscriptionPlan).where(
            SubscriptionPlan.slug == new_plan_slug,
            SubscriptionPlan.is_active.is_(True),
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        return None

    sub = await ensure_subscription(session, tenant_id)
    if plan.id == sub.plan_id:
        # Already on this plan — clear any staged change and leave as-is.
        sub.pending_plan_id = None
        sub.plan_change_requested_at = None
        await session.flush()
        return sub

    sub.pending_plan_id = plan.id
    sub.plan_change_requested_at = datetime.now(UTC)
    await session.flush()
    return sub


async def apply_pending_plan(session: AsyncSession, sub: MerchantSubscription) -> bool:
    """Apply a staged plan change. Called when a billing period rolls over."""
    if not sub.pending_plan_id:
        return False
    sub.plan_id = sub.pending_plan_id
    sub.pending_plan_id = None
    sub.plan_change_requested_at = None
    await session.flush()
    return True


async def open_period_for_subscription(
    session: AsyncSession, sub: MerchantSubscription
) -> BillingPeriod:
    """Get or create the billing period for a subscription's current cycle."""
    result = await session.execute(
        select(BillingPeriod).where(
            BillingPeriod.tenant_id == sub.tenant_id,
            BillingPeriod.period_start == sub.current_period_start,
        )
    )
    period = result.scalar_one_or_none()
    if period:
        return period
    period = BillingPeriod(
        tenant_id=sub.tenant_id,
        period_start=sub.current_period_start,
        period_end=sub.current_period_end,
        status="open",
    )
    session.add(period)
    await session.flush()
    return period


async def advance_period(session: AsyncSession, sub: MerchantSubscription) -> MerchantSubscription:
    """Move a subscription to its next 30-day cycle and apply staged plan changes."""
    now = sub.current_period_end
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=PERIOD_DAYS)
    sub.next_billing_date = sub.current_period_end
    if sub.status == "past_due":
        sub.status = "active"
        sub.grace_period_end = None
    await apply_pending_plan(session, sub)
    await session.flush()
    return sub


async def get_tenant_subscription(
    session: AsyncSession, tenant_id: UUID
) -> MerchantSubscription | None:
    result = await session.execute(
        select(MerchantSubscription).where(MerchantSubscription.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()
