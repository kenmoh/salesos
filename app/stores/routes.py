from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, ok
from app.auth.schemas.schema import StoreCreate, StoreUpdate
from app.auth.schemas.responses import (
    StoreCreated,
    StoreDetails,
    StoreSummary,
    SuccessResponse,
)
import app.common.bridge as bridge

router = APIRouter(prefix="/stores", tags=["Stores"])


@router.post(
    "",
    status_code=201,
    response_model=DataResponse[StoreCreated],
    dependencies=[Depends(require_permission("stores:create"))],
)
async def create_store(payload: StoreCreate, ctx: TenantDep):
    try:
        result = await bridge.create_store(
            tenant_id=ctx.user.business_id,
            name=payload.name,
            address=payload.address,
            is_warehouse=payload.is_warehouse,
            actor_id=ctx.user.user_id,
        )
        return ok(result)
    except ValueError as e:
        if str(e) == "main_store_exists":
            raise HTTPException(
                status_code=409, detail="A main store already exists for this tenant"
            )
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "",
    response_model=DataResponse[list[StoreSummary]],
    dependencies=[Depends(require_permission("stores:read"))],
)
async def stores(ctx: TenantDep):
    return ok(await bridge.list_stores(tenant_id=ctx.user.business_id))


@router.get(
    "/{store_id}",
    response_model=DataResponse[StoreDetails],
    dependencies=[Depends(require_permission("stores:read"))],
)
async def get_store_details(
    store_id: str,
    ctx: TenantDep,
    from_date: str | None = Query(None, description="ISO 8601 date"),
    to_date: str | None = Query(None, description="ISO 8601 date"),
):
    result = await bridge.get_store_details(
        tenant_id=ctx.user.business_id,
        store_id=store_id,
        from_date=from_date,
        to_date=to_date,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Store not found")
    return ok(result)


@router.patch(
    "/{store_id}",
    response_model=DataResponse[StoreCreated],
    dependencies=[Depends(require_permission("stores:update"))],
)
async def update_store(store_id: str, payload: StoreUpdate, ctx: TenantDep):
    try:
        result = await bridge.update_store(
            tenant_id=ctx.user.business_id,
            store_id=store_id,
            name=payload.name,
            address=payload.address,
            is_warehouse=payload.is_warehouse,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Store not found")
        return ok(result)
    except ValueError as e:
        if str(e) == "main_store_exists":
            raise HTTPException(
                status_code=409, detail="A main store already exists for this tenant"
            )
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{store_id}",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("stores:update"))],
)
async def delete_store(store_id: str, ctx: TenantDep):
    result = await bridge.delete_store(tenant_id=ctx.user.business_id, store_id=store_id)
    if not result:
        raise HTTPException(status_code=404, detail="Store not found")
    return ok(result)
