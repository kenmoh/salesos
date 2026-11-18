"""Pure planning for cashier tips and settlements."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from app.sales.models import Sale, SaleItem
from app.tips.models import CashierTip, CashierTipSettlement
from app.tips.schemas import SettlementRequest

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def build_tips_for_sale(sale: Sale, items: list[SaleItem]) -> list[CashierTip]:
    """Create one tip per sale line with a positive per-unit ``extra``.

    A negative ``extra`` is a price override on the customer's bill, not money
    handed to the cashier, so it is ignored here.
    """
    if not sale.cashier_id:
        return []

    earned_at = sale.created_at or datetime.now(UTC)
    tips: list[CashierTip] = []
    for item in items:
        extra = Decimal(str(item.extra or 0))
        qty = Decimal(str(item.qty or 0))
        if extra <= 0 or qty <= 0:
            continue
        amount = money(extra * qty)
        if amount <= 0:
            continue
        tips.append(
            CashierTip(
                id=uuid4(),
                tenant_id=sale.tenant_id,
                sale_id=sale.id,
                sale_item_id=item.id,
                cashier_id=sale.cashier_id,
                store_id=sale.store_id,
                extra=money(extra),
                qty=qty,
                amount=amount,
                status="pending",
                earned_at=earned_at,
            )
        )
    return tips


def plan_settlement(
    command: SettlementRequest,
    tips: list[CashierTip],
    *,
    paid_by: UUID,
    paid_at: datetime | None = None,
) -> tuple[CashierTipSettlement, Decimal]:
    """Sum the locked tips into one paid settlement.

    Raises:
        ValueError("invalid_period"): the range is reversed.
        ValueError("no_tips_to_settle"): every candidate was already settled
            (or voided) by a concurrent request.
    """
    if command.period_start > command.period_end:
        raise ValueError("invalid_period")
    if not tips:
        raise ValueError("no_tips_to_settle")

    amount = money(sum((Decimal(str(t.amount)) for t in tips), Decimal("0")))
    settlement = CashierTipSettlement(
        id=uuid4(),
        tenant_id=tips[0].tenant_id,
        cashier_id=command.cashier_id,
        period_start=command.period_start,
        period_end=command.period_end,
        amount=amount,
        tip_count=len(tips),
        status="paid",
        note=command.note,
        paid_by=paid_by,
        paid_at=paid_at or datetime.now(UTC),
    )
    return settlement, amount
