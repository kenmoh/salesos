from fastapi import APIRouter, Depends

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from app.auth.schemas.schema import SaleCreate, VoidSale
from app.auth.schemas.responses import (
    SaleCreated,
    SaleDetail,
    SaleListItem,
    SaleReturnResult,
    SuccessResponse,
)
import app.common.bridge as bridge
from app.common import services

router = APIRouter(prefix="/sales", tags=["Sales"])


@router.post(
    "",
    status_code=201,
    response_model=DataResponse[SaleCreated],
    dependencies=[Depends(require_permission("sales:create"))],
)
async def create_sale(payload: SaleCreate, ctx: TenantDep):
    items = [i.model_dump() for i in payload.items]
    return ok(
        await bridge.create_sale_via_service(
            tenant_id=ctx.user.business_id,
            cashier_id=ctx.user.user_id,
            items=items,
            discount=payload.discount,
            customer_name=payload.customer_name,
            customer_phone=payload.customer_phone,
            store_id=str(payload.store_id) if payload.store_id else None,
            notes=payload.notes,
        )
    )


@router.get(
    "",
    response_model=PaginatedResponse[SaleListItem],
    dependencies=[Depends(require_permission("sales:read"))],
)
async def list_sales(
    ctx: TenantDep,
    status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    cashier_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    result = await services.list_sales(
        session=ctx.session,
        business_id=ctx.user.business_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        cashier_id=cashier_id,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@router.get(
    "/{sale_id}",
    response_model=DataResponse[SaleDetail],
    dependencies=[Depends(require_permission("sales:read"))],
)
async def get_sale(sale_id: str, ctx: TenantDep):
    return ok(
        await services.get_sale(
            session=ctx.session, business_id=ctx.user.business_id, sale_id=sale_id
        )
    )


@router.post(
    "/{sale_id}/void",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("sales:void"))],
)
async def void_sale(sale_id: str, payload: VoidSale, ctx: TenantDep):
    return ok(
        {
            "success": await services.void_sale(
                session=ctx.session,
                business_id=ctx.user.business_id,
                user_id=ctx.user.user_id,
                sale_id=sale_id,
                reason=payload.reason,
            )
        }
    )


@router.post(
    "/{sale_id}/return",
    response_model=DataResponse[SaleReturnResult],
    dependencies=[Depends(require_permission("sales:void"))],
)
async def return_sale(sale_id: str, payload: VoidSale, ctx: TenantDep):
    result = await services.return_sale(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=ctx.user.user_id,
        sale_id=sale_id,
        reason=payload.reason,
    )
    return ok(result)
