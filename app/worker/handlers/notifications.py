import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.events.envelope import EventEnvelope
from app.notifications.repository import (
    create_in_app_notification,
    create_notification,
    get_active_push_tokens,
)
from app.notifications.schemas import NotificationSendCommand
from app.notifications.service import plan_send_notification

logger = logging.getLogger("storeflow.notifications.handlers")


def _get_notification_type_preferences(tenant_settings: dict) -> dict[str, bool]:
    """Extract notification type preferences from tenant settings.
    System is always enabled and cannot be disabled.
    """
    if isinstance(tenant_settings, str):
        try:
            tenant_settings = json.loads(tenant_settings)
        except (json.JSONDecodeError, TypeError):
            return {}
    prefs = tenant_settings.get("notification_types", {})
    prefs["system"] = True
    return prefs


def _is_type_enabled(prefs: dict[str, bool], ntype: str) -> bool:
    """Check if a notification type is enabled. System is always enabled."""
    if ntype == "system":
        return True
    return prefs.get(ntype, True)


def _send_push_for_notification(
    session: AsyncSession,
    tenant_id,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Send push notifications to all active devices for a tenant."""
    import asyncio
    from app.notifications.push import send_push_batch

    async def _get_tokens():
        return await get_active_push_tokens(session, tenant_id)

    tokens = asyncio.run(_get_tokens())
    if not tokens:
        return

    token_values = [t.token for t in tokens]
    result = send_push_batch(token_values, title, body, data or {})

    # Deactivate tokens for devices that are no longer registered
    if result.get("failed_tokens"):
        from app.notifications.repository import deactivate_stale_push_tokens
        from uuid import UUID

        stale_ids = [
            t.id for t in tokens if t.token in result["failed_tokens"]
        ]
        asyncio.run(deactivate_stale_push_tokens(session, stale_ids))


async def handle_tenant_created(envelope: EventEnvelope, session: AsyncSession) -> None:
    tenant_id = envelope.payload.get("tenant_id")
    owner_email = envelope.payload.get("owner_email")
    business_name = envelope.payload.get("business_name")
    if not tenant_id or not owner_email:
        return

    # Email notification
    command = NotificationSendCommand(
        tenant_id=tenant_id,
        channel="email",
        recipient=owner_email,
        subject=f"Welcome to StoreFlow, {business_name}!",
        body=f"Your tenant {business_name} has been created. Get started by adding your first product.",
        correlation_id=envelope.correlation_id,
    )
    _result, notification = plan_send_notification(command)
    await create_notification(session, notification)

    # In-app notification
    await create_in_app_notification(
        session=session,
        tenant_id=tenant_id,
        type="system",
        title="Welcome to StoreFlow",
        body=f"Your business '{business_name}' has been created. Get started by adding your first product.",
        meta={"screen": "/(tabs)/(more)/notifications"},
    )


async def handle_user_created(envelope: EventEnvelope, session: AsyncSession) -> None:
    tenant_id = envelope.payload.get("tenant_id")
    email = envelope.payload.get("email")
    full_name = envelope.payload.get("full_name")
    if not tenant_id or not email:
        return

    # Email notification
    command = NotificationSendCommand(
        tenant_id=tenant_id,
        channel="email",
        recipient=email,
        subject="Your StoreFlow account is ready",
        body=f"Hi {full_name}, your account has been created. Welcome aboard!",
        correlation_id=envelope.correlation_id,
    )
    _result, notification = plan_send_notification(command)
    await create_notification(session, notification)

    # In-app notification
    await create_in_app_notification(
        session=session,
        tenant_id=tenant_id,
        type="system",
        title="Account Ready",
        body=f"Hi {full_name}, your account has been created. Welcome aboard!",
        meta={"screen": "/(tabs)/(more)/notifications"},
    )


async def handle_low_stock_detected(envelope: EventEnvelope, session: AsyncSession) -> None:
    tenant_id = envelope.payload.get("tenant_id")
    product_name = envelope.payload.get("product_name", "Unknown")
    product_id = envelope.payload.get("product_id")
    current_qty = envelope.payload.get("current_qty", 0)
    if not tenant_id:
        return

    # Check notification preferences
    from app.tenancy.repository import get_tenant_by_id
    from uuid import UUID as _UUID

    tenant = await get_tenant_by_id(session, _UUID(str(tenant_id)))
    prefs = _get_notification_type_preferences(tenant.settings if tenant else "{}")

    if not _is_type_enabled(prefs, "inventory"):
        return

    # Email notification
    command = NotificationSendCommand(
        tenant_id=tenant_id,
        channel="in_app",
        recipient=str(tenant_id),
        subject="Low Stock Alert",
        body=f"Product '{product_name}' ({product_id}) is low on stock. Current qty: {current_qty}.",
        correlation_id=envelope.correlation_id,
    )
    _result, notification = plan_send_notification(command)
    await create_notification(session, notification)

    # In-app notification
    await create_in_app_notification(
        session=session,
        tenant_id=tenant_id,
        type="inventory",
        title="Low Stock Alert",
        body=f"Product '{product_name}' is running low — only {current_qty} units left",
        meta={
            "product_id": str(product_id) if product_id else None,
            "current_qty": current_qty,
            "screen": "/(tabs)/(more)/notifications",
        },
    )

    # Push notification
    _send_push_for_notification(
        session=session,
        tenant_id=tenant_id,
        title="Low Stock Alert",
        body=f"{product_name} is running low — only {current_qty} units left",
        data={"screen": "/(tabs)/(more)/notifications"},
    )


async def handle_sale_confirmed(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Send in-app + push notification when a sale is confirmed."""
    tenant_id = envelope.payload.get("tenant_id")
    sale_number = envelope.payload.get("sale_number", "")
    total = envelope.payload.get("total", 0)
    store_name = envelope.payload.get("store_name", "")
    if not tenant_id:
        return

    from app.tenancy.repository import get_tenant_by_id
    from uuid import UUID as _UUID

    tenant = await get_tenant_by_id(session, _UUID(str(tenant_id)))
    prefs = _get_notification_type_preferences(tenant.settings if tenant else "{}")

    if not _is_type_enabled(prefs, "orders"):
        return

    fmt_total = f"₦{total:,.0f}" if isinstance(total, (int, float)) else str(total)

    await create_in_app_notification(
        session=session,
        tenant_id=tenant_id,
        type="orders",
        title="Sale Completed",
        body=f"Order {sale_number} confirmed for {fmt_total}" + (f" at {store_name}" if store_name else ""),
        meta={
            "sale_number": sale_number,
            "total": total,
            "store_name": store_name,
            "screen": "/(tabs)/(more)/notifications",
        },
    )

    _send_push_for_notification(
        session=session,
        tenant_id=tenant_id,
        title="Sale Completed",
        body=f"{sale_number} — {fmt_total}",
        data={"screen": "/(tabs)/(more)/notifications"},
    )


async def handle_payment_received(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Send in-app + push notification when payment is received."""
    tenant_id = envelope.payload.get("tenant_id")
    amount = envelope.payload.get("amount", 0)
    method = envelope.payload.get("method", "cash")
    sale_number = envelope.payload.get("sale_number", "")
    if not tenant_id:
        return

    from app.tenancy.repository import get_tenant_by_id
    from uuid import UUID as _UUID

    tenant = await get_tenant_by_id(session, _UUID(str(tenant_id)))
    prefs = _get_notification_type_preferences(tenant.settings if tenant else "{}")

    if not _is_type_enabled(prefs, "payments"):
        return

    fmt_amount = f"₦{amount:,.0f}" if isinstance(amount, (int, float)) else str(amount)

    await create_in_app_notification(
        session=session,
        tenant_id=tenant_id,
        type="payments",
        title="Payment Received",
        body=f"{method.title()} payment of {fmt_amount} confirmed" + (f" for {sale_number}" if sale_number else ""),
        meta={
            "amount": amount,
            "method": method,
            "sale_number": sale_number,
            "screen": "/(tabs)/(more)/notifications",
        },
    )

    _send_push_for_notification(
        session=session,
        tenant_id=tenant_id,
        title="Payment Received",
        body=f"{fmt_amount} via {method}",
        data={"screen": "/(tabs)/(more)/notifications"},
    )


async def handle_out_of_stock(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Send in-app + push notification when a product is out of stock."""
    tenant_id = envelope.payload.get("tenant_id")
    product_name = envelope.payload.get("product_name", "Unknown")
    product_id = envelope.payload.get("product_id")
    if not tenant_id:
        return

    from app.tenancy.repository import get_tenant_by_id
    from uuid import UUID as _UUID

    tenant = await get_tenant_by_id(session, _UUID(str(tenant_id)))
    prefs = _get_notification_type_preferences(tenant.settings if tenant else "{}")

    if not _is_type_enabled(prefs, "stock"):
        return

    await create_in_app_notification(
        session=session,
        tenant_id=tenant_id,
        type="stock",
        title="Out of Stock",
        body=f"{product_name} is now out of stock",
        meta={
            "product_id": str(product_id) if product_id else None,
            "screen": "/(tabs)/(more)/notifications",
        },
    )

    _send_push_for_notification(
        session=session,
        tenant_id=tenant_id,
        title="Out of Stock",
        body=f"{product_name} is now out of stock",
        data={"screen": "/(tabs)/(more)/notifications"},
    )
