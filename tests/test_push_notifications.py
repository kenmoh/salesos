"""Push notifications run off the consumer's event loop, once per low stock.

Every worker handler is awaited inside the consumer's running event loop, so
the push helper may not call ``asyncio.run`` — the blocking Expo HTTP call
belongs on the executor. And low stock re-fires with every sale, so the
handler repeats its alert for a product at most once a day.
"""

from uuid import uuid4

from unittest.mock import AsyncMock, MagicMock, patch

from app.common.events.envelope import EventEnvelope
from app.worker.handlers import notifications


def _session():
    session = MagicMock()
    return session


def _envelope(**payload):
    return EventEnvelope(
        event_id=uuid4(),
        event_type="inventory.low_stock_detected",
        tenant_id=str(payload.get("tenant_id", uuid4())),
        payload=payload,
    )


class TestPushHelperRunsOffTheLoop:
    async def test_the_expo_call_runs_in_the_executor(self):
        token = MagicMock(id=uuid4(), token="ExponentPushToken[abc]")
        session = _session()

        with (
            patch(
                "app.worker.handlers.notifications.get_active_push_tokens",
                AsyncMock(return_value=[token]),
            ),
            patch(
                "app.notifications.push.send_push_batch",
                return_value={"failed_tokens": []},
            ) as expo_call,
        ):
            await notifications._send_push_for_notification(
                session, uuid4(), "title", "body"
            )

        assert expo_call.call_count == 1

    async def test_stale_tokens_are_deactivated(self):
        token = MagicMock(id=uuid4(), token="ExponentPushToken[gone]")
        session = _session()

        with (
            patch(
                "app.worker.handlers.notifications.get_active_push_tokens",
                AsyncMock(return_value=[token]),
            ),
            patch(
                "app.notifications.push.send_push_batch",
                return_value={"failed_tokens": ["ExponentPushToken[gone]"]},
            ),
            patch(
                "app.notifications.repository.deactivate_stale_push_tokens",
                AsyncMock(),
            ) as deactivate,
        ):
            await notifications._send_push_for_notification(
                session, uuid4(), "title", "body"
            )

        deactivate.assert_awaited_once()
        assert deactivate.await_args.args[1] == [token.id]


class TestLowStockSendOncePerDay:
    async def test_a_recent_alert_for_the_product_sends_nothing(self):
        tenant = uuid4()
        product_id = uuid4()
        session = _session()

        with (
            patch.object(
                notifications,
                "get_active_push_tokens",
                AsyncMock(return_value=[MagicMock()]),
            ) as tokens,
            patch(
                "app.tenancy.repository.get_tenant_by_id",
                AsyncMock(return_value=MagicMock(settings="{}")),
            ),
            patch(
                "app.worker.handlers.notifications.has_recent_low_stock_notification",
                AsyncMock(return_value=True),
            ),
            patch(
                "app.worker.handlers.notifications.create_notification", AsyncMock()
            ) as notif,
            patch(
                "app.worker.handlers.notifications.create_in_app_notification",
                AsyncMock(),
            ) as in_app,
            patch.object(
                notifications, "_send_push_for_notification", AsyncMock()
            ) as push,
        ):
            await notifications.handle_low_stock_detected(
                _envelope(tenant_id=str(tenant), product_id=str(product_id)), session
            )

        push.assert_not_awaited()
        in_app.assert_not_awaited()
        notif.assert_not_awaited()

    async def test_the_first_alert_for_the_product_still_sends(self):
        tenant = uuid4()
        product_id = uuid4()
        session = _session()

        with (
            patch(
                "app.tenancy.repository.get_tenant_by_id",
                AsyncMock(return_value=MagicMock(settings="{}")),
            ),
            patch(
                "app.worker.handlers.notifications.has_recent_low_stock_notification",
                AsyncMock(return_value=False),
            ) as recent,
            patch(
                "app.worker.handlers.notifications.create_notification", AsyncMock()
            ) as notif,
            patch(
                "app.worker.handlers.notifications.create_in_app_notification",
                AsyncMock(),
            ) as in_app,
            patch.object(
                notifications, "_send_push_for_notification", AsyncMock()
            ) as push,
        ):
            await notifications.handle_low_stock_detected(
                _envelope(tenant_id=str(tenant), product_id=str(product_id)), session
            )

        assert recent.await_args.args[2] == str(product_id)
        push.assert_awaited_once()
        in_app.assert_awaited_once()
        notif.assert_awaited_once()
