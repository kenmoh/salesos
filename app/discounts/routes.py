"""Discount & Coupon routes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from app.discounts.schemas import (
    CreateCouponRequest,
    CreateDiscountRequest,
    CouponResponse,
    DiscountResponse,
    UpdateCouponRequest,
    UpdateDiscountRequest,
    ValidateCouponRequest,
    ValidateCouponResponse,
)
import app.common.bridge as bridge

router = APIRouter(prefix="/discounts", tags=["Discounts"])


# ═══════════════════════════════════════════════════════════════════════════════
#  DISCOUNTS (PROMOTIONS)
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "",
    status_code=201,
    response_model=DataResponse[DiscountResponse],
    dependencies=[Depends(require_permission("discounts:create"))],
)
async def create_discount(payload: CreateDiscountRequest, ctx: TenantDep):
    return ok(
        await bridge.create_discount(
            tenant_id=ctx.user.business_id,
            name=payload.name,
            discount_type=payload.discount_type,
            value=payload.value,
            buy_x_get_y_free_qty=payload.buy_x_get_y_free_qty,
            scope=payload.scope,
            min_order=payload.min_order,
            is_active=payload.is_active,
            start_date=payload.start_date,
            end_date=payload.end_date,
            product_ids=payload.product_ids,
            category_ids=payload.category_ids,
        )
    )


@router.get(
    "",
    response_model=PaginatedResponse[DiscountResponse],
    dependencies=[Depends(require_permission("discounts:read"))],
)
async def list_discounts(
    ctx: TenantDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    active_only: bool = Query(False),
):
    result = await bridge.list_discounts(
        tenant_id=ctx.user.business_id,
        page=page,
        page_size=page_size,
        active_only=active_only,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@router.get(
    "/{discount_id}",
    response_model=DataResponse[DiscountResponse],
    dependencies=[Depends(require_permission("discounts:read"))],
)
async def get_discount(discount_id: str, ctx: TenantDep):
    result = await bridge.get_discount(tenant_id=ctx.user.business_id, discount_id=discount_id)
    if not result:
        raise HTTPException(status_code=404, detail="Discount not found")
    return ok(result)


@router.patch(
    "/{discount_id}",
    response_model=DataResponse[DiscountResponse],
    dependencies=[Depends(require_permission("discounts:update"))],
)
async def update_discount(discount_id: str, payload: UpdateDiscountRequest, ctx: TenantDep):
    result = await bridge.update_discount(
        tenant_id=ctx.user.business_id,
        discount_id=discount_id,
        **payload.model_dump(exclude_unset=True),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Discount not found")
    return ok(result)


@router.patch(
    "/{discount_id}/toggle",
    response_model=DataResponse[DiscountResponse],
    dependencies=[Depends(require_permission("discounts:update"))],
)
async def toggle_discount(discount_id: str, ctx: TenantDep):
    result = await bridge.toggle_discount(
        tenant_id=ctx.user.business_id, discount_id=discount_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Discount not found")
    return ok(result)


@router.delete(
    "/{discount_id}",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("discounts:delete"))],
)
async def delete_discount(discount_id: str, ctx: TenantDep):
    ok_ = await bridge.delete_discount(
        tenant_id=ctx.user.business_id, discount_id=discount_id
    )
    if not ok_:
        raise HTTPException(status_code=404, detail="Discount not found")
    return ok({"success": True, "discount_id": discount_id})


# ═══════════════════════════════════════════════════════════════════════════════
#  COUPONS
# ═══════════════════════════════════════════════════════════════════════════════

# Mount coupon routes under /coupons
coupon_router = APIRouter(prefix="/coupons", tags=["Coupons"])


@coupon_router.post(
    "",
    status_code=201,
    response_model=DataResponse[CouponResponse],
    dependencies=[Depends(require_permission("discounts:create"))],
)
async def create_coupon(payload: CreateCouponRequest, ctx: TenantDep):
    return ok(
        await bridge.create_coupon(
            tenant_id=ctx.user.business_id,
            code=payload.code,
            discount_type=payload.discount_type,
            value=payload.value,
            max_uses=payload.max_uses,
            min_order=payload.min_order,
            is_active=payload.is_active,
            expires_at=payload.expires_at,
        )
    )


@coupon_router.get(
    "",
    response_model=PaginatedResponse[CouponResponse],
    dependencies=[Depends(require_permission("discounts:read"))],
)
async def list_coupons(
    ctx: TenantDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    result = await bridge.list_coupons(
        tenant_id=ctx.user.business_id,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@coupon_router.get(
    "/{coupon_id}",
    response_model=DataResponse[CouponResponse],
    dependencies=[Depends(require_permission("discounts:read"))],
)
async def get_coupon(coupon_id: str, ctx: TenantDep):
    result = await bridge.get_coupon(tenant_id=ctx.user.business_id, coupon_id=coupon_id)
    if not result:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return ok(result)


@coupon_router.patch(
    "/{coupon_id}",
    response_model=DataResponse[CouponResponse],
    dependencies=[Depends(require_permission("discounts:update"))],
)
async def update_coupon(coupon_id: str, payload: UpdateCouponRequest, ctx: TenantDep):
    result = await bridge.update_coupon(
        tenant_id=ctx.user.business_id,
        coupon_id=coupon_id,
        **payload.model_dump(exclude_unset=True),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return ok(result)


@coupon_router.delete(
    "/{coupon_id}",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("discounts:delete"))],
)
async def delete_coupon(coupon_id: str, ctx: TenantDep):
    ok_ = await bridge.delete_coupon(
        tenant_id=ctx.user.business_id, coupon_id=coupon_id
    )
    if not ok_:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return ok({"success": True, "coupon_id": coupon_id})


@coupon_router.post(
    "/validate",
    response_model=DataResponse[ValidateCouponResponse],
    dependencies=[Depends(require_permission("discounts:read"))],
)
async def validate_coupon_endpoint(payload: ValidateCouponRequest, ctx: TenantDep):
    result = await bridge.validate_coupon(
        tenant_id=ctx.user.business_id,
        code=payload.coupon_code,
        cart_subtotal=payload.cart_subtotal,
    )
    return ok(result)
