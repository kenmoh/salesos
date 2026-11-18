"""Cashier tip reporting and settlement endpoints."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import DbTenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated

from . import repository as repo
from .schemas import (
    SettlementDetail,
    SettlementRequest,
    SettlementResult,
    SettlementTipRow,
    TipRow,
    TipSummaryRow,
)
from .service import plan_settlement

router = APIRouter(prefix="/tips", tags=["Tips"])


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"Invalid {field} date, expected YYYY-MM-DD")


def _parse_uuid(value: str | None, field: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, f"Invalid {field}")


def _scope_cashier(ctx: DbTenantDep, requested: str | None) -> UUID | None:
    """Non-settlement roles may only ever see their own tips."""
    if ctx.user.has_perm("tips:settle"):
        return _parse_uuid(requested, "cashier_id")
    return UUID(ctx.user.user_id)


def _tip_row(row) -> TipRow:
    tip, sale_number, sale_status, product_name, cashier_name = row
    return TipRow(
        id=str(tip.id),
        sale_id=str(tip.sale_id),
        sale_number=sale_number,
        sale_item_id=str(tip.sale_item_id),
        product_name=product_name,
        cashier_id=str(tip.cashier_id),
        cashier_name=cashier_name,
        store_id=str(tip.store_id),
        extra=float(tip.extra),
        qty=float(tip.qty),
        amount=float(tip.amount),
        status=tip.status,
        sale_status=sale_status,
        earned_at=tip.earned_at,
        settlement_id=str(tip.settlement_id) if tip.settlement_id else None,
        settled_at=tip.settled_at,
    )


def _settlement_result(settlement) -> SettlementResult:
    return SettlementResult(
        id=str(settlement.id),
        cashier_id=str(settlement.cashier_id),
        period_start=settlement.period_start,
        period_end=settlement.period_end,
        amount=float(settlement.amount),
        tip_count=settlement.tip_count,
        status=settlement.status,
        note=settlement.note,
        paid_by=str(settlement.paid_by),
        paid_at=settlement.paid_at,
    )


@router.get(
    "",
    response_model=PaginatedResponse[TipRow],
    dependencies=[Depends(require_permission("tips:read"))],
)
async def list_tips(
    ctx: DbTenantDep,
    cashier_id: str | None = None,
    status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    rows, total = await repo.list_tips(
        ctx.session,
        tenant_id=ctx.user.business_id,
        cashier_id=_scope_cashier(ctx, cashier_id),
        status=status,
        from_date=_parse_date(from_date, "from_date"),
        to_date=_parse_date(to_date, "to_date"),
        page=page,
        page_size=page_size,
    )
    return paginated(
        [_tip_row(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/summary",
    response_model=DataResponse[list[TipSummaryRow]],
    dependencies=[Depends(require_permission("tips:read"))],
)
async def tip_summary(
    ctx: DbTenantDep,
    cashier_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    rows = await repo.tip_summary(
        ctx.session,
        tenant_id=ctx.user.business_id,
        cashier_id=_scope_cashier(ctx, cashier_id),
        from_date=_parse_date(from_date, "from_date"),
        to_date=_parse_date(to_date, "to_date"),
    )
    return ok(
        [
            TipSummaryRow(
                cashier_id=str(cashier),
                cashier_name=name,
                unsettled_amount=float(unsettled_amount or 0),
                unsettled_count=int(unsettled_count or 0),
                settled_amount=float(settled_amount or 0),
                settled_count=int(settled_count or 0),
                last_settlement_at=last_settlement_at,
            )
            for (
                cashier,
                name,
                unsettled_amount,
                unsettled_count,
                settled_amount,
                settled_count,
                last_settlement_at,
            ) in rows
        ]
    )


@router.post(
    "/settlements",
    response_model=DataResponse[SettlementResult],
    status_code=201,
    dependencies=[Depends(require_permission("tips:settle"))],
)
async def create_settlement(payload: SettlementRequest, ctx: DbTenantDep):
    if not await repo.cashier_exists(
        ctx.session,
        tenant_id=ctx.user.business_id,
        cashier_id=payload.cashier_id,
    ):
        raise HTTPException(404, "cashier_not_found")

    tips = await repo.lock_payable_tips(
        ctx.session,
        tenant_id=ctx.user.business_id,
        cashier_id=payload.cashier_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )
    try:
        settlement, _amount = plan_settlement(
            payload, tips, paid_by=UUID(ctx.user.user_id)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    await repo.create_settlement(ctx.session, settlement)
    await repo.mark_tips_settled(
        ctx.session,
        [tip.id for tip in tips],
        settlement.id,
        settlement.paid_at,
    )
    return ok(_settlement_result(settlement), message="settled")


@router.get(
    "/settlements",
    response_model=PaginatedResponse[SettlementResult],
    dependencies=[Depends(require_permission("tips:read"))],
)
async def list_settlements(
    ctx: DbTenantDep,
    cashier_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    settlements, total = await repo.list_settlements(
        ctx.session,
        tenant_id=ctx.user.business_id,
        cashier_id=_scope_cashier(ctx, cashier_id),
        from_date=_parse_date(from_date, "from_date"),
        to_date=_parse_date(to_date, "to_date"),
        page=page,
        page_size=page_size,
    )
    return paginated(
        [_settlement_result(settlement) for settlement in settlements],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/settlements/{settlement_id}",
    response_model=DataResponse[SettlementDetail],
    dependencies=[Depends(require_permission("tips:read"))],
)
async def get_settlement(settlement_id: UUID, ctx: DbTenantDep):
    settlement = await repo.get_settlement(
        ctx.session,
        tenant_id=ctx.user.business_id,
        settlement_id=settlement_id,
    )
    if settlement is None:
        raise HTTPException(404, "settlement_not_found")
    if not ctx.user.has_perm("tips:settle") and settlement.cashier_id != UUID(
        ctx.user.user_id
    ):
        raise HTTPException(404, "settlement_not_found")

    rows = await repo.settlement_tips(
        ctx.session,
        tenant_id=ctx.user.business_id,
        settlement_id=settlement_id,
    )
    result = _settlement_result(settlement)
    return ok(
        SettlementDetail(
            **result.model_dump(),
            tips=[
                SettlementTipRow(
                    id=str(tip.id),
                    sale_id=str(tip.sale_id),
                    sale_number=sale_number,
                    sale_item_id=str(tip.sale_item_id),
                    product_name=product_name,
                    amount=float(tip.amount),
                    earned_at=tip.earned_at,
                )
                for tip, sale_number, product_name in rows
            ],
        )
    )
