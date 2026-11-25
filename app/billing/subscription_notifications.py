"""Subscription lifecycle notifications for paid tenants."""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import MerchantSubscription, SubscriptionPlan
from app.notifications.repository import (
    create_in_app_notification,
    has_debt_cap_notification,
    has_subscription_reminder,
)

REMINDER_DAYS = (7, 3)
ACTIVE_SUBSCRIPTION_STATUSES = ("active", "past_due")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_day_window(now: datetime, days_remaining: int) -> tuple[datetime, datetime]:
    target_date = _as_utc(now).date() + timedelta(days=days_remaining)
    start = datetime.combine(target_date, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _reminder_key(subscription: MerchantSubscription, event: str) -> str:
    period_end = _as_utc(subscription.current_period_end).isoformat()
    return f"subscription:{subscription.id}:{period_end}:{event}"


async def notify_debt_cap_reached(
    session: AsyncSession,
    subscription: MerchantSubscription,
    *,
    event_reference: str,
) -> bool:
    """Create one notice for a billable event that reaches the debt cap."""
    if subscription.accrued_debt < subscription.debt_cap:
        return False

    key = f"debt_cap:{subscription.id}:{event_reference}"
    if await has_debt_cap_notification(session, subscription.tenant_id, key):
        return False

    debt_cap = subscription.debt_cap
    await create_in_app_notification(
        session=session,
        tenant_id=subscription.tenant_id,
        type="system",
        title="Billing debt limit reached",
        body=(
            f"Your accrued billing debt has reached the NGN {debt_cap:,.2f} limit. "
            "New billable actions may be blocked until you add funds."
        ),
        meta={
            "debt_cap_notification_key": key,
            "subscription_id": str(subscription.id),
            "event": "debt_cap_reached",
            "event_reference": event_reference,
            "accrued_debt": str(subscription.accrued_debt),
            "debt_cap": str(debt_cap),
        },
    )
    return True


async def _create_subscription_notification(
    session: AsyncSession,
    subscription: MerchantSubscription,
    *,
    event: str,
    title: str,
    body: str,
    days_remaining: int | None = None,
) -> bool:
    key = _reminder_key(subscription, event)
    if await has_subscription_reminder(session, subscription.tenant_id, key):
        return False

    await create_in_app_notification(
        session=session,
        tenant_id=subscription.tenant_id,
        type="system",
        title=title,
        body=body,
        meta={
            "subscription_reminder_key": key,
            "subscription_id": str(subscription.id),
            "current_period_end": _as_utc(subscription.current_period_end).isoformat(),
            "event": event,
            "days_remaining": days_remaining,
        },
    )
    return True


async def send_due_subscription_reminders(
    session: AsyncSession,
    now: datetime | None = None,
) -> int:
    """Create the 7-day and 3-day reminders due on the current UTC date."""
    now = _as_utc(now or datetime.now(UTC))
    query = (
        select(MerchantSubscription)
        .join(SubscriptionPlan, SubscriptionPlan.id == MerchantSubscription.plan_id)
        .where(
            SubscriptionPlan.slug != "free",
            MerchantSubscription.status.in_(ACTIVE_SUBSCRIPTION_STATUSES),
        )
    )
    created = 0

    for days_remaining in REMINDER_DAYS:
        start, end = _utc_day_window(now, days_remaining)
        result = await session.execute(
            query.where(
                MerchantSubscription.current_period_end >= start,
                MerchantSubscription.current_period_end < end,
            )
        )
        for subscription in result.scalars().all():
            date_text = _as_utc(subscription.current_period_end).date().isoformat()
            if await _create_subscription_notification(
                session,
                subscription,
                event=f"reminder_{days_remaining}_days",
                title="Subscription renewal reminder",
                body=(
                    f"Your paid subscription period ends in {days_remaining} days, "
                    f"on {date_text}. Renew before then to avoid interruption."
                ),
                days_remaining=days_remaining,
            ):
                created += 1

    return created


async def notify_subscription_expired(
    session: AsyncSession,
    subscription: MerchantSubscription,
    now: datetime | None = None,
) -> bool:
    """Create one notice when a paid subscription reaches its period end."""
    now = _as_utc(now or datetime.now(UTC))
    period_end = _as_utc(subscription.current_period_end)
    if (
        subscription.status not in ACTIVE_SUBSCRIPTION_STATUSES
        or period_end > now
    ):
        return False

    plan = await session.get(SubscriptionPlan, subscription.plan_id)
    if plan is None or plan.slug == "free":
        return False

    return await _create_subscription_notification(
        session,
        subscription,
        event="expired",
        title="Subscription period ended",
        body=(
            "Your paid subscription period ended on "
            f"{period_end.date().isoformat()}. Review your plan to maintain access "
            "to paid features."
        ),
    )