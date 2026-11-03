"""Platform fee calculation and ledger management.

This module handles the calculation of platform commission fees for transactions,
the flat fee per created document, tracking pending fee balances, and managing
fee ledger entries.

Sales commission follows a tiered rule system where:
1. Rules are ordered by minimum threshold (descending)
2. The first rule where the sale total >= threshold is applied
3. Fees can be either flat (fixed amount) or percentage-based

Documents carry their own charge: a flat ``document_fee`` booked at creation
for every type, not a share of the document's value.

Typical usage:
    from app.platform.fee_calculator import calculate_platform_fee, book_document_fee

    # Calculate fee for a 5000 NGN sale
    result = await calculate_platform_fee(session, 5000.0)
    # Returns: {"platform_fee": 100.0, "settlement_amount": 4900.0, ...}

    # Book the flat fee for a created document
    await book_document_fee(session, tenant_id=..., document_id=...)
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


def sale_fee_base(sale) -> float:
    """Get the value the commission applies to: pre-tax, post-discount.

    Tax belongs to the government, not the platform, so the commission is
    never taken on the tax portion of a sale. Falls back to
    ``total - tax`` for legacy rows that were saved without a subtotal.
    """
    try:
        subtotal = float(sale.subtotal or 0)
        discount = float(sale.discount or 0)
        tax = float(sale.tax or 0)
        total = float(sale.total or 0)
    except (TypeError, ValueError):
        return max(total, 0.0) if "total" in dir() else 0.0

    base = subtotal - discount
    if base <= 0 and total > 0:
        base = total - tax
    return max(base, 0.0)


# Payment methods whose money arrives without an external processor. The full
# rate bundles the processor's share, which these collections never incurred,
# so they are charged the platform-only rate (rule.cash_amount) instead.
NO_PROCESSOR_METHODS = frozenset({"cash", "document"})


def rate_for_rule(rule, method: str | None) -> Decimal:
    """Pick the rate a rule charges for a payment collected via ``method``.

    Cash and other processorless methods are charged ``cash_amount`` when the
    rule defines one, and the full ``amount`` otherwise. Gateway-processed
    methods (card, transfer) always pay ``amount``.
    """
    if method in NO_PROCESSOR_METHODS and getattr(rule, "cash_amount", None) is not None:
        return Decimal(str(rule.cash_amount))
    return Decimal(str(rule.amount))


async def calculate_platform_fee(
    session: AsyncSession, sale_total: float, method: str | None = None
) -> dict:
    """Apply a tiered commission rule to the given base amount.

    Applies tiered commission rules to determine the fee amount.
    Rules are matched by minimum threshold (highest first).

    Args:
        session: Database session for querying commission rules.
        sale_total: Commission base in Nigerian Naira (pre-tax,
            post-discount sale value).
        method: Payment method that produced the money (e.g. "cash",
            "card"). Methods without an external processor are charged the
            rule's cash_amount.

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
            rate = rate_for_rule(rule, method)
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


async def calculate_fee_shares(
    session: AsyncSession, fee_base: float, method_amounts: dict[str, float]
) -> dict:
    """Compute each method's fee for a batch that mixes collection channels.

    Every method is charged its own rate — cash at the platform-only rate,
    card/transfer at the full rate — so a batch is never billed the
    processor's share on its processorless portion. Each method's fee is
    calculated on the full commission base with its rate, then scaled by that
    method's share of the money actually collected.

    Args:
        session: Database session for querying commission rules.
        fee_base: Commission base in Naira (pre-tax, post-discount).
        method_amounts: Maps payment method to the amount collected with it.

    Returns:
        Dict containing:
            - "rule_id": Identifier of the matched commission rule (None if no rule)
            - "total_fee": Sum of all method shares
            - "shares": One dict per method with "method", "amount",
              "rate" and "fee_type" for its ledger row.
    """
    total_collected = Decimal(str(sum(method_amounts.values())))
    total_fee = Decimal("0")
    rule_id = None
    shares: list[dict] = []

    for method, amount in method_amounts.items():
        if Decimal(str(amount)) <= 0:
            continue
        result = await calculate_platform_fee(session, fee_base, method=method)
        rule_id = result["rule_id"]
        fraction = (
            Decimal(str(amount)) / total_collected if total_collected > 0 else Decimal("0")
        )
        share = (Decimal(str(result["platform_fee"])) * fraction).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_fee += share
        shares.append(
            {
                "method": method,
                "amount": _round(share),
                "rate": result["rate"],
                "fee_type": result["fee_type"],
            }
        )

    return {"rule_id": rule_id, "total_fee": _round(total_fee), "shares": shares}


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


async def get_document_fee(session: AsyncSession) -> float:
    """Get the flat NGN fee charged for every document created.

    Reads ``document_fee`` from the newest commission rule. The fee is not
    tiered: one document costs the same regardless of its value. Zero (the
    default) disables the charge.

    Args:
        session: Database session for querying commission rules.

    Returns:
        Flat document fee in Nigerian Naira (defaults to 0.0).
    """
    from app.platform.models import PlatformCommission

    result = await session.execute(
        select(PlatformCommission.document_fee)
        .order_by(desc(PlatformCommission.created_at), desc(PlatformCommission.id))
        .limit(1)
    )
    val = result.scalar()
    return float(val) if val else 0.0


async def book_document_fee(
    session: AsyncSession, *, tenant_id: UUID, document_id: UUID, fee_amount: float | None = None
) -> dict | None:
    """Book the flat rate charged for a created document.

    Every document — quote, invoice, receipt, purchase order, draft or not —
    costs the tenant ``document_fee`` at creation. The row lands in the
    ledger as pending, so it joins the tenant's outstanding balance and is
    collected by the same settlement flow as sale commissions. Voiding the
    document later does not refund it: the document was issued.

    Args:
        session: Database session for the tenant's documents database.
        tenant_id: Unique identifier of the tenant business.
        document_id: The document the fee bills for.
        fee_amount: Precomputed flat fee; read from the commission rules
            when omitted.

    Returns:
        A dict with rule-independent fee details, or None when the flat fee
        is zero (document creation is not charged).
    """
    from app.platform.models import PlatformFeeLedger

    fee = fee_amount if fee_amount is not None else await get_document_fee(session)
    if fee <= 0:
        return None

    fee_result = {
        "rule_id": None,
        "label": "document_fee",
        "fee_type": "flat",
        "rate": fee,
        "sale_total": 0.0,
        "platform_fee": fee,
        "settlement_amount": 0.0,
        "fee_category": "document",
    }

    session.add(
        PlatformFeeLedger(
            tenant_id=UUID(str(tenant_id)),
            document_id=UUID(str(document_id)),
            amount=float(Decimal(str(fee)).quantize(Decimal("0.01"))),
            fee_type="flat",
            rate=float(Decimal(str(fee)).quantize(Decimal("0.01"))),
            payment_method="document",
            fee_category="document",
            status="pending",
        )
    )
    return fee_result
