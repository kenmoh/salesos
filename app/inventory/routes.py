from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from app.auth.schemas.responses import (
    CategoryCreated,
    CategoryDetail,
    DistributeResult,
    LowStockItem,
    MinStockLevelResult,
    ProductCreatedForStore,
    StockAdjustmentResult,
    StockBalanceItem,
    StockMovementItem,
    StoreProductAdded,
    StoreProductDetail,
    StoreProductListItem,
    StoreProductUpdated,
    SuccessResponse,
    SyncResult,
    TransferFulfilled,
    TransferRequestApproved,
    TransferRequestCreated,
    TransferRequestDetail,
)
from app.auth.schemas.schema import (
    CategoryCreate,
    CategoryUpdate,
    InventoryAdjust,
    ProductCreateForStore,
    SetMinStockLevel,
    StoreDistribute,
    StoreProductAdd,
    StoreProductUpdate,
    TransferRequestApprove,
    TransferRequestCreate,
)
import app.common.bridge as bridge
from app.common import services


router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.post(
    "/{store_id}/categories",
    status_code=201,
    response_model=DataResponse[CategoryCreated],
    dependencies=[Depends(require_permission("categories:create"))],
)
async def create_category(store_id: str, payload: CategoryCreate, ctx: TenantDep):
    try:
        result = await bridge.create_category(
            tenant_id=ctx.user.business_id,
            store_id=store_id,
            name=payload.name,
            description=payload.description,
            actor_id=ctx.user.user_id,
        )
        return ok(result)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get(
    "/{store_id}/categories",
    response_model=DataResponse[list[CategoryDetail]],
    dependencies=[Depends(require_permission("categories:read"))],
)
async def categories(store_id: str, ctx: TenantDep):
    from uuid import UUID
    try:
        UUID(store_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid store_id")
    return ok(await bridge.list_categories(store_id=store_id))


@router.get(
    "/{store_id}/categories/{category_id}",
    response_model=DataResponse[CategoryDetail],
    dependencies=[Depends(require_permission("categories:read"))],
)
async def get_category(store_id: str, category_id: str, ctx: TenantDep):
    category = await bridge.get_category(store_id=store_id, category_id=category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return ok(category)


@router.patch(
    "/{store_id}/categories/{category_id}",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("categories:update"))],
)
async def update_category(store_id: str, category_id: str, payload: CategoryUpdate, ctx: TenantDep):
    try:
        await bridge.update_category(
            store_id=store_id,
            category_id=category_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ok({"success": True})


@router.delete(
    "/{store_id}/categories/{category_id}",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("categories:delete"))],
)
async def delete_category(store_id: str, category_id: str, ctx: TenantDep):
    try:
        await bridge.delete_category(store_id=store_id, category_id=category_id)
    except ValueError as e:
        if str(e) == "category_has_products":
            raise HTTPException(
                status_code=409, detail="Cannot delete category with linked products"
            )
        raise HTTPException(status_code=404, detail=str(e))
    return ok({"success": True})


