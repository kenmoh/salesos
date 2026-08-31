from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, ok
from app.auth.schemas.schema import BusinessUpdate, SyncBatch, SyncTriggerResult
from app.auth.schemas.responses import (
    BusinessSettings,
    PermissionDetail,
    SyncBatchResult,
    SyncPendingItem,
)
from app.common import services

try:
    from app.worker.tasks import task_reconcile_events
except ImportError:
    task_reconcile_events = None

sync_router = APIRouter(tags=["Sync"])


@sync_router.post("/sync/events", response_model=DataResponse[SyncBatchResult])
async def sync_events(payload: SyncBatch, ctx: TenantDep):
    return ok(
        await services.process_sync_batch(
            session=ctx.session,
            business_id=ctx.user.business_id,
            user_id=ctx.user.user_id,
            events=[e.model_dump() for e in payload.events],
        )
    )


@sync_router.get(
    "/sync/pending",
    response_model=DataResponse[list[SyncPendingItem]],
    dependencies=[Depends(require_permission("sync:read"))],
)
async def pending(
    ctx: TenantDep,
    since: str = Query(default="1970-01-01T00:00:00Z"),
    limit: int = Query(default=50, ge=1, le=500),
):
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid 'since' format. Use ISO 8601.")
    return ok(
        await services.get_pending_events(
            session=ctx.session,
            business_id=ctx.user.business_id,
            since=dt,
            limit=limit,
        )
    )


@sync_router.post(
    "/sync/trigger",
    response_model=DataResponse[SyncTriggerResult],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def trigger_sync(ctx: TenantDep):
    count = await services.get_pending_event_count(
        session=ctx.session,
        business_id=ctx.user.business_id,
    )
    if count > 0:
        task_reconcile_events.delay(business_id=ctx.user.business_id)
    return ok(SyncTriggerResult(triggered=count > 0, pending_count=count))


@sync_router.get("/business/settings", response_model=DataResponse[BusinessSettings])
async def business_settings(ctx: TenantDep):
    rows = await services.call(ctx.session, "api.fn_business_settings", p_bid=ctx.user.business_id)
    if not rows:
        return ok({})
    return ok(rows[0].get("settings", {}))


@sync_router.patch(
    "/business/settings",
    response_model=DataResponse[BusinessSettings],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def update_business_settings(payload: BusinessUpdate, ctx: TenantDep):
    from app.common.services import exec_fn

    await exec_fn(
        ctx.session,
        "api.fn_update_business_settings",
        p_bid=ctx.user.business_id,
        p_settings=payload.model_dump(exclude_unset=True, exclude_none=True),
    )
    return ok(payload.model_dump(exclude_unset=True, exclude_none=True))


@sync_router.get(
    "/business/permissions",
    response_model=DataResponse[list[PermissionDetail]],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def business_permissions(ctx: TenantDep):
    from sqlalchemy import text
    result = await ctx.session.execute(
        text("SELECT name, description FROM permissions ORDER BY name")
    )
    rows = result.mappings().all()
    permissions = [dict(r) for r in rows]
    return ok(permissions)


@sync_router.patch(
    "/business/permissions",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def update_business_permissions(payload: dict[str, bool], ctx: TenantDep):
    from sqlalchemy import text
    for code, enabled in payload.items():
        await ctx.session.execute(
            text("""
                UPDATE permissions
                SET description = :description
                WHERE name = :code
            """),
            {"code": code, "description": None}
        )
    await ctx.session.commit()
    return ok(payload)
