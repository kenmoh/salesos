"""Platform fee calculation and ledger management.

This module handles the calculation of platform commission fees for transactions,
tracking pending fee balances, and managing fee ledger entries.

The fee calculation follows a tiered rule system where:
1. Rules are ordered by minimum threshold (descending)
2. The first rule where the sale total >= threshold is applied
3. Fees can be either flat (fixed amount) or percentage-based

Typical usage:
    from app.platform.fee_calculator import calculate_platform_fee

    # Calculate fee for a 5000 NGN sale
    result = await calculate_platform_fee(session, 5000.0)
    # Returns: {"platform_fee": 100.0, "settlement_amount": 4900.0, ...}
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import desc, func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _round(val: Decimal) -> float:
    """Round a Decimal value to 2 decimal places.

    Args:
        val: The Decimal value to round.

    Returns:
        Rounded float value.
    """
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def calculate_platform_fee(session: AsyncSession, sale_total: float) -> dict:
    """Calculate platform commission fee for a sale.

    Applies tiered commission rules to determine the fee amount.
    Rules are matched by minimum threshold (highest first).

    Args:
        session: Database session for querying commission rules.
        sale_total: Total sale amount in Nigerian Naira.

    Returns:
        Dict containing:
            - "rule_id": Identifier of the matched commission rule (None if no rule)
            - "label": Human-readable rule name
            - "fee_type": "flat" or "percentage"
            - "rate": Commission rate applied
            - "min_threshold": Minimum sale amount for this rule
            - "sale_total": Original sale total
            - "platform_fee": Calculated fee amount
            - "settlement_amount": Amount after fee deduction (total - fee)

    Example:
        # For a 5000 NGN sale with a 2% rule:
        # {"platform_fee": 100.0, "settlement_amount": 4900.0}

        # For a 500 NGN sale with a flat 100 NGN rule:
        # {"platform_fee": 100.0, "settlement_amount": 400.0}
    """
    from app.platform.models import FeeType, PlatformCommission

    total = Decimal(str(sale_total))

    result = await session.execute(
        select(PlatformCommission).order_by(desc(PlatformCommission.min_threshold))
    )
    rules = result.scalars().all()

    for rule in rules:
        threshold = Decimal(str(rule.min_threshold))
        if total >= threshold:
            rate = Decimal(str(rule.amount))
            if rule.fee_type == FeeType.FLAT.value:
                fee = rate
            else:
                fee = (total * rate / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            return {
                "rule_id": str(rule.id),
                "label": rule.label,
                "fee_type": rule.fee_type,
                "rate": _round(rate),
                "min_threshold": _round(threshold),
                "sale_total": _round(total),
                "platform_fee": _round(fee),
                "settlement_amount": _round(total - fee),
            }

    return {
        "rule_id": None,
        "label": "no_rule",
        "fee_type": "flat",
        "rate": 0,
        "min_threshold": 0,
        "sale_total": _round(total),
        "platform_fee": 0,
        "settlement_amount": _round(total),
    }


async def get_pending_fee_balance(session: AsyncSession, tenant_id: UUID) -> float:
    """Get the total pending fee balance for a tenant.

    Sums all unpaid fee ledger entries for the specified tenant.
    Used to gate new sales when fees accumulate beyond threshold.

    Args:
        session: Database session for querying fee ledger.
        tenant_id: Unique identifier of the tenant business.

    Returns:
        Total pending fee balance in Nigerian Naira.
    """
    from app.platform.models import PlatformFeeLedger

    result = await session.execute(
        select(func.coalesce(func.sum(PlatformFeeLedger.amount), 0)).where(
            PlatformFeeLedger.tenant_id == tenant_id,
            PlatformFeeLedger.status == "pending",
        )
    )
    return float(result.scalar())


async def get_max_pending_balance(session: AsyncSession) -> float:
    """Get the maximum allowed pending fee balance.

    Returns the threshold from the first PlatformCommission rule.
    When a tenant's pending balance exceeds this, new sales are blocked.

    Args:
        session: Database session for querying commission rules.

    Returns:
        Maximum pending balance threshold (default: 1000.0 NGN).
    """
    from app.platform.models import PlatformCommission

    result = await session.execute(select(PlatformCommission.max_pending_balance).limit(1))
    val = result.scalar()
    return float(val) if val else 1000.0


async def assert_fee_headroom(session: AsyncSession, tenant_id: UUID) -> None:
    """Refuse the action when the tenant's unpaid fees have reached the limit.

    Selling is what accrues the debt, so every path that produces revenue checks
    here: opening a cart, checking one out, and creating a document. A quote,
    invoice, receipt or purchase order is the beginning of a sale, and letting
    one be written while the tenant is already over the threshold just deepens
    the hole.

    Raises ValueError("fee_balance_exceeded"), which the routes surface as 402.
    """
    pending_balance = await get_pending_fee_balance(session, tenant_id)
    max_balance = await get_max_pending_balance(session)
    if pending_balance >= max_balance:
        raise ValueError("fee_balance_exceeded")


async def record_platform_fee(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    total: float,
    sale_id: UUID | None = None,
    document_id: UUID | None = None,
    payment_method: str = "document",
    status: str = "pending",
) -> dict | None:
    """Book the platform's commission for something that earned it.

    The fee was only ever charged when a sale reached "completed", which left
    two paths earning revenue for the tenant with nothing booked against them: a
    converted document, whose sale is pending forever because nothing pays it,
    and a standalone receipt, which is not a sale at all. Both are charged here,
    when the document is created.

    The existing row for the same sale or document is checked first. Without
    that, a converted sale charged here would be charged again when it is later
    paid in full and completes through the normal payment path.

    Exactly one of ``sale_id`` / ``document_id`` identifies what the fee is for.

    Returns the fee result, or None when no rule matched or a fee already
    exists.
    """
    from sqlalchemy import or_

    from app.platform.models import PlatformFeeLedger

    if not sale_id and not document_id:
        raise ValueError("a platform fee must reference a sale or a document")

    existing = (
        await session.execute(
            select(PlatformFeeLedger.id).where(
                PlatformFeeLedger.tenant_id == tenant_id,
                or_(
                    PlatformFeeLedger.sale_id == sale_id
                    if sale_id
                    else False,
                    PlatformFeeLedger.document_id == document_id
                    if document_id
                    else False,
                ),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return None

    fee_result = await calculate_platform_fee(session, total)
    if fee_result["platform_fee"] <= 0:
        return None

    session.add(
        PlatformFeeLedger(
            tenant_id=tenant_id,
            sale_id=sale_id,
            document_id=document_id,
            amount=float(Decimal(str(fee_result["platform_fee"])).quantize(Decimal("0.01"))),
            fee_type=fee_result["fee_type"],
            rate=fee_result["rate"],
            payment_method=payment_method,
            status=status,
        )
    )
    return fee_result
