from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, ok

from . import repository as repo
from .models import Tax
from .schemas import TaxCreateCommand, TaxResult, TaxUpdateCommand
from .service import plan_create_tax

router = APIRouter(prefix="/taxes", tags=["Taxes"])


@router.get(
    "",
    response_model=DataResponse[list[TaxResult]],
    dependencies=[Depends(require_permission("taxes:read"))],
)
async def list_taxes(ctx: TenantDep, include_inactive: bool = False):
    taxes = await repo.list_taxes(ctx.session, ctx.user.business_id, include_inactive)
    return ok([
        TaxResult(
            id=t.id,
            tenant_id=t.tenant_id,
            name=t.name,
            rate=float(t.rate),
            is_active=t.is_active,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in taxes
    ])


@router.post(
    "",
    response_model=DataResponse[TaxResult],
    status_code=201,
    dependencies=[Depends(require_permission("taxes:manage"))],
)
async def create_tax(payload: TaxCreateCommand, ctx: TenantDep):
    command = TaxCreateCommand(
        tenant_id=ctx.user.business_id,
        name=payload.name,
        rate=payload.rate,
    )
    result, tax = plan_create_tax(command)
    await repo.create_tax(ctx.session, tax)
    return ok(result)


@router.patch(
    "/{tax_id}",
    response_model=DataResponse[TaxResult],
    dependencies=[Depends(require_permission("taxes:manage"))],
)
async def update_tax(tax_id: UUID, payload: TaxUpdateCommand, ctx: TenantDep):
    tax = await repo.update_tax(
        ctx.session,
        tax_id,
        ctx.user.business_id,
        name=payload.name,
        rate=payload.rate,
        is_active=payload.is_active,
    )
    if not tax:
        raise HTTPException(404, "Tax type not found")
    return ok(TaxResult(
        id=tax.id,
        tenant_id=tax.tenant_id,
        name=tax.name,
        rate=float(tax.rate),
        is_active=tax.is_active,
        created_at=tax.created_at,
        updated_at=tax.updated_at,
    ))


@router.delete(
    "/{tax_id}",
    dependencies=[Depends(require_permission("taxes:manage"))],
)
async def delete_tax(tax_id: UUID, ctx: TenantDep):
    deleted = await repo.delete_tax(ctx.session, tax_id, ctx.user.business_id)
    if not deleted:
        raise HTTPException(404, "Tax type not found")
    return ok(None, message="Tax type deactivated")
