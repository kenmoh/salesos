from sqlalchemy.ext.asyncio import AsyncSession

from common.events.envelope import EventEnvelope
from notifications.repository import create_notification
from notifications.schemas import NotificationSendCommand
from notifications.service import plan_send_notification


async def handle_tenant_created(envelope: EventEnvelope, session: AsyncSession) -> None:
    tenant_id = envelope.payload.get("tenant_id")
    owner_email = envelope.payload.get("owner_email")
    business_name = envelope.payload.get("business_name")
    if not tenant_id or not owner_email:
        return

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


async def handle_user_created(envelope: EventEnvelope, session: AsyncSession) -> None:
    tenant_id = envelope.payload.get("tenant_id")
    email = envelope.payload.get("email")
    full_name = envelope.payload.get("full_name")
    if not tenant_id or not email:
        return

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


async def handle_low_stock_detected(envelope: EventEnvelope, session: AsyncSession) -> None:
    tenant_id = envelope.payload.get("tenant_id")
    product_name = envelope.payload.get("product_name", "Unknown")
    product_id = envelope.payload.get("product_id")
    current_qty = envelope.payload.get("current_qty", 0)
    if not tenant_id:
        return

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
