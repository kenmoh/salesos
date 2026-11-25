"""Fee calculation service.

Fees are fixed NGN amounts determined by pre-tax, post-discount sale value,
selected from the active pricing version's bands for the tenant's plan.
A snapshot of plan/version/band/fee is stored on the billable transaction so
historical fees never change when pricing is edited.

All monetary math uses Decimal. Band ranges are validated to be contiguous
and non-overlapping when pricing versions are created.
"""

from datetime import datetime, UTC
from decimal import Decimal
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.invoices import debit_wallet, get_or_create_wallet
from app.billing.models import (
    BillableTransaction,
    BillingFeeLedger,
    BillingPeriod,
    MerchantSubscription,
    PricingVersion,
    SubscriptionPlan,
    TransactionFeeTier,
)
from app.billing.subscription_notifications import notify_debt_cap_reached
from app.billing.subscriptions import get_tenant_subscription


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


async def active_pricing_version(session: AsyncSession, at: datetime | None = None) -> PricingVersion | None:
    """Return the pricing version applicable at the given time."""
    at = at or datetime.now(UTC)
    result = await session.execute(
        select(PricingVersion)
        .where(PricingVersion.status == "active")
        .where(PricingVersion.effective_from <= at)
        .where((PricingVersion.effective_until.is_(None)) | (PricingVersion.effective_until > at))
        .order_by(PricingVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def resolve_fee(
    session: AsyncSession,
    tenant_id: UUID,
    fee_base: Decimal,
    completed_at: datetime | None = None,
) -> dict | None:
    """Resolve the applicable fee for a sale without writing anything.

    Returns a dict with plan/version/tier snapshots, or None if billing is not
    configured for the tenant.
    """
    sub = await get_tenant_subscription(session, tenant_id)
    if not sub:
        return None

    version = await active_pricing_version(session, completed_at)
    if not version:
        return None

    result = await session.execute(
        select(TransactionFeeTier)
        .where(TransactionFeeTier.pricing_version_id == version.id)
        .where(TransactionFeeTier.plan_id == sub.plan_id)
        .where(TransactionFeeTier.is_active.is_(True))
        .where(TransactionFeeTier.minimum_amount <= fee_base)
        .where(
            (TransactionFeeTier.maximum_amount.is_(None))
            | (fee_base <= TransactionFeeTier.maximum_amount)
        )
        .order_by(TransactionFeeTier.minimum_amount.desc())
        .limit(1)
    )
    tier = result.scalar_one_or_none()
    if not tier:
        return None

    plan = await session.get(SubscriptionPlan, sub.plan_id)
    return {
        "subscription": sub,
        "version": version,
        "tier": tier,
        "plan": plan,
        "fee": money(tier.fee_amount),
        "fee_label": tier.label,
        "fee_base": money(fee_base),
    }


async def current_period(
    session: AsyncSession, tenant_id: UUID, at: datetime | None = None
) -> BillingPeriod | None:
    """Return the open billing period covering the given moment, if any."""
    at = at or datetime.now(UTC)
    result = await session.execute(
        select(BillingPeriod)
        .where(BillingPeriod.tenant_id == tenant_id)
        .where(BillingPeriod.period_start <= at)
        .where(BillingPeriod.period_end > at)
        .order_by(BillingPeriod.period_start.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def check_fee_affordability(
    session: AsyncSession,
    tenant_id: UUID,
    fee: Decimal,
) -> tuple[bool, str | None]:
    """Check whether a transaction fee can be settled now.

    Returns ``(ok, reason)``. When ``ok`` is False the sale must be blocked
    (402) because the wallet cannot cover the fee and accrued debt would
    exceed the tenant's debt cap.

    Resolution order:
    1. Wallet balance covers the full fee → immediate debit at confirmation.
    2. Wallet covers part; the remainder accrues as debt within the cap.
    3. Neither → blocked.
    """
    fee = money(fee)
    sub = await get_tenant_subscription(session, tenant_id)
    if not sub:
        # No billing configured → no fee to collect.
        return True, None

    wallet = await get_or_create_wallet(session, tenant_id)
    debit = min(fee, wallet.balance)
    remainder = money(fee - debit)

    if remainder == 0:
        return True, None
    if sub.accrued_debt + remainder <= sub.debt_cap:
        return True, None
    return False, "fee_balance_exceeded"


async def assert_billing_headroom(
    session: AsyncSession, tenant_id: UUID
) -> None:
    """Refuse the action when the tenant's accrued billing debt has reached
    the cap. Called when opening a cart, checking out, or creating a document.

    Raises ValueError("fee_balance_exceeded"), which the routes surface as 402.
    """
    sub = await get_tenant_subscription(session, tenant_id)
    if not sub or not isinstance(sub, MerchantSubscription):
        return
    if sub.accrued_debt >= sub.debt_cap:
        raise ValueError("fee_balance_exceeded")


async def _settle_fee(
    session: AsyncSession,
    *,
    sub: MerchantSubscription,
    fee: Decimal,
    reference: str | None,
    description: str,
    actor_id: UUID | None,
    entry_type_label: str,
    billing_period_id: UUID | None,
    sale_id: UUID | None = None,
    billable_transaction_id: UUID | None = None,
) -> None:
    """Shared wallet-deduction + debt-accrual settlement for any billable fee.

    1. Debit the tenant wallet for as much of the fee as it can cover.
    2. Accrue any remainder as debt, up to ``debt_cap``.
    3. If the remainder would push debt over the cap, raise ``ValueError``
       so the caller can refuse the action (402).

    Appends charge/payment/accrual entries to ``BillingFeeLedger`` and
    mutates ``sub.accrued_debt`` in place.
    """
    fee = money(fee)

    # Immediate wallet deduction.
    debited = await debit_wallet(
        session,
        tenant_id=sub.tenant_id,
        amount=fee,
        reference=reference,
        description=description,
    )
    remainder = money(fee - debited)

    # Accrue any remainder as debt, capped by debt_cap.
    if sub.accrued_debt + remainder > sub.debt_cap:
        raise ValueError("fee_balance_exceeded")

    previous_debt = sub.accrued_debt
    sub.accrued_debt = money(sub.accrued_debt + remainder)

    # Ledger: charge for the full fee.
    session.add(
        BillingFeeLedger(
            tenant_id=sub.tenant_id,
            sale_id=sale_id,
            billable_transaction_id=billable_transaction_id,
            entry_type=f"{entry_type_label}_charge",
            amount=fee,
            currency="NGN",
            description=description,
            actor_id=actor_id,
            billing_period_id=billing_period_id,
        )
    )

    # Ledger: payment for the wallet-debited portion.
    if debited > 0:
        session.add(
            BillingFeeLedger(
                tenant_id=sub.tenant_id,
                sale_id=sale_id,
                billable_transaction_id=billable_transaction_id,
                entry_type=f"{entry_type_label}_payment",
                amount=debited,
                currency="NGN",
                description=f"{description} — wallet debit",
                actor_id=actor_id,
                billing_period_id=billing_period_id,
            )
        )

    # Ledger: accrual for the debt portion.
    if remainder > 0:
        session.add(
            BillingFeeLedger(
                tenant_id=sub.tenant_id,
                sale_id=sale_id,
                billable_transaction_id=billable_transaction_id,
                entry_type=f"{entry_type_label}_accrual",
                amount=remainder,
                currency="NGN",
                description=f"{description} — accrued debt",
                actor_id=actor_id,
                billing_period_id=billing_period_id,
            )
        )

    if previous_debt < sub.debt_cap <= sub.accrued_debt:
        event_reference = str(billable_transaction_id or reference or "unknown")
        await notify_debt_cap_reached(
            session, sub, event_reference=event_reference
        )

    await session.flush()


async def charge_document_fee(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    doc_type: str,
    new_status: str,
    old_status: str,
    document_total: Decimal,
    actor_id: UUID | None = None,
) -> bool:
    """Charge a plan-based transaction fee when a document leaves draft status.

    Uses the same plan/version/tier fee bands as sales — the fee is resolved
    from the document's total via ``resolve_fee`` (pre-tax, post-discount
    equivalent for documents = total), so Free/Basic/Pro each pay their own
    tiered fee.

    Settlement follows the wallet-deduction + debt-accrual pattern:

    1. Debit the tenant wallet for as much of the fee as it can cover.
    2. Accrue any remainder as debt, up to ``debt_cap``.
    3. If the remainder would push debt over the cap, raise ``ValueError``
       so the caller can refuse the status change (402).

    Idempotent: returns ``False`` if a document_charge ledger entry already
    exists for this document.

    Returns ``True`` when a fee was charged, ``False`` when no fee applies
    (not leaving draft, no billing configured, fee is zero, or already charged).
    """
    # Only charge when leaving draft — every document pays once.
    if old_status != "draft" or new_status == "draft":
        return False

    sub = await get_tenant_subscription(session, tenant_id)
    if not sub or not isinstance(sub, MerchantSubscription):
        return False

    # Idempotency: one document_charge per document.
    existing = await session.execute(
        select(BillingFeeLedger).where(
            BillingFeeLedger.tenant_id == tenant_id,
            BillingFeeLedger.entry_type == "document_charge",
            BillingFeeLedger.description.ilike(f"%document:{document_id}%"),
        )
    )
    if existing.scalar_one_or_none():
        return False

    # Resolve the plan-based fee from the document total, same as sales.
    resolution = await resolve_fee(session, tenant_id, money(document_total))
    if not resolution:
        return False

    fee = resolution["fee"]
    if fee <= 0:
        return False

    period = await current_period(session, tenant_id)

    await _settle_fee(
        session,
        sub=sub,
        fee=fee,
        reference=str(document_id),
        description=f"document:{document_id} — {doc_type} fee ({new_status})",
        actor_id=actor_id,
        entry_type_label="document",
        billing_period_id=(period.id if period else None),
    )
    return True


async def register_billable_sale(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    sale_id: UUID,
    branch_id: UUID | None,
    fee_base: Decimal,
    completed_at: datetime,
    actor_id: UUID | None = None,
) -> BillableTransaction | None:
    """Register one billable transaction and settle its fee immediately.

    Settlement order:
    1. Debit the tenant wallet for as much of the fee as it can cover.
    2. Accrue any remainder as debt, up to ``debt_cap``.
    3. If the remainder would push debt over the cap, raise ``ValueError``
       so the caller can refuse the sale (402).

    The UNIQUE(tenant_id, sale_id) constraint guarantees exactly one fee per
    sale, even under concurrent duplicate sale events. A duplicate call
    returns the existing row without creating a second ledger entry.
    """
    existing = await session.execute(
        select(BillableTransaction).where(
            BillableTransaction.tenant_id == tenant_id,
            BillableTransaction.sale_id == sale_id,
        )
    )
    if existing.scalar_one_or_none():
        return None

    resolution = await resolve_fee(session, tenant_id, fee_base, completed_at)
    if not resolution:
        return None

    fee = money(resolution["fee"])
    sub = resolution["subscription"]
    if not sub:
        return None

    bt = BillableTransaction(
        tenant_id=tenant_id,
        branch_id=branch_id,
        sale_id=sale_id,
        subscription_id=resolution["subscription"].id,
        plan_id=resolution["plan"].id if resolution["plan"] else None,
        pricing_version_id=resolution["version"].id,
        fee_tier_id=resolution["tier"].id,
        transaction_amount=money(fee_base),
        transaction_fee=fee,
        fee_label_snapshot=resolution["fee_label"],
        currency=resolution["tier"].currency,
        transaction_status="billable",
        completed_at=completed_at,
    )
    session.add(bt)
    await session.flush()

    period = await current_period(session, tenant_id, completed_at)

    await _settle_fee(
        session,
        sub=sub,
        fee=fee,
        reference=str(sale_id),
        description=f"Transaction fee — {resolution['fee_label']}",
        actor_id=actor_id,
        entry_type_label="sale",
        billing_period_id=(period.id if period else None),
        sale_id=sale_id,
        billable_transaction_id=bt.id,
    )
    return bt


async def reverse_sale_fee(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    sale_id: UUID,
    actor_id: UUID | None = None,
    reason: str = "Sale reversed",
    retained_amount: Decimal | None = None,
) -> BillingFeeLedger | None:
    """Append a credit/reversal entry for a voided or refunded sale.

    For a full reversal, credits the full original fee. For a partial refund,
    recalculates the fee on the retained value and credits only the difference.
    Original charge rows are never modified.
    """
    result = await session.execute(
        select(BillableTransaction).where(
            BillableTransaction.tenant_id == tenant_id,
            BillableTransaction.sale_id == sale_id,
        )
    )
    bt = result.scalar_one_or_none()
    if not bt:
        return None

    original_fee = money(bt.transaction_fee)
    if retained_amount is not None and retained_amount > 0:
        resolution = await resolve_fee(session, tenant_id, money(retained_amount), bt.completed_at)
        revised = resolution["fee"] if resolution else money(0)
        credit_amount = money(max(Decimal(0), original_fee - revised))
    else:
        credit_amount = original_fee

    if credit_amount <= 0:
        return None

    # Idempotency: one reversal per billable transaction.
    existing = await session.execute(
        select(BillingFeeLedger).where(
            BillingFeeLedger.billable_transaction_id == bt.id,
            BillingFeeLedger.entry_type == "reversal",
        )
    )
    if existing.scalar_one_or_none():
        return None

    period = await current_period(session, tenant_id)

    entry = BillingFeeLedger(
        tenant_id=tenant_id,
        sale_id=sale_id,
        billable_transaction_id=bt.id,
        billing_period_id=(period.id if period else None),
        entry_type="reversal",
        amount=credit_amount,
        currency=bt.currency,
        description=f"{reason} — fee reversal",
        actor_id=actor_id,
    )
    session.add(entry)
    bt.transaction_status = "reversed"
    await session.flush()
    return entry


def validate_bands(bands: list[dict]) -> tuple[bool, str]:
    """Validate a complete set of fee bands for one plan/version.

    Rules: non-negative amounts, no overlaps, no gaps, last band unlimited.
    Bands are (minimum_amount, maximum_amount) with None = unlimited.
    """
    if not bands:
        return False, "No fee bands provided"
    sorted_bands = sorted(bands, key=lambda b: Decimal(str(b["minimum_amount"])))
    if money(sorted_bands[0]["minimum_amount"]) != money(0):
        return False, "First band must start at 0"
    expected = money(0)
    for idx, band in enumerate(sorted_bands):
        minimum = money(band["minimum_amount"])
        maximum = band["maximum_amount"]
        if minimum != expected:
            return False, f"Gap or overlap at band starting {minimum}"
        if maximum is None:
            if idx != len(sorted_bands) - 1:
                return False, "Only the final band may be unlimited"
        else:
            maximum = money(maximum)
            if maximum <= minimum:
                return False, f"Invalid range {minimum}-{maximum}"
            expected = maximum + Decimal("0.01")
    return True, "OK"
