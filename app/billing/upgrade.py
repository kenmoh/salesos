"""Immediate plan subscription / upgrade pricing and activation.

Pricing is computed server-side from the plan's price and the platform annual
discount setting. When moving between two paid plans, the unused days of the
current period are credited toward the new charge (day-based, not full
proration). Downgrades remain staged to ``pending_plan_id`` for period end.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import (
    BillingPeriod,
    MerchantSubscription,
    SubscriptionPlan,
)
from app.core.config import settings

MONTHLY_DAYS = 30
ANNUAL_DAYS = 365


def money(value) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def period_days(interval: str) -> int:
    return ANNUAL_DAYS if interval == "annual" else MONTHLY_DAYS


async def plan_price(
    session: AsyncSession, plan: SubscriptionPlan, interval: str
) -> Decimal:
    """Server-side price for a plan and billing interval."""
    monthly = money(plan.monthly_price)
    if interval != "annual":
        return monthly
    from app.platform.models import PlatformSetting

    setting = await session.scalar(
        select(PlatformSetting).where(PlatformSetting.key == "annual_discount_pct")
    )
    try:
        discount = Decimal(str(setting.value)) if setting and setting.value else Decimal("17")
    except Exception:
        discount = Decimal("17")
    discount = min(max(discount, Decimal("0")), Decimal("100"))
    return money(monthly * 12 * (1 - discount / 100))


async def resolve_plan(session: AsyncSession, plan_ref: str) -> SubscriptionPlan | None:
    """Accept a plan slug or UUID."""
    try:
        plan_id = UUID(str(plan_ref))
    except (ValueError, TypeError, AttributeError):
        plan_id = None
    if plan_id:
        result = await session.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.id == plan_id,
                SubscriptionPlan.is_active.is_(True),
            )
        )
    else:
        result = await session.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.slug == str(plan_ref),
                SubscriptionPlan.is_active.is_(True),
            )
        )
    return result.scalar_one_or_none()


async def build_quote(
    session: AsyncSession,
    tenant_id: UUID,
    plan: SubscriptionPlan,
    interval: str,
    sub: MerchantSubscription,
) -> dict:
    """Compute gross, unused-time credit, and amount due for an immediate upgrade."""
    days = period_days(interval)
    gross = await plan_price(session, plan, interval)

    current = await session.get(SubscriptionPlan, sub.plan_id)
    credit = money(0)
    if (
        current
        and current.id != plan.id
        and money(current.monthly_price) > 0
        and money(plan.monthly_price) > money(current.monthly_price)
    ):
        end = sub.current_period_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        remaining = max(0, (end - datetime.now(UTC)).total_seconds() / 86400)
        if remaining > 0:
            current_days = period_days(sub.billing_interval)
            credit = money(
                money(current.monthly_price)
                * Decimal(str(remaining))
                / Decimal(current_days)
            )

    amount_due = max(money(0), money(gross - credit))

    monthly = money(plan.monthly_price)
    if interval == "annual":
        base_price = money(monthly * 12)
        discount_pct = (
            float(round((Decimal("1") - gross / base_price) * 100, 2))
            if base_price > 0
            else 0.0
        )
    else:
        base_price = monthly
        discount_pct = 0.0

    return {
        "gross": float(gross),
        "base_price": float(base_price),
        "discount_pct": discount_pct,
        "discount_amount": float(base_price - gross),
        "credit": float(credit),
        "amount_due": float(amount_due),
        "interval": interval,
        "period_days": days,
        "plan_id": str(plan.id),
        "plan_slug": plan.slug,
        "plan_name": plan.name,
        "currency": plan.currency or "NGN",
    }


async def apply_plan_upgrade(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    plan: SubscriptionPlan,
    interval: str,
    actor_id: str | None = None,
) -> dict:
    """Activate a plan immediately: reset the period, open a fresh billing period."""
    sub = (
        await session.execute(
            select(MerchantSubscription).where(MerchantSubscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not sub:
        raise ValueError("subscription_not_found")

    now = datetime.now(UTC)
    end = now + timedelta(days=period_days(interval))
    sub.plan_id = plan.id
    sub.pending_plan_id = None
    sub.plan_change_requested_at = None
    sub.billing_interval = interval
    sub.current_period_start = now
    sub.current_period_end = end
    sub.next_billing_date = end
    sub.status = "active"
    sub.grace_period_end = None
    await session.flush()

    period = BillingPeriod(
        tenant_id=tenant_id,
        period_start=now,
        period_end=end,
        status="open",
        invoice_generated=False,
    )
    session.add(period)
    await session.flush()

    from app.billing.limits import invalidate_plan_limits

    await invalidate_plan_limits(str(tenant_id))
    return {"subscription_id": str(sub.id), "period_id": str(period.id), "actor_id": actor_id}
