from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.billing.fees import _settle_fee
from app.billing.subscription_notifications import (
    _utc_day_window,
    notify_debt_cap_reached,
    notify_subscription_expired,
    send_due_subscription_reminders,
)


class FakeResult:
    def __init__(self, subscriptions=None):
        self._subscriptions = subscriptions or []

    def scalars(self):
        return SimpleNamespace(all=lambda: self._subscriptions)


def _subscription(*, days=7, status="active"):
    period_end = datetime(2026, 12, 2, 14, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        plan_id=uuid4(),
        status=status,
        current_period_end=period_end + timedelta(days=days - 7),
    )


def test_utc_day_window_uses_calendar_days():
    now = datetime(2026, 11, 25, 23, 30, tzinfo=UTC)

    start, end = _utc_day_window(now, 7)

    assert start == datetime(2026, 12, 2, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 12, 3, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_due_reminder_is_created_once_for_paid_subscription():
    subscription = _subscription(days=7)
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[FakeResult([subscription]), FakeResult()]
    )

    with (
        patch(
            "app.billing.subscription_notifications.has_subscription_reminder",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.billing.subscription_notifications.create_in_app_notification",
            new=AsyncMock(),
        ) as create_notification,
    ):
        created = await send_due_subscription_reminders(
            session, now=datetime(2026, 11, 25, 12, tzinfo=UTC)
        )

    assert created == 1
    create_notification.assert_awaited_once()
    assert create_notification.await_args.kwargs["type"] == "system"
    assert create_notification.await_args.kwargs["meta"]["days_remaining"] == 7
    compiled_query = session.execute.await_args_list[0].args[0].compile(
        dialect=postgresql.dialect()
    )
    assert "subscription_plans.slug !=" in compiled_query.string
    assert "free" in compiled_query.params.values()


@pytest.mark.asyncio
async def test_existing_reminder_is_not_created_again():
    subscription = _subscription(days=7)
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[FakeResult([subscription]), FakeResult()]
    )

    with (
        patch(
            "app.billing.subscription_notifications.has_subscription_reminder",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.billing.subscription_notifications.create_in_app_notification",
            new=AsyncMock(),
        ) as create_notification,
    ):
        created = await send_due_subscription_reminders(
            session, now=datetime(2026, 11, 25, 12, tzinfo=UTC)
        )

    assert created == 0
    create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_free_plan_does_not_receive_expiry_notification():
    subscription = _subscription(days=-1)
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(slug="free"))

    with patch(
        "app.billing.subscription_notifications.create_in_app_notification",
        new=AsyncMock(),
    ) as create_notification:
        created = await notify_subscription_expired(
            session,
            subscription,
            now=subscription.current_period_end + timedelta(seconds=1),
        )

    assert created is False
    create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_notification_waits_until_current_period_end():
    subscription = _subscription(days=3)
    session = MagicMock()
    session.get = AsyncMock()

    with patch(
        "app.billing.subscription_notifications.create_in_app_notification",
        new=AsyncMock(),
    ) as create_notification:
        created = await notify_subscription_expired(
            session,
            subscription,
            now=subscription.current_period_end - timedelta(seconds=1),
        )

    assert created is False
    session.get.assert_not_awaited()
    create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_expiry_notification_is_created_at_period_end():
    subscription = _subscription(days=-1)
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(slug="pro"))

    with (
        patch(
            "app.billing.subscription_notifications.has_subscription_reminder",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.billing.subscription_notifications.create_in_app_notification",
            new=AsyncMock(),
        ) as create_notification,
    ):
        created = await notify_subscription_expired(
            session,
            subscription,
            now=subscription.current_period_end,
        )

    assert created is True
    create_notification.assert_awaited_once()
    assert create_notification.await_args.kwargs["type"] == "system"
    assert create_notification.await_args.kwargs["meta"]["event"] == "expired"


@pytest.mark.asyncio
async def test_debt_cap_notice_is_created_once_per_crossing_event():
    subscription = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        accrued_debt=Decimal("2000.00"),
        debt_cap=Decimal("2000.00"),
    )
    session = MagicMock()

    with (
        patch(
            "app.billing.subscription_notifications.has_debt_cap_notification",
            new=AsyncMock(return_value=False),
        ) as has_notification,
        patch(
            "app.billing.subscription_notifications.create_in_app_notification",
            new=AsyncMock(),
        ) as create_notification,
    ):
        created = await notify_debt_cap_reached(
            session, subscription, event_reference="sale-123"
        )

    assert created is True
    has_notification.assert_awaited_once()
    create_notification.assert_awaited_once()
    notification = create_notification.await_args.kwargs
    assert notification["type"] == "system"
    assert "2,000.00" in notification["body"]
    assert notification["meta"]["event"] == "debt_cap_reached"
    assert notification["meta"]["debt_cap_notification_key"].endswith("sale-123")


