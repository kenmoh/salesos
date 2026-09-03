import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, ok
from app.auth.schemas.responses import SuccessResponse
import app.common.bridge as bridge
from app.cart.schemas import (
    AddItemRequest,
    CartCreateRequest,
    CartCreatedResponse,
    CartDetailResponse,
    CartListItemResponse,
    CartItemResponse,
    CheckoutRequest,
    CheckoutResultResponse,
    VoidItemRequest,
)

router = APIRouter(prefix="/cart", tags=["Cart"])


@router.get(
    "",
    response_model=DataResponse[list[CartListItemResponse]],
    dependencies=[Depends(require_permission("cart:read"))],
)
async def list_carts(ctx: TenantDep):
    return ok(await bridge.list_carts(ctx.user.business_id))


@router.post(
    "",
    status_code=201,
    response_model=DataResponse[CartCreatedResponse],
    dependencies=[Depends(require_permission("cart:create"))],
)
async def create_cart(
    cart_data: CartCreateRequest,
    ctx: TenantDep,
):
    sid = f"sess_{uuid.uuid4().hex[:16]}"
    try:
        return ok(
            await bridge.create_or_resume_cart(
                tenant_id=ctx.user.business_id,
                session_id=sid,
                store_id=cart_data.store_id,
                customer_name=cart_data.customer_name,
                customer_phone=cart_data.customer_phone,
                actor_id=ctx.user.user_id,
            )
        )
    except ValueError as e:
        if str(e) == "fee_balance_exceeded":
            raise HTTPException(
                status_code=402,
                detail="Fee balance exceeded. Please clear outstanding fees before creating a new sale.",
            )
        raise


@router.delete(
    "/items/{item_id}",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("cart:delete"))],
)
async def remove_item(item_id: str, ctx: TenantDep):
    await bridge.remove_cart_item(
        tenant_id=ctx.user.business_id,
        item_id=item_id,
        actor_id=ctx.user.user_id,
    )
    return ok({"success": True})


@router.post(
    "/items/{item_id}/void",
    response_model=DataResponse[SuccessResponse],
)
async def void_item(item_id: str, body: VoidItemRequest, ctx: TenantDep, request: Request):
    from app.core.redis_client import get_cache_redis
    import time

    client = await get_cache_redis()
    ip = request.client.host if request.client else "0.0.0.0"
    rate_key = f"sf:pin_attempts:{ip}:{ctx.user.user_id}"

    if client:
        try:
            count = await client.get(rate_key)
            if count and int(count) >= 5:
                raise HTTPException(
                    status_code=429,
                    detail="Too many PIN attempts. Try again in 60 seconds.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    try:
        await bridge.void_cart_item(
            tenant_id=ctx.user.business_id,
            item_id=item_id,
            actor_id=ctx.user.user_id,
            supervisor_pin=body.supervisor_pin,
        )
        if client:
            try:
                await client.delete(rate_key)
            except Exception:
                pass
        return ok({"success": True})
    except ValueError as e:
        if client:
            try:
                pipe = await client.pipeline()
                await pipe.incr(rate_key)
                await pipe.expire(rate_key, 60)
                await pipe.execute()
            except Exception:
                pass

        if str(e) == "invalid_supervisor_pin":
            raise HTTPException(status_code=403, detail="Invalid supervisor PIN")
        if str(e) == "cart_item_not_found":
            raise HTTPException(status_code=404, detail="Cart item not found")
        raise


@router.post(
    "/{cart_id}/items",
    status_code=201,
    response_model=DataResponse[CartItemResponse],
    dependencies=[Depends(require_permission("cart:create"))],
)
async def add_item(cart_id: str, body: AddItemRequest, ctx: TenantDep):
    return ok(
        await bridge.add_to_cart(
            tenant_id=ctx.user.business_id,
            cart_id=cart_id,
            product_id=body.product_id,
            qty=body.qty,
            actor_id=ctx.user.user_id,
        )
    )


@router.get(
    "/{cart_id}",
    response_model=DataResponse[CartDetailResponse],
    dependencies=[Depends(require_permission("cart:read"))],
)
async def get_cart(cart_id: str, ctx: TenantDep):
    return ok(await bridge.get_cart(ctx.user.business_id, cart_id))


@router.post(
    "/{cart_id}/checkout",
    response_model=DataResponse[CheckoutResultResponse],
    dependencies=[Depends(require_permission("cart:checkout"))],
)
async def checkout_cart(cart_id: str, body: CheckoutRequest, ctx: TenantDep):
    try:
        items_raw = None
        if body.items:
            items_raw = [i.model_dump(mode="json") for i in body.items]
        return ok(
            await bridge.checkout_cart(
                tenant_id=ctx.user.business_id,
                cart_id=cart_id,
                actor_id=ctx.user.user_id,
                items=items_raw,
                customer_name=body.customer_name,
                customer_phone=body.customer_phone,
                store_id=body.store_id,
                coupon_code=body.coupon_code,
                discount_id=body.discount_id,
            )
        )
    except ValueError as e:
        if str(e) == "fee_balance_exceeded":
            raise HTTPException(
                status_code=402,
                detail="Fee balance exceeded. Please clear outstanding fees before completing this sale.",
            )
        raise
