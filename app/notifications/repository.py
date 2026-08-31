from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications.models import Notification, NotificationTemplate


async def create_template(
    session: AsyncSession, template: NotificationTemplate
) -> NotificationTemplate:
    session.add(template)
    await session.flush()
    return template


async def get_template_by_name(
    session: AsyncSession, name: str, tenant_id: UUID | None = None
) -> NotificationTemplate | None:
    query = select(NotificationTemplate).where(
        NotificationTemplate.name == name, NotificationTemplate.is_active == True
    )
    if tenant_id:
        query = query.where(NotificationTemplate.tenant_id == tenant_id)
    else:
        query = query.where(NotificationTemplate.tenant_id.is_(None))
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def create_notification(session: AsyncSession, notification: Notification) -> Notification:
    session.add(notification)
    await session.flush()
    return notification


async def mark_notification_sent(session: AsyncSession, notification_id: UUID) -> Notification:
    from datetime import UTC, datetime

    result = await session.execute(select(Notification).where(Notification.id == notification_id))
    notification = result.scalar_one()
    notification.status = "sent"
    notification.sent_at = datetime.now(UTC)
    await session.flush()
    return notification


async def mark_notification_failed(
    session: AsyncSession, notification_id: UUID, error: str
) -> Notification:
    result = await session.execute(select(Notification).where(Notification.id == notification_id))
    notification = result.scalar_one()
    notification.status = "failed"
    notification.last_error = error
    notification.attempts += 1
    await session.flush()
    return notification


async def get_pending_notifications(session: AsyncSession, limit: int = 50) -> list[Notification]:
    result = await session.execute(
        select(Notification)
        .where(Notification.status == "pending")
        .order_by(Notification.created_at)
        .limit(limit)
    )
    return list(result.scalars().all())
