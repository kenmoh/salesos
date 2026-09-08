from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

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
    from app.tenancy.models import Tenant

    result = await ctx.session.execute(
        select(Tenant).where(Tenant.id == ctx.user.business_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        return ok(BusinessSettings())

    settings = json.loads(tenant.settings) if isinstance(tenant.settings, str) else (tenant.settings or {})
    return ok(BusinessSettings(
        name=tenant.business_name,
        phone=tenant.owner_phone,
        address=settings.get("address"),
        tax_rate=settings.get("tax_rate"),
        currency=settings.get("currency"),
        logo_url=settings.get("logo_url"),
        settings=settings,
    ))


@sync_router.patch(
    "/business/settings",
    response_model=DataResponse[BusinessSettings],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def update_business_settings(payload: BusinessUpdate, ctx: TenantDep):
    from app.tenancy.models import Tenant

    result = await ctx.session.execute(
        select(Tenant).where(Tenant.id == ctx.user.business_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Update top-level columns
    if payload.name is not None:
        tenant.business_name = payload.name
    if payload.phone is not None:
        tenant.owner_phone = payload.phone

    # Merge into settings JSON
    settings = json.loads(tenant.settings) if isinstance(tenant.settings, str) else (tenant.settings or {})
    if payload.address is not None:
        settings["address"] = payload.address
    if payload.tax_rate is not None:
        settings["tax_rate"] = float(payload.tax_rate)
    if payload.currency is not None:
        settings["currency"] = payload.currency
    if payload.logo_url is not None:
        settings["logo_url"] = payload.logo_url
    if payload.settings is not None:
        settings.update(payload.settings)
        # System notifications are always enabled
        if "notification_types" in settings:
            settings["notification_types"]["system"] = True
    tenant.settings = json.dumps(settings)

    await ctx.session.flush()

    return ok(BusinessSettings(
        name=tenant.business_name,
        phone=tenant.owner_phone,
        address=settings.get("address"),
        tax_rate=settings.get("tax_rate"),
        currency=settings.get("currency"),
        logo_url=settings.get("logo_url"),
        settings=settings,
    ))


@sync_router.post(
    "/business/logo",
    response_model=DataResponse[BusinessSettings],
    dependencies=[Depends(require_permission("sync:manage"))],
)
async def upload_business_logo(
    file: "UploadFile",
    ctx: TenantDep,
):
    from fastapi import UploadFile as _UploadFile
    from app.tenancy.models import Tenant
    from app.catalog.cloudinary_upload import _ensure_configured, _configured
    import cloudinary.uploader

    _ensure_configured()
    if not _configured:
        raise HTTPException(status_code=500, detail="Cloudinary not configured")

    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    public_id = f"storeflow/{ctx.user.business_id}/logo"
    upload_result = cloudinary.uploader.upload(
        contents,
        public_id=public_id,
        resource_type="image",
        overwrite=True,
    )
    logo_url = str(upload_result.get("secure_url", ""))

    # Save to tenant settings
    result = await ctx.session.execute(
        select(Tenant).where(Tenant.id == ctx.user.business_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    settings = json.loads(tenant.settings) if isinstance(tenant.settings, str) else (tenant.settings or {})
    settings["logo_url"] = logo_url
    tenant.settings = json.dumps(settings)
    await ctx.session.flush()

    return ok(BusinessSettings(
        name=tenant.business_name,
        phone=tenant.owner_phone,
        address=settings.get("address"),
        tax_rate=settings.get("tax_rate"),
        currency=settings.get("currency"),
        logo_url=logo_url,
        settings=settings,
    ))


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
