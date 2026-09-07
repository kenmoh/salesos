from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import UUID

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, ok
from app.notifications.schemas import (
    InAppNotificationResult,
    MarkReadCommand,
    NotificationListResult,
    PushTokenRegisterCommand,
)

notif_router = APIRouter(tags=["Notifications"])


@notif_router.post(
    "/notifications/push-token",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def register_push_token(payload: PushTokenRegisterCommand, ctx: TenantDep):
    from app.notifications.repository import register_push_token

    await register_push_token(
        session=ctx.session,
        tenant_id=ctx.user.business_id,
        user_id=ctx.user.user_id,
        token=payload.token,
        platform=payload.platform,
    )
    return ok({"registered": True})


@notif_router.delete(
    "/notifications/push-token",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def deactivate_push_token(ctx: TenantDep):
    from sqlalchemy import select
    from app.notifications.models import PushToken

    result = await ctx.session.execute(
        select(PushToken).where(
            PushToken.user_id == ctx.user.user_id,
            PushToken.is_active == True,
        )
    )
    tokens = list(result.scalars().all())
    for t in tokens:
        t.is_active = False
    await ctx.session.flush()
    return ok({"deactivated": len(tokens)})


@notif_router.get(
    "/notifications",
    response_model=DataResponse[NotificationListResult],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def list_notifications(
    ctx: TenantDep,
    type: str | None = Query(None),
    unread_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    from app.notifications.repository import get_in_app_notifications, get_unread_count

    items, total = await get_in_app_notifications(
        session=ctx.session,
        tenant_id=ctx.user.business_id,
        type_filter=type,
        unread_only=unread_only,
        offset=offset,
        limit=limit,
    )
    unread_count = await get_unread_count(ctx.session, ctx.user.business_id)

    return ok(
        NotificationListResult(
            items=[
                InAppNotificationResult(
                    id=n.id,
                    type=n.type,
                    title=n.title,
                    body=n.body,
                    is_read=n.is_read,
                    meta=n.meta,
                    created_at=n.created_at,
                )
                for n in items
            ],
            unread_count=unread_count,
            total=total,
        )
    )


@notif_router.get(
    "/notifications/unread-count",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def unread_count(ctx: TenantDep):
    from app.notifications.repository import get_unread_count

    count = await get_unread_count(ctx.session, ctx.user.business_id)
    return ok({"count": count})


@notif_router.patch(
    "/notifications/read",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def mark_read(payload: MarkReadCommand, ctx: TenantDep):
    from app.notifications.repository import mark_notifications_read

    count = await mark_notifications_read(
        session=ctx.session,
        tenant_id=ctx.user.business_id,
        notification_ids=payload.notification_ids,
    )
    return ok({"marked": count})


@notif_router.patch(
    "/notifications/read-all",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def mark_all_read(ctx: TenantDep):
    from app.notifications.repository import mark_all_read

    count = await mark_all_read(ctx.session, ctx.user.business_id)
    return ok({"marked": count})


@notif_router.delete(
    "/notifications",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def delete_notifications(payload: MarkReadCommand, ctx: TenantDep):
    from app.notifications.repository import delete_notifications

    count = await delete_notifications(
        session=ctx.session,
        tenant_id=ctx.user.business_id,
        notification_ids=payload.notification_ids,
    )
    return ok({"deleted": count})
