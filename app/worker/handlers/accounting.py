"""Accounting event handlers for the StoreFlow worker service.

This module contains event handlers that create journal entries when specific
domain events occur (e.g., sale confirmed, payment succeeded). These handlers
are registered in the worker's event routing system and process events
asynchronously via RabbitMQ.

Abbreviations Used in This Module
----------------------------------
- JE: Journal Entry -- a record of a financial transaction (debit/credit line).
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- UTC: Coordinated Universal Time -- the primary time standard.
- AR: Accounts Receivable -- money owed TO the business by customers.
- COGS: Cost of Goods Sold -- the direct cost of products sold.
- NGN: Nigerian Naira -- the base currency for all financial amounts.

Double-Entry Bookkeeping Rules Applied:
    - Sale Confirmed: Debit Cash (1000), Credit Revenue (4000)
    - Payment Succeeded: Debit AR (1100), Credit Revenue (4000)
"""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.events.envelope import EventEnvelope


async def handle_sale_confirmed(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Create journal entries when a sale is confirmed.

    When a sale is confirmed, two journal entries are created:
        1. Debit: Cash Account (1000) -- we received money
        2. Credit: Revenue Account (4000) -- we earned revenue

    This handler is triggered by the "sales.sale_confirmed" event.

    Event Payload:
        - sale_id (str): UUID of the confirmed sale
        - tenant_id (str): UUID of the business tenant
        - total (float): Total sale amount in NGN

    Args:
        envelope: The event envelope containing the sale confirmation payload.
        session: The async SQLAlchemy database session for persistence.
    """
    from datetime import UTC, datetime

    from app.accounting.models import Journal, JournalEntry
    from app.accounting.repository import get_account_by_code

    sale_id = envelope.payload.get("sale_id")
    tenant_id = envelope.payload.get("tenant_id")
    total = envelope.payload.get("total")
    if not sale_id or not tenant_id or not total:
        return

    # Look up the Cash and Revenue accounts for this tenant
    cash_account = await get_account_by_code(session, UUID(tenant_id), "1000")
    revenue_account = await get_account_by_code(session, UUID(tenant_id), "4000")
    if not cash_account or not revenue_account:
        return

    # Create the journal header
    journal_id = uuid4()
    journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"
    journal = Journal(
        id=journal_id,
        tenant_id=UUID(tenant_id),
        journal_number=journal_number,
        description=f"Sale {sale_id}",
        reference_id=UUID(sale_id),
        reference_type="sale",
        status="posted",
        posted_at=datetime.now(UTC),
    )

    # Debit entry: Cash (we received money)
    debit_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=UUID(tenant_id),
        account_id=cash_account.id,
        account_code=cash_account.code,
        debit=float(total),
        credit=0,
        description=f"Sale {sale_id} - cash",
        type="asset",
        status="posted",
        posted_at=datetime.now(UTC),
        amount=float(total),
    )

    # Credit entry: Revenue (we earned revenue)
    credit_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=UUID(tenant_id),
        account_id=revenue_account.id,
        account_code=revenue_account.code,
        debit=0,
        credit=float(total),
        description=f"Sale {sale_id} - revenue",
        type="revenue",
        status="posted",
        posted_at=datetime.now(UTC),
        amount=float(total),
    )

    session.add(journal)
    session.add(debit_entry)
    session.add(credit_entry)
    await session.flush()


async def handle_payment_succeeded(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Create journal entries when a payment succeeds (card/transfer).

    When a card or transfer payment succeeds, two journal entries are created:
        1. Debit: Accounts Receivable (1100) -- customer now owes us
        2. Credit: Revenue Account (4000) -- we earned revenue

    This handler is triggered by the "payment.succeeded" event.

    Event Payload:
        - payment_id (str): UUID of the successful payment
        - tenant_id (str): UUID of the business tenant
        - amount (float): Payment amount in NGN

    Args:
        envelope: The event envelope containing the payment success payload.
        session: The async SQLAlchemy database session for persistence.
    """
    from datetime import UTC, datetime

    from app.accounting.models import Journal, JournalEntry
    from app.accounting.repository import get_account_by_code

    payment_id = envelope.payload.get("payment_id")
    tenant_id = envelope.payload.get("tenant_id")
    amount = envelope.payload.get("amount")
    if not payment_id or not tenant_id or not amount:
        return

    # Look up the Accounts Receivable and Revenue accounts for this tenant
    receivable_account = await get_account_by_code(session, UUID(tenant_id), "1100")
    revenue_account = await get_account_by_code(session, UUID(tenant_id), "4000")
    if not receivable_account or not revenue_account:
        return

    # Create the journal header
    journal_id = uuid4()
    journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"
    journal = Journal(
        id=journal_id,
        tenant_id=UUID(tenant_id),
        journal_number=journal_number,
        description=f"Payment {payment_id}",
        reference_id=UUID(payment_id),
        reference_type="payment",
        status="posted",
        posted_at=datetime.now(UTC),
    )

    # Debit entry: Accounts Receivable (customer owes us)
    debit_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=UUID(tenant_id),
        account_id=receivable_account.id,
        account_code=receivable_account.code,
        debit=float(amount),
        credit=0,
        description=f"Payment {payment_id} - receivable",
        type="asset",
        status="posted",
        posted_at=datetime.now(UTC),
        amount=float(amount),
    )

    # Credit entry: Revenue (we earned revenue)
    credit_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=UUID(tenant_id),
        account_id=revenue_account.id,
        account_code=revenue_account.code,
        debit=0,
        credit=float(amount),
        description=f"Payment {payment_id} - revenue",
        type="revenue",
        status="posted",
        posted_at=datetime.now(UTC),
        amount=float(amount),
    )

    session.add(journal)
    session.add(debit_entry)
    session.add(credit_entry)
    await session.flush()
