"""Invoice generation and wallet auto-debit.

Invoices are generated per tenant per 30-day billing period with an immutable
snapshot of subscription amount, accumulated fees, credits, and adjustments.
Generation is idempotent via UNIQUE(tenant_id, billing_period_id).

Wallet invoices are auto-debited on the due date when the tenant has enough
balance and auto_debit is enabled; otherwise the invoice remains payable via
manual settlement.
"""

import json
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import flutterwave_service
from app.billing.models import (
    BillingFeeLedger,
    BillingInvoice,
    BillingPayment,
    BillingPeriod,
    MerchantSubscription,
    SubscriptionPlan,
    TenantWallet,
    WalletTransaction,
)
from app.billing.subscriptions import DUE_DAYS, GRACE_DAYS, get_tenant_subscription


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


async def generate_invoice_for_period(
    session: AsyncSession, period: BillingPeriod
) -> BillingInvoice | None:
    """Generate (once) the invoice for a closed billing period."""
    existing = await session.execute(
        select(BillingInvoice).where(
            BillingInvoice.tenant_id == period.tenant_id,
            BillingInvoice.billing_period_id == period.id,
        )
    )
    if existing.scalar_one_or_none():
        return None

    sub_res = await session.execute(
        select(MerchantSubscription).where(MerchantSubscription.tenant_id == period.tenant_id)
    )
    sub = sub_res.scalar_one_or_none()
    plan = None
    if sub:
        plan = await session.get(SubscriptionPlan, sub.plan_id)

    charges = await session.execute(
        select(func.coalesce(func.sum(BillingFeeLedger.amount), 0)).where(
            BillingFeeLedger.tenant_id == period.tenant_id,
            BillingFeeLedger.billing_period_id == period.id,
            BillingFeeLedger.entry_type.in_(("charge", "adjustment")),
        )
    )
    credits = await session.execute(
        select(func.coalesce(func.sum(BillingFeeLedger.amount), 0)).where(
            BillingFeeLedger.tenant_id == period.tenant_id,
            BillingFeeLedger.billing_period_id == period.id,
            BillingFeeLedger.entry_type.in_(("credit", "reversal")),
        )
    )

    subscription_amount = money(plan.monthly_price if plan else 0)
    fee_total = money(charges.scalar())
    credit_total = money(credits.scalar())
    total = money(max(Decimal(0), subscription_amount + fee_total - credit_total))

    issued_at = datetime.now(UTC)
    due_at = issued_at + timedelta(days=DUE_DAYS)
    grace_until = due_at + timedelta(days=GRACE_DAYS)
    invoice_number = (
        f"INV-{period.tenant_id.hex[:8].upper()}-"
        f"{issued_at.strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"
    )

    invoice = BillingInvoice(
        tenant_id=period.tenant_id,
        billing_period_id=period.id,
        invoice_number=invoice_number,
        subscription_amount=subscription_amount,
        transaction_fee_total=fee_total,
        credit_total=credit_total,
        total_amount=total,
        amount_paid=money(0),
        amount_due=total,
        status="issued",
        issued_at=issued_at,
        due_at=due_at,
        grace_until=grace_until,
    )
    session.add(invoice)
    period.status = "billed"
    period.invoice_generated = True
    await session.flush()
    return invoice


async def apply_wallet_payment(
    session: AsyncSession,
    *,
    invoice: BillingInvoice,
    wallet: TenantWallet,
) -> BillingPayment | None:
    """Debit the wallet for the invoice amount due. Returns None if insufficient."""
    if wallet.balance < invoice.amount_due or invoice.amount_due <= 0:
        return None

    amount = money(invoice.amount_due)
    wallet.balance = money(wallet.balance - amount)

    payment = BillingPayment(
        invoice_id=invoice.id,
        tenant_id=invoice.tenant_id,
        amount=amount,
        payment_method="wallet",
        reference=f"wallet-{invoice.invoice_number}",
        status="completed",
    )
    session.add(payment)
    session.add(
        WalletTransaction(
            tenant_id=invoice.tenant_id,
            wallet_id=wallet.id,
            entry_type="invoice_debit",
            amount=amount,
            reference=invoice.invoice_number,
            invoice_id=invoice.id,
            description=f"Auto-debit for invoice {invoice.invoice_number}",
        )
    )
    invoice.amount_paid = money(invoice.amount_paid + amount)
    invoice.amount_due = money(max(Decimal(0), invoice.amount_due - amount))
    if invoice.amount_due <= 0:
        invoice.status = "paid"
        invoice.paid_at = datetime.now(UTC)
    await session.flush()
    return payment


