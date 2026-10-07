from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import DbTenantDep, require_permission
from app.core.responses import DataResponse, ok

from sqlalchemy import select

from app.accounting.models import ChartOfAccount

from . import repository as repo
from .models import Tax
from .schemas import TaxCreateCommand, TaxResult, TaxUpdateCommand
from .service import plan_create_tax

router = APIRouter(prefix="/taxes", tags=["Taxes"])


def _result(tax: Tax) -> TaxResult:
    return TaxResult(
        id=tax.id,
        tenant_id=tax.tenant_id,
        name=tax.name,
        rate=float(tax.rate),
        is_active=tax.is_active,
        account_code=tax.account_code,
        created_at=tax.created_at,
        updated_at=tax.updated_at,
    )


async def _require_account(ctx: DbTenantDep, account_code: str | None) -> None:
    """Refuse an account the tenant does not have.

    A mistyped code would otherwise look saved everywhere, and only fail when
    the sale it belongs to is posted -- at the worst possible moment.
    """
    if not account_code:
        return
    row = await ctx.session.execute(
        select(ChartOfAccount.id).where(
            ChartOfAccount.tenant_id == ctx.user.business_id,
            ChartOfAccount.code == account_code,
        )
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(400, f"Unknown account code: {account_code}")


@router.get(
    "",
    response_model=DataResponse[list[TaxResult]],
    dependencies=[Depends(require_permission("taxes:read"))],
)
async def list_taxes(ctx: DbTenantDep, include_inactive: bool = False):
    taxes = await repo.list_taxes(ctx.session, ctx.user.business_id, include_inactive)
    return ok([_result(t) for t in taxes])


@router.post(
    "",
    response_model=DataResponse[TaxResult],
    status_code=201,
    dependencies=[Depends(require_permission("taxes:manage"))],
)
async def create_tax(payload: TaxCreateCommand, ctx: DbTenantDep):
    await _require_account(ctx, payload.account_code)
    command = TaxCreateCommand(
        tenant_id=ctx.user.business_id,
        name=payload.name,
        rate=payload.rate,
        account_code=payload.account_code,
    )
    result, tax = plan_create_tax(command)
    await repo.create_tax(ctx.session, tax)
    return ok(result)


@router.patch(
    "/{tax_id}",
    response_model=DataResponse[TaxResult],
    dependencies=[Depends(require_permission("taxes:manage"))],
)
async def update_tax(tax_id: UUID, payload: TaxUpdateCommand, ctx: DbTenantDep):
    await _require_account(ctx, payload.account_code)
    tax = await repo.update_tax(
        ctx.session,
        tax_id,
        ctx.user.business_id,
        name=payload.name,
        rate=payload.rate,
        is_active=payload.is_active,
        account_code=payload.account_code,
    )
    if not tax:
        raise HTTPException(404, "Tax type not found")
    return ok(_result(tax))


@router.delete(
    "/{tax_id}",
    dependencies=[Depends(require_permission("taxes:manage"))],
)
async def delete_tax(tax_id: UUID, ctx: DbTenantDep):
    deleted = await repo.delete_tax(ctx.session, tax_id, ctx.user.business_id)
    if not deleted:
        raise HTTPException(404, "Tax type not found")
    return ok(None, message="Tax type deactivated")
