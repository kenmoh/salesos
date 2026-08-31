from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
import app.common.bridge as bridge


class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    phone: str | None = None
    email: str | None = None
    address: str | None = None


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = None
    email: str | None = None
    address: str | None = None


router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post(
    "",
    status_code=201,
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("customers:create"))],
)
async def create_customer(payload: CustomerCreate, ctx: TenantDep):
    return ok(
        await bridge.create_customer(
            tenant_id=ctx.user.business_id,
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
            address=payload.address,
        )
    )


@router.get(
    "",
    response_model=PaginatedResponse[dict],
    dependencies=[Depends(require_permission("customers:read"))],
)
async def list_customers(
    ctx: TenantDep,
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    result = await bridge.list_customers(
        tenant_id=ctx.user.business_id,
        page=page,
        page_size=page_size,
        search=search,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@router.get(
    "/{customer_id}",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("customers:read"))],
)
async def get_customer(customer_id: str, ctx: TenantDep):
    customer = await bridge.get_customer(tenant_id=ctx.user.business_id, customer_id=customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return ok(customer)


@router.patch(
    "/{customer_id}",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("customers:update"))],
)
async def update_customer(customer_id: str, payload: CustomerUpdate, ctx: TenantDep):
    result = await bridge.update_customer(
        tenant_id=ctx.user.business_id,
        customer_id=customer_id,
        **payload.model_dump(exclude_unset=True),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Customer not found")
    return ok(result)


@router.delete(
    "/{customer_id}",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("customers:delete"))],
)
async def delete_customer(customer_id: str, ctx: TenantDep):
    ok_ = await bridge.delete_customer(tenant_id=ctx.user.business_id, customer_id=customer_id)
    if not ok_:
        raise HTTPException(status_code=404, detail="Customer not found")
    return ok({"success": True, "customer_id": customer_id})