@router.post(
    "/{store_id}/products",
    status_code=201,
    response_model=DataResponse[StoreProductAdded],
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def add_store_product(store_id: str, payload: StoreProductAdd, ctx: TenantDep):
    try:
        result = await bridge.add_product_to_store(
            tenant_id=ctx.user.business_id,
            store_id=store_id,
            product_id=payload.product_id,
            qty=payload.qty,
        )
        return ok(result)
    except ValueError as e:
        detail = str(e)
        if detail == "product_not_found":
            raise HTTPException(status_code=404, detail="Product not found")
        if detail == "product_already_in_store":
            raise HTTPException(status_code=409, detail="Product already exists in this store")
        raise HTTPException(status_code=400, detail=detail)


@router.post(
    "/{store_id}/products/create",
    status_code=201,
    response_model=DataResponse[ProductCreatedForStore],
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def create_product_for_store(store_id: str, payload: ProductCreateForStore, ctx: TenantDep):
    try:
        result = await bridge.create_product_for_store(
            tenant_id=ctx.user.business_id,
            store_id=store_id,
            name=payload.name,
            description=payload.description,
            category_id=str(payload.category_id) if payload.category_id else None,
            unit=payload.unit,
            cost_price=payload.cost_price,
            selling_price=payload.selling_price,
            tax_rate=payload.tax_rate,
            reorder_point=payload.reorder_point,
            image_url=payload.image_url,
            qty=payload.qty,
            created_by=ctx.user.user_id,
        )
        return ok(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{store_id}/products",
    response_model=PaginatedResponse[StoreProductListItem],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def store_products(
    store_id: str,
    ctx: TenantDep,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    result = await bridge.get_store_products(
        tenant_id=ctx.user.business_id,
        store_id=store_id,
        search=search,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@router.get(
    "/{store_id}/products/{product_id}",
    response_model=DataResponse[StoreProductDetail],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def get_store_product(store_id: str, product_id: str, ctx: TenantDep):
    result = await bridge.get_store_product(
        tenant_id=ctx.user.business_id, store_id=store_id, product_id=product_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Product not found in this store")
    return ok(result)


@router.patch(
    "/{store_id}/products/{product_id}",
    response_model=DataResponse[StoreProductUpdated],
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def update_store_product(store_id: str, product_id: str, payload: StoreProductUpdate, ctx: TenantDep):
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = await bridge.update_store_product(
            tenant_id=ctx.user.business_id,
            store_id=store_id,
            product_id=product_id,
            **fields,
        )
    except ValueError as e:
        if str(e) == "store_not_found":
            raise HTTPException(status_code=404, detail="Store not found")
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Product not found in this store")
    return ok(result)


@router.delete(
    "/{store_id}/products/{product_id}",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def delete_store_product(store_id: str, product_id: str, ctx: TenantDep):
    try:
        await bridge.delete_store_product(
            tenant_id=ctx.user.business_id, store_id=store_id, product_id=product_id
        )
    except ValueError as e:
        if str(e) == "product_not_in_store":
            raise HTTPException(status_code=404, detail="Product not found in this store")
        if str(e) == "has_reserved_stock":
            raise HTTPException(status_code=409, detail="Product has reserved stock and cannot be removed")
        raise HTTPException(status_code=400, detail=str(e))
    return ok({"success": True})


@router.get(
    "/{store_id}/products/{product_id}/qr",
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def download_product_qr(store_id: str, product_id: str, ctx: TenantDep):
    from fastapi.responses import RedirectResponse, Response

    result = await bridge.get_product_qr_download(ctx.user.business_id, product_id)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    if isinstance(result, str):
        download_url = result.replace("/upload/", "/upload/fl_attachment/")
        return RedirectResponse(url=download_url)
    png_bytes, public_id = result
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{public_id}_qr.png"'},
    )


@router.post(
    "/{store_id}/adjust",
    response_model=DataResponse[StockAdjustmentResult],
    dependencies=[Depends(require_permission("inventory:adjust"))],
)
async def adjust(store_id: str, payload: InventoryAdjust, ctx: TenantDep):
    return ok(
        await bridge.adjust_stock(
            tenant_id=ctx.user.business_id,
            actor_id=ctx.user.user_id,
            product_id=str(payload.product_id),
            store_id=store_id,
            reason=payload.reason,
            qty_change=payload.qty_change,
            unit_cost=payload.unit_cost,
            notes=payload.notes,
        )
    )


@router.get(
    "/{store_id}/stock",
    response_model=PaginatedResponse[StockBalanceItem],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def stock_balances(
    store_id: str,
    ctx: TenantDep,
    product_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    result = await bridge.list_stock_balances(
        tenant_id=ctx.user.business_id,
        store_id=store_id,
        product_id=product_id,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@router.get(
    "/{store_id}/history",
    response_model=PaginatedResponse[StockMovementItem],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def history(
    store_id: str,
    ctx: TenantDep,
    product_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    result = await services.inventory_history(
        session=ctx.session,
        business_id=ctx.user.business_id,
        product_id=product_id,
        store_id=store_id,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result.get("data", []),
        total=result.get("total", 0),
        page=result.get("page", page),
        page_size=result.get("page_size", page_size),
    )


@router.get(
    "/{store_id}/low-stock",
    response_model=DataResponse[list[LowStockItem]],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def store_low_stock(store_id: str, ctx: TenantDep):
    return ok(
        await services.low_stock_report(session=ctx.session, business_id=ctx.user.business_id)
    )


@router.post(
    "/{store_id}/distribute",
    response_model=DataResponse[DistributeResult],
    dependencies=[Depends(require_permission("inventory:transfer"))],
)
async def distribute_from_main(store_id: str, payload: StoreDistribute, ctx: TenantDep):
    try:
        result = await bridge.transfer_stock(
            tenant_id=ctx.user.business_id,
            product_id=str(payload.product_id),
            from_store_id=store_id,
            to_store_id=str(payload.to_store_id),
            qty=payload.qty,
            notes=payload.notes,
            created_by=ctx.user.user_id,
        )
        return ok(result)
    except ValueError as e:
        detail = str(e)
        if detail == "source_store_not_found" or detail == "destination_store_not_found":
            raise HTTPException(status_code=404, detail="Store not found")
        if detail == "same_store":
            raise HTTPException(status_code=400, detail="Cannot transfer to the same store")
        if detail == "source_balance_not_found":
            raise HTTPException(
                status_code=404,
                detail="No stock balance found for this product in source store",
            )
        if detail.startswith("insufficient_stock"):
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


@router.post(
    "/{store_id}/sync",
    response_model=DataResponse[SyncResult],
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def sync_store_products(store_id: str, ctx: TenantDep):
    try:
        result = await bridge.sync_store_products(
            tenant_id=ctx.user.business_id,
            store_id=store_id,
        )
        return ok(result)
    except ValueError as e:
        detail = str(e)
        if detail == "no_warehouse":
            raise HTTPException(status_code=400, detail="No warehouse store found")
        if detail == "store_not_found":
            raise HTTPException(status_code=404, detail="Store not found")
        raise HTTPException(status_code=400, detail=detail)


@router.patch(
    "/{store_id}/min-level/{product_id}",
    response_model=DataResponse[MinStockLevelResult],
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def set_min_stock_level(
    store_id: str, product_id: str, payload: SetMinStockLevel, ctx: TenantDep
):
    try:
        result = await bridge.set_min_stock_level(
            tenant_id=ctx.user.business_id,
            store_id=store_id,
            product_id=product_id,
            min_stock_level=payload.min_stock_level,
        )
        return ok(result)
    except ValueError as e:
        detail = str(e)
        if detail == "store_not_found":
            raise HTTPException(status_code=404, detail="Store not found")
        if detail == "stock_balance_not_found":
            raise HTTPException(status_code=404, detail="Stock balance not found for this product")
        raise HTTPException(status_code=400, detail=detail)


@router.post(
    "/{store_id}/transfer-requests",
    status_code=201,
    response_model=DataResponse[TransferRequestCreated],
    dependencies=[Depends(require_permission("inventory:transfer_request"))],
)
async def create_transfer_request(store_id: str, payload: TransferRequestCreate, ctx: TenantDep):
    try:
        result = await bridge.create_transfer_request(
            tenant_id=ctx.user.business_id,
            product_id=str(payload.product_id),
            requesting_store_id=store_id,
            supplying_store_id=str(payload.supplying_store_id),
            requested_qty=payload.requested_qty,
            notes=payload.notes,
            created_by=ctx.user.user_id,
        )
        return ok(result)
    except ValueError as e:
        detail = str(e)
        if detail == "requesting_store_not_found" or detail == "supplying_store_not_found":
            raise HTTPException(status_code=404, detail="Store not found")
        if detail == "cannot_request_from_self":
            raise HTTPException(status_code=400, detail="Cannot request from the same store")
        if detail == "cannot_request_from_main":
            raise HTTPException(status_code=400, detail="Cannot request stock from main store")
        if detail == "supplying_balance_not_found":
            raise HTTPException(
                status_code=404,
                detail="No stock balance found for this product in supplying store",
            )
        raise HTTPException(status_code=400, detail=detail)


@router.get(
    "/{store_id}/transfer-requests",
    response_model=PaginatedResponse[TransferRequestDetail],
    dependencies=[Depends(require_permission("inventory:transfer_request"))],
)
async def list_transfer_requests(
    store_id: str,
    ctx: TenantDep,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    result = await bridge.list_transfer_requests(
        tenant_id=ctx.user.business_id,
        status=status,
        store_id=store_id,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@router.get(
    "/transfer-requests/{request_id}",
    response_model=DataResponse[TransferRequestDetail],
    dependencies=[Depends(require_permission("inventory:transfer_request"))],
)
async def get_transfer_request(request_id: str, ctx: TenantDep):
    result = await bridge.get_transfer_request(
        tenant_id=ctx.user.business_id,
        request_id=request_id,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Transfer request not found")
    return ok(result)


@router.patch(
    "/transfer-requests/{request_id}/approve",
    response_model=DataResponse[TransferRequestApproved],
    dependencies=[Depends(require_permission("inventory:transfer_approve"))],
)
async def approve_transfer_request(
    request_id: str, payload: TransferRequestApprove, ctx: TenantDep
):
    try:
        result = await bridge.approve_transfer_request(
            tenant_id=ctx.user.business_id,
            request_id=request_id,
            approved_qty=payload.approved_qty,
            rejection_reason=payload.rejection_reason,
            approved_by=ctx.user.user_id,
            notes=payload.notes,
        )
        return ok(result)
    except ValueError as e:
        detail = str(e)
        if detail == "request_not_found":
            raise HTTPException(status_code=404, detail="Transfer request not found")
        if detail.startswith("invalid_status"):
            raise HTTPException(status_code=400, detail=detail)
        if detail.startswith("insufficient_stock"):
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


@router.post(
    "/{store_id}/transfer-requests/{request_id}/fulfill",
    response_model=DataResponse[TransferFulfilled],
    dependencies=[Depends(require_permission("inventory:transfer_fulfill"))],
)
async def fulfill_transfer_request(store_id: str, request_id: str, ctx: TenantDep):
    try:
        result = await bridge.fulfill_transfer_request(
            tenant_id=ctx.user.business_id,
            request_id=request_id,
        )
        return ok(result)
    except ValueError as e:
        detail = str(e)
        if detail == "request_not_found":
            raise HTTPException(status_code=404, detail="Transfer request not found")
        if detail.startswith("invalid_status"):
            raise HTTPException(status_code=400, detail=detail)
        if detail.startswith("insufficient_stock"):
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
