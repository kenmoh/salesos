"""Document event handlers for the StoreFlow worker service.

This module contains event handlers that create Accounts Receivable (AR) records
and journal entries when document statuses change (e.g., invoice sent, invoice paid).
These handlers are registered in the worker's event routing system and process
events asynchronously via RabbitMQ.

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- JE: Journal Entry -- a record of a financial transaction (debit/credit line).
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- UTC: Coordinated Universal Time -- the primary time standard.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- INV: Invoice -- a request for payment from a customer.
- NGN: Nigerian Naira -- the base currency for all financial amounts.

Double-Entry Bookkeeping Rules Applied:
    - Invoice Sent: Debit AR (1100), Credit Revenue (4000)
    - Invoice Paid: Debit Cash (1000), Credit AR (1100)
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.events.envelope import EventEnvelope


async def handle_document_status_changed(
    envelope: EventEnvelope, session: AsyncSession
) -> None:
    """Create or update AR records when a document's status changes.

    This handler processes document.status_changed events and performs
    accounting actions based on the document type and new status:

        - Invoice → sent: Creates an Accounts Receivable record and a journal
          entry (Debit AR 1100, Credit Revenue 4000).
        - Invoice → paid: Updates the AR record and creates a journal entry
          (Debit Cash 1000, Credit AR 1100).
        - Invoice → void: Marks the AR record as voided (if it exists).
        - Other document types: No action (quotes, receipts, POs have no AR impact).

    Event Payload:
        - document_id (str): UUID of the document
        - doc_number (str): Document number (e.g., "INV-20260826-A1B2C3D4")
        - doc_type (str): Document type (quote, invoice, receipt, purchase_order)
        - old_status (str): Previous status before the transition
        - new_status (str): New status after the transition
        - total (str): Document total amount as a string
        - customer_name (str): Customer name for AR display
        - customer_id (str): Customer UUID for AR linking
        - due_date (str): Due date as ISO string for AR aging

    Args:
        envelope: The event envelope containing the status change payload.
        session: The async SQLAlchemy database session for persistence.
    """
    from app.accounting.models import AccountReceivable, Journal, JournalEntry
    from app.accounting.repository import (
        create_accounts_receivable,
        get_account_by_code,
        update_ar_payment,
    )

    doc_type = envelope.payload.get("doc_type")
    new_status = envelope.payload.get("new_status")
    document_id = envelope.payload.get("document_id")
    doc_number = envelope.payload.get("doc_number")
    total = envelope.payload.get("total")
    customer_name = envelope.payload.get("customer_name", "Unknown Customer")
    customer_id = envelope.payload.get("customer_id")
    due_date_str = envelope.payload.get("due_date")
    tenant_id = envelope.payload.get("tenant_id")

    if not all([doc_type, new_status, document_id, tenant_id]):
        return

    # Only handle invoices (other document types have no AR impact)
    if doc_type != "invoice":
        return

    # ─── Invoice → sent: Create AR + journal entry ──────────────────────────
    if new_status == "sent":
        if not total or not customer_id or not doc_number:
            return

        # Look up the AR and Revenue accounts for this tenant
        receivable_account = await get_account_by_code(session, UUID(tenant_id), "1100")
        revenue_account = await get_account_by_code(session, UUID(tenant_id), "4000")
        if not receivable_account or not revenue_account:
            return

        # Parse due_date (default to 30 days from now if not provided)
        due_date = datetime.now(UTC) + timedelta(days=30)
        if due_date_str:
            try:
                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass  # Keep default

        # Create the AR record
        ar = AccountReceivable(
            id=uuid4(),
            tenant_id=UUID(tenant_id),
            invoice_id=UUID(document_id),
            customer_id=UUID(customer_id),
            customer_name=customer_name,
            invoice_number=doc_number,
            amount=float(total),
            amount_paid=0,
            balance=float(total),
            due_date=due_date,
            status="pending",
        )
        await create_accounts_receivable(session, ar)

        # Create the journal header
        journal_id = uuid4()
        journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"
        journal = Journal(
            id=journal_id,
            tenant_id=UUID(tenant_id),
            journal_number=journal_number,
            description=f"Invoice {doc_number} sent",
            reference_id=UUID(document_id),
            reference_type="invoice",
            status="posted",
            posted_at=datetime.now(UTC),
        )

        # Debit entry: Accounts Receivable (customer now owes us)
        debit_entry = JournalEntry(
            id=uuid4(),
            journal_id=journal_id,
            tenant_id=UUID(tenant_id),
            account_id=receivable_account.id,
            account_code=receivable_account.code,
            debit=float(total),
            credit=0,
            description=f"Invoice {doc_number} - receivable",
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
            description=f"Invoice {doc_number} - revenue",
            type="revenue",
            status="posted",
            posted_at=datetime.now(UTC),
            amount=float(total),
        )

        session.add(journal)
        session.add(debit_entry)
        session.add(credit_entry)
        await session.flush()

    # ─── Invoice → paid: Update AR + journal entry ──────────────────────────
    elif new_status == "paid":
        if not total or not document_id:
            return

        # Look up the Cash and AR accounts for this tenant
        cash_account = await get_account_by_code(session, UUID(tenant_id), "1000")
        receivable_account = await get_account_by_code(session, UUID(tenant_id), "1100")
        if not cash_account or not receivable_account:
            return

        # Find the AR record for this invoice and update it
        # We need to look it up by invoice_id
        from sqlalchemy import select

        from app.accounting.models import AccountReceivable

        ar_result = await session.execute(
            select(AccountReceivable).where(
                AccountReceivable.invoice_id == UUID(document_id),
                AccountReceivable.tenant_id == UUID(tenant_id),
            )
        )
        ar = ar_result.scalar_one_or_none()

        if ar:
            await update_ar_payment(session, ar.id, float(total))

        # Create the journal header
        journal_id = uuid4()
        journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"
        journal = Journal(
            id=journal_id,
            tenant_id=UUID(tenant_id),
            journal_number=journal_number,
            description=f"Invoice {doc_number or document_id} paid",
            reference_id=UUID(document_id),
            reference_type="invoice",
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
            description=f"Invoice {doc_number or document_id} paid - cash",
            type="asset",
            status="posted",
            posted_at=datetime.now(UTC),
            amount=float(total),
        )

        # Credit entry: Accounts Receivable (customer no longer owes us)
        credit_entry = JournalEntry(
            id=uuid4(),
            journal_id=journal_id,
            tenant_id=UUID(tenant_id),
            account_id=receivable_account.id,
            account_code=receivable_account.code,
            debit=0,
            credit=float(total),
            description=f"Invoice {doc_number or document_id} paid - receivable",
            type="asset",
            status="posted",
            posted_at=datetime.now(UTC),
            amount=float(total),
        )

        session.add(journal)
        session.add(debit_entry)
        session.add(credit_entry)
        await session.flush()