async def credit_wallet(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    amount: Decimal,
    entry_type: str = "funding",
    gateway_reference: str | None = None,
    invoice_id: UUID | None = None,
    description: str | None = None,
) -> WalletTransaction:
    """Add funds to a tenant's wallet, clearing accrued billing debt first.

    If the tenant has ``accrued_debt`` on their subscription, the incoming
    funds are applied to it before any remainder is added to the wallet
    balance. A ``payment`` ledger entry is recorded for the debt cleared.
    """
    amount = money(amount)
    wallet = await get_or_create_wallet(session, tenant_id)

    sub = await get_tenant_subscription(session, tenant_id)
    debt_cleared = money(0)
    if sub and sub.accrued_debt > 0:
        debt_cleared = min(amount, sub.accrued_debt)
        sub.accrued_debt = money(sub.accrued_debt - debt_cleared)
        amount = money(amount - debt_cleared)

    if debt_cleared > 0:
        session.add(
            BillingFeeLedger(
                tenant_id=tenant_id,
                entry_type="payment",
                amount=debt_cleared,
                currency=wallet.currency,
                description=f"Accrued debt cleared via wallet funding — {gateway_reference or 'manual'}",
            )
        )
        session.add(
            WalletTransaction(
                tenant_id=tenant_id,
                wallet_id=wallet.id,
                entry_type="debt_clearance",
                amount=debt_cleared,
                reference=gateway_reference,
                gateway_reference=gateway_reference,
                description="Cleared accrued billing debt",
            )
        )

    wallet.balance = money(wallet.balance + amount)
    entry = WalletTransaction(
        tenant_id=tenant_id,
        wallet_id=wallet.id,
        entry_type=entry_type,
        amount=money(amount),
        reference=gateway_reference,
        gateway_reference=gateway_reference,
        invoice_id=invoice_id,
        description=description,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_or_create_wallet(session: AsyncSession, tenant_id: UUID) -> TenantWallet:
    result = await session.execute(
        select(TenantWallet).where(TenantWallet.tenant_id == tenant_id)
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        wallet = TenantWallet(tenant_id=tenant_id, balance=0, currency="NGN")
        session.add(wallet)
        await session.flush()
    return wallet


async def debit_wallet(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    amount: Decimal,
    reference: str | None = None,
    description: str | None = None,
) -> Decimal:
    """Debit ``amount`` from the tenant wallet. Returns the amount actually debited (may be 0)."""
    wallet = await get_or_create_wallet(session, tenant_id)
    amount = money(amount)
    debit = min(amount, wallet.balance)
    if debit > 0:
        wallet.balance = money(wallet.balance - debit)
        session.add(
            WalletTransaction(
                tenant_id=tenant_id,
                wallet_id=wallet.id,
                entry_type="fee_debit",
                amount=debit,
                reference=reference,
                description=description or f"Transaction fee debit — {reference}",
            )
        )
    return debit


async def create_billing_settlement_link(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: str,
) -> dict:
    """Create a Flutterwave link to clear the tenant's accrued billing debt.

    Mirrors the legacy ``create_settlement_payment`` but targets
    ``merchant_subscriptions.accrued_debt`` instead of ``PlatformFeeLedger``
    pending rows. The Flutterwave webhook (intent type ``billing_settlement``)
    clears the debt on confirmation.
    """
    import re

    from app.common.bridge import _get_sdb
    from app.identity.repository import get_user_by_id as _get_user
    from app.payments.models import PaymentIntent
    from app.payments.repository import create_intent

    sub = await get_tenant_subscription(session, tenant_id)
    if not sub or sub.accrued_debt <= 0:
        raise ValueError("nothing_pending")

    total = money(sub.accrued_debt)

    sdb = _get_sdb("payments")
    async with sdb.session() as pay_session:
        open_intent = (
            await pay_session.execute(
                select(PaymentIntent).where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.status == "pending",
                    PaymentIntent.gateway_reference.like("SF-BILL-SETTLE-%"),
                ).order_by(PaymentIntent.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()

        if open_intent:
            base_ref = re.sub(r"-R\d+$", "", open_intent.gateway_reference)
            attempt = re.search(r"-R(\d+)$", open_intent.gateway_reference)
            n = int(attempt.group(1)) + 1 if attempt else 1
            tx_ref = f"{base_ref}-R{n}"
            regenerated = True
            try:
                metadata = json.loads(open_intent.intent_metadata or "{}")
            except (TypeError, ValueError):
                metadata = {}
            metadata["type"] = "billing_settlement"
            metadata["tenant_id"] = str(tenant_id)
            open_intent.amount = float(total)
            open_intent.gateway_reference = tx_ref
            open_intent.intent_metadata = json.dumps(metadata)
            intent = open_intent
        else:
            regenerated = False
            tx_ref = f"SF-BILL-SETTLE-{uuid4().hex[:10].upper()}"
            intent = PaymentIntent(
                id=uuid4(),
                tenant_id=tenant_id,
                sale_id=None,
                cart_id=None,
                method="card",
                amount=float(total),
                currency="NGN",
                status="pending",
                gateway_reference=tx_ref,
                intent_metadata=json.dumps({
                    "type": "billing_settlement",
                    "tenant_id": str(tenant_id),
                    "created_by": actor_id,
                }),
            )

    # Owner email for the Flutterwave customer receipt.
    owner_email = None
    async with _get_sdb("identity").session() as id_session:
        actor_user = await _get_user(id_session, UUID(actor_id))
        owner_email = actor_user.email if actor_user else None

    async with sdb.session() as pay_session:
        if regenerated:
            intent = await pay_session.merge(intent)
        else:
            await create_intent(pay_session, intent)
        await pay_session.commit()

    link_result = await flutterwave_service.create_payment_link(
        amount=total,
        tx_ref=tx_ref,
        customer_email=owner_email or "settlements@storeflow.ng",
        title="Clear Billing Debt",
        meta={"tenant_id": str(tenant_id), "intent_id": str(intent.id)},
    )

    async with sdb.session() as pay_session:
        row = (
            await pay_session.execute(
                select(PaymentIntent).where(PaymentIntent.id == intent.id)
            )
        ).scalar_one()
        row.authorization_url = link_result["link"]
        await pay_session.commit()

    return {
        "link": link_result["link"],
        "amount": float(total),
        "intent_id": str(intent.id),
        "tx_ref": tx_ref,
        "regenerated": regenerated,
    }


async def create_wallet_funding_link(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: str,
    amount: Decimal,
) -> dict:
    """Create a Flutterwave link to fund the tenant's wallet.

    The Flutterwave webhook (intent type ``wallet_funding``) credits the
    wallet on confirmation.
    """
    import re

    from app.common.bridge import _get_sdb
    from app.identity.repository import get_user_by_id as _get_user
    from app.payments.models import PaymentIntent
    from app.payments.repository import create_intent

    total = money(amount)
    if total <= 0:
        raise ValueError("amount_must_be_positive")

    sdb = _get_sdb("payments")
    async with sdb.session() as pay_session:
        open_intent = (
            await pay_session.execute(
                select(PaymentIntent).where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.status == "pending",
                    PaymentIntent.gateway_reference.like("SF-WALLET-FUND-%"),
                ).order_by(PaymentIntent.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()

        if open_intent:
            base_ref = re.sub(r"-R\d+$", "", open_intent.gateway_reference)
            attempt = re.search(r"-R(\d+)$", open_intent.gateway_reference)
            n = int(attempt.group(1)) + 1 if attempt else 1
            tx_ref = f"{base_ref}-R{n}"
            regenerated = True
            try:
                metadata = json.loads(open_intent.intent_metadata or "{}")
            except (TypeError, ValueError):
                metadata = {}
            metadata["type"] = "wallet_funding"
            metadata["tenant_id"] = str(tenant_id)
            open_intent.amount = float(total)
            open_intent.gateway_reference = tx_ref
            open_intent.intent_metadata = json.dumps(metadata)
            intent = open_intent
        else:
            regenerated = False
            tx_ref = f"SF-WALLET-FUND-{uuid4().hex[:10].upper()}"
            intent = PaymentIntent(
                id=uuid4(),
                tenant_id=tenant_id,
                sale_id=None,
                cart_id=None,
                method="card",
                amount=float(total),
                currency="NGN",
                status="pending",
                gateway_reference=tx_ref,
                intent_metadata=json.dumps({
                    "type": "wallet_funding",
                    "tenant_id": str(tenant_id),
                    "created_by": actor_id,
                }),
            )

    owner_email = None
    async with _get_sdb("identity").session() as id_session:
        actor_user = await _get_user(id_session, UUID(actor_id))
        owner_email = actor_user.email if actor_user else None

    async with sdb.session() as pay_session:
        if regenerated:
            intent = await pay_session.merge(intent)
        else:
            await create_intent(pay_session, intent)
        await pay_session.commit()

    link_result = await flutterwave_service.create_payment_link(
        amount=total,
        tx_ref=tx_ref,
        customer_email=owner_email or "settlements@storeflow.ng",
        title="Fund Wallet",
        meta={"tenant_id": str(tenant_id), "intent_id": str(intent.id)},
    )

    async with sdb.session() as pay_session:
        row = (
            await pay_session.execute(
                select(PaymentIntent).where(PaymentIntent.id == intent.id)
            )
        ).scalar_one()
        row.authorization_url = link_result["link"]
        await pay_session.commit()

    return {
        "link": link_result["link"],
        "amount": float(total),
        "intent_id": str(intent.id),
        "tx_ref": tx_ref,
        "regenerated": regenerated,
    }
