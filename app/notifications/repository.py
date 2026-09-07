from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications.models import (
    InAppNotification,
    Notification,
    NotificationTemplate,
    PushToken,
)


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


# ── Push Token ──────────────────────────────────────────────────────────────


async def register_push_token(
    session: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    token: str,
    platform: str,
) -> PushToken:
    result = await session.execute(select(PushToken).where(PushToken.token == token))
    existing = result.scalar_one_or_none()
    if existing:
        existing.tenant_id = tenant_id
        existing.user_id = user_id
        existing.platform = platform
        existing.is_active = True
        existing.updated_at = datetime.now(UTC)
        await session.flush()
        return existing

    push_token = PushToken(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        token=token,
        platform=platform,
        is_active=True,
    )
    session.add(push_token)
    await session.flush()
    return push_token


async def deactivate_push_token(session: AsyncSession, token: str) -> None:
    result = await session.execute(select(PushToken).where(PushToken.token == token))
    pt = result.scalar_one_or_none()
    if pt:
        pt.is_active = False
        pt.updated_at = datetime.now(UTC)
        await session.flush()


async def deactivate_push_token_by_id(session: AsyncSession, token_id: UUID) -> None:
    result = await session.execute(select(PushToken).where(PushToken.id == token_id))
    pt = result.scalar_one_or_none()
    if pt:
        pt.is_active = False
        pt.updated_at = datetime.now(UTC)
        await session.flush()


async def get_active_push_tokens(session: AsyncSession, tenant_id: UUID) -> list[PushToken]:
    result = await session.execute(
        select(PushToken).where(
            PushToken.tenant_id == tenant_id,
            PushToken.is_active == True,
        )
    )
    return list(result.scalars().all())


async def deactivate_stale_push_tokens(session: AsyncSession, token_ids: list[UUID]) -> None:
    if not token_ids:
        return
    await session.execute(
        update(PushToken)
        .where(PushToken.id.in_(token_ids))
        .values(is_active=False, updated_at=datetime.now(UTC))
    )
    await session.flush()


# ── In-App Notifications ────────────────────────────────────────────────────


async def create_in_app_notification(
    session: AsyncSession,
    tenant_id: UUID,
    type: str,
    title: str,
    body: str,
    meta: dict | None = None,
) -> InAppNotification:
    notification = InAppNotification(
        id=uuid4(),
        tenant_id=tenant_id,
        type=type,
        title=title,
        body=body,
        is_read=False,
        meta=meta,
    )
    session.add(notification)
    await session.flush()
    return notification


async def get_in_app_notifications(
    session: AsyncSession,
    tenant_id: UUID,
    type_filter: str | None = None,
    unread_only: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[InAppNotification], int]:
    query = select(InAppNotification).where(InAppNotification.tenant_id == tenant_id)
    count_query = select(func.count(InAppNotification.id)).where(InAppNotification.tenant_id == tenant_id)

    if type_filter:
        query = query.where(InAppNotification.type == type_filter)
        count_query = count_query.where(InAppNotification.type == type_filter)
    if unread_only:
        query = query.where(InAppNotification.is_read == False)
        count_query = count_query.where(InAppNotification.is_read == False)

    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(InAppNotification.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    items = list(result.scalars().all())

    return items, total


async def get_unread_count(session: AsyncSession, tenant_id: UUID) -> int:
    result = await session.execute(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.tenant_id == tenant_id,
            InAppNotification.is_read == False,
        )
    )
    return result.scalar() or 0


async def mark_notifications_read(
    session: AsyncSession,
    tenant_id: UUID,
    notification_ids: list[UUID],
) -> int:
    if not notification_ids:
        return 0
    result = await session.execute(
        update(InAppNotification)
        .where(
            InAppNotification.tenant_id == tenant_id,
            InAppNotification.id.in_(notification_ids),
            InAppNotification.is_read == False,
        )
        .values(is_read=True)
    )
    await session.flush()
    return result.rowcount


async def mark_all_read(session: AsyncSession, tenant_id: UUID) -> int:
    result = await session.execute(
        update(InAppNotification)
        .where(
            InAppNotification.tenant_id == tenant_id,
            InAppNotification.is_read == False,
        )
        .values(is_read=True)
    )
    await session.flush()
    return result.rowcount


async def delete_notifications(
    session: AsyncSession,
    tenant_id: UUID,
    notification_ids: list[UUID],
) -> int:
    if not notification_ids:
        return 0
    result = await session.execute(
        delete(InAppNotification).where(
            InAppNotification.tenant_id == tenant_id,
            InAppNotification.id.in_(notification_ids),
        )
    )
    await session.flush()
    return result.rowcount


async def delete_expired_notifications(session: AsyncSession) -> dict:
    now = datetime.now(UTC)
    read_cutoff = now - timedelta(hours=24)
    unread_cutoff = now - timedelta(hours=48)

    read_result = await session.execute(
        delete(InAppNotification).where(
            InAppNotification.is_read == True,
            InAppNotification.created_at < read_cutoff,
        )
    )
    unread_result = await session.execute(
        delete(InAppNotification).where(
            InAppNotification.is_read == False,
            InAppNotification.created_at < unread_cutoff,
        )
    )
    await session.flush()
    return {"deleted_read": read_result.rowcount, "deleted_unread": unread_result.rowcount}