@pytest.mark.asyncio
async def test_existing_debt_cap_notice_is_not_created_again():
    subscription = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        accrued_debt=Decimal("2000.00"),
        debt_cap=Decimal("2000.00"),
    )
    session = MagicMock()

    with (
        patch(
            "app.billing.subscription_notifications.has_debt_cap_notification",
            new=AsyncMock(return_value=True),
        ) as has_notification,
        patch(
            "app.billing.subscription_notifications.create_in_app_notification",
            new=AsyncMock(),
        ) as create_notification,
    ):
        created = await notify_debt_cap_reached(
            session, subscription, event_reference="sale-123"
        )

    assert created is False
    has_notification.assert_awaited_once()
    create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_debt_cap_notice_is_not_created_before_cap_is_reached():
    subscription = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        accrued_debt=Decimal("1999.99"),
        debt_cap=Decimal("2000.00"),
    )
    session = MagicMock()

    with (
        patch(
            "app.billing.subscription_notifications.has_debt_cap_notification",
            new=AsyncMock(),
        ) as has_notification,
        patch(
            "app.billing.subscription_notifications.create_in_app_notification",
            new=AsyncMock(),
        ) as create_notification,
    ):
        created = await notify_debt_cap_reached(
            session, subscription, event_reference="sale-123"
        )

    assert created is False
    has_notification.assert_not_awaited()
    create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_debt_cap_notice_is_emitted_when_fee_settlement_reaches_cap():
    subscription = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        accrued_debt=Decimal("1900.00"),
        debt_cap=Decimal("2000.00"),
    )
    session = MagicMock()
    session.flush = AsyncMock()
    event_reference = str(uuid4())

    with (
        patch(
            "app.billing.fees.debit_wallet",
            new=AsyncMock(return_value=Decimal("0.00")),
        ),
        patch(
            "app.billing.fees.notify_debt_cap_reached",
            new=AsyncMock(return_value=True),
        ) as notify,
    ):
        await _settle_fee(
            session,
            sub=subscription,
            fee=Decimal("100.00"),
            reference=event_reference,
            description="Transaction fee",
            actor_id=None,
            entry_type_label="sale",
            billing_period_id=None,
        )

    assert subscription.accrued_debt == Decimal("2000.00")
    notify.assert_awaited_once_with(
        session, subscription, event_reference=event_reference
    )


@pytest.mark.asyncio
async def test_fee_settlement_below_cap_does_not_notify():
    subscription = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        accrued_debt=Decimal("1900.00"),
        debt_cap=Decimal("2000.00"),
    )
    session = MagicMock()
    session.flush = AsyncMock()

    with (
        patch(
            "app.billing.fees.debit_wallet",
            new=AsyncMock(return_value=Decimal("0.00")),
        ),
        patch(
            "app.billing.fees.notify_debt_cap_reached",
            new=AsyncMock(),
        ) as notify,
    ):
        await _settle_fee(
            session,
            sub=subscription,
            fee=Decimal("50.00"),
            reference="sale-456",
            description="Transaction fee",
            actor_id=None,
            entry_type_label="sale",
            billing_period_id=None,
        )

    assert subscription.accrued_debt == Decimal("1950.00")
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_fee_settlement_does_not_notify_debt_cap():
    subscription = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        accrued_debt=Decimal("1950.00"),
        debt_cap=Decimal("2000.00"),
    )
    session = MagicMock()
    session.flush = AsyncMock()

    with (
        patch(
            "app.billing.fees.debit_wallet",
            new=AsyncMock(return_value=Decimal("0.00")),
        ),
        patch(
            "app.billing.fees.notify_debt_cap_reached",
            new=AsyncMock(),
        ) as notify,
    ):
        with pytest.raises(ValueError, match="fee_balance_exceeded"):
            await _settle_fee(
                session,
                sub=subscription,
                fee=Decimal("100.00"),
                reference="sale-789",
                description="Transaction fee",
                actor_id=None,
                entry_type_label="sale",
                billing_period_id=None,
            )

    assert subscription.accrued_debt == Decimal("1950.00")
    notify.assert_not_awaited()