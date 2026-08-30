"""Service layer for accounting domain business logic (planning functions).

This module contains pure planning functions that validate business rules and
create model instances without persisting them. The actual persistence is handled
by the repository layer. This separation keeps business logic testable and
independent of database concerns.

Planning Function Pattern:
    1. Accept a Command (Pydantic schema) as input
    2. Validate business rules (e.g., debits == credits)
    3. Create model instances (in-memory, not persisted)
    4. Return a tuple of (Result, Model, [optional models], [OutboxWrite events])

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- AP: Accounts Payable -- money the business OWES to vendors/suppliers.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- JE: Journal Entry -- a record of a financial transaction (debit/credit line).
- FK: Foreign Key -- a reference from one table to another.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- OutboxWrite: A record to be written to the transactional outbox table for
    reliable event publishing (ensures events are published exactly once).
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.common.events.outbox import OutboxWrite

from .events import journal_posted_event
from .models import (
    AccountPayable,
    AccountReceivable,
    ChartOfAccount,
    CommissionLedger,
    Expense,
    Journal,
    JournalEntry,
)
from .schemas import (
    AccountsPayableCreateCommand,
    AccountsPayableResult,
    AccountsReceivableCreateCommand,
    AccountsReceivableResult,
    ChartOfAccountCreateCommand,
    ChartOfAccountResult,
    CommissionRecordCommand,
    CommissionResult,
    ExpenseCreateCommand,
    ExpenseResult,
    JournalCreateCommand,
    JournalEntryLine,
    JournalPostCommand,
    JournalResult,
    PaymentRecordCommand,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS (COA) PLANNING
# ═══════════════════════════════════════════════════════════════════════════════


def plan_create_account(
    command: ChartOfAccountCreateCommand,
) -> tuple[ChartOfAccountResult, ChartOfAccount]:
    """Plan the creation of a new account in the Chart of Accounts.

    This function creates an in-memory ChartOfAccount model based on the
    provided command. It does NOT persist to the database -- that is handled
    by the repository layer after this function returns.

    Args:
        command: The ChartOfAccountCreateCommand containing account details.
            Must include: tenant_id, code, name, account_type.
            Optional: parent_id (for hierarchical accounts).

    Returns:
        A tuple of:
            - ChartOfAccountResult: Pydantic result schema for API responses.
            - ChartOfAccount: SQLAlchemy model instance for database persistence.

    Example:
        >>> command = ChartOfAccountCreateCommand(
        ...     tenant_id=uuid4(),
        ...     code="1000",
        ...     name="Cash",
        ...     account_type="asset",
        ... )
        >>> result, model = plan_create_account(command)
        >>> print(result.code)
        1000
    """
    account = ChartOfAccount(
        id=uuid4(),
        tenant_id=command.tenant_id,
        code=command.code,
        name=command.name,
        account_type=command.account_type,
        parent_id=command.parent_id,
        status="active",
    )
    result = ChartOfAccountResult(
        id=account.id,
        tenant_id=account.tenant_id,
        code=account.code,
        name=account.name,
        account_type=account.account_type,
        status=account.status,
    )
    return result, account


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL PLANNING
# ═══════════════════════════════════════════════════════════════════════════════


def plan_create_journal(
    command: JournalCreateCommand,
    lines: list[JournalEntryLine],
) -> tuple[JournalResult, Journal, list[JournalEntry], list[OutboxWrite]]:
    """Plan the creation of a journal with balanced debit/credit entries.

    This function validates that the journal entries balance (total debits == total
    credits) and creates in-memory Journal and JournalEntry models. The journal
    starts in "draft" status and can be posted later via plan_post_journal().

    Business Rules:
        1. Total debits MUST equal total credits (double-entry bookkeeping).
        2. Each entry must have either a debit or credit amount (not both).
        3. Journal number is auto-generated: JRN-YYYYMMDD-XXXXXXXX.

    Args:
        command: The JournalCreateCommand containing journal header details.
            Must include: tenant_id, description.
            Optional: reference_id, reference_type, actor_id, correlation_id.
        lines: A list of JournalEntryLine objects representing debit/credit lines.
            Must contain at least 2 lines that balance.

    Returns:
        A tuple of:
            - JournalResult: Pydantic result schema for API responses.
            - Journal: SQLAlchemy model instance for the journal header.
            - list[JournalEntry]: List of journal entry line items.
            - list[OutboxWrite]: Empty list (events emitted on post, not create).

    Raises:
        ValueError: If debits do not equal credits.

    Example:
        >>> command = JournalCreateCommand(
        ...     tenant_id=uuid4(),
        ...     description="Sale INV-20260001",
        ... )
        >>> lines = [
        ...     JournalEntryLine(account_id=cash_id, account_code="1000", debit=50000),
        ...     JournalEntryLine(account_id=revenue_id, account_code="4000", credit=50000),
        ... ]
        >>> result, journal, entries, events = plan_create_journal(command, lines)
    """
    journal_id = uuid4()
    journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"

    journal = Journal(
        id=journal_id,
        tenant_id=command.tenant_id,
        journal_number=journal_number,
        description=command.description,
        reference_id=command.reference_id,
        reference_type=command.reference_type,
        status="draft",
    )

    entries = []
    for line in lines:
        entry = JournalEntry(
            id=uuid4(),
            journal_id=journal_id,
            tenant_id=command.tenant_id,
            account_id=line.account_id,
            account_code=line.account_code,
            debit=line.debit,
            credit=line.credit,
            description=line.description,
            type=_determine_entry_type(line.account_code),
            status="draft",
            amount=line.debit - line.credit if line.debit > 0 else line.credit - line.debit,
        )
        entries.append(entry)

    # Validate that debits == credits (double-entry bookkeeping rule)
    total_debit = sum(e.debit for e in entries)
    total_credit = sum(e.credit for e in entries)
    if abs(total_debit - total_credit) > 0.001:
        raise ValueError(
            f"Journal entries must balance: debits ({total_debit}) != credits ({total_credit})"
        )

    result = JournalResult(
        id=journal.id,
        tenant_id=journal.tenant_id,
        journal_number=journal.journal_number,
        description=journal.description,
        status=journal.status,
        reference_id=str(journal.reference_id) if journal.reference_id else None,
        reference_type=journal.reference_type,
    )
    return result, journal, entries, []


def plan_post_journal(
    command: JournalPostCommand,
    journal_number: str,
    tenant_id: str,
    reference_type: str | None,
    reference_id: str | None,
) -> tuple[list[OutboxWrite], Journal]:
    """Plan the posting (finalization) of a journal.

    Posting a journal transitions it from "draft" to "posted" status, making
    it immutable. This function also creates an outbox event to notify other
    services that the journal has been posted.

    Args:
        command: The JournalPostCommand containing the journal_id and actor_id.
        journal_number: The journal number (e.g., "JRN-20260826-A1B2C3D4").
        tenant_id: The business tenant ID as a string.
        reference_type: The type of the referenced entity (e.g., "sale").
        reference_id: The UUID of the referenced entity as a string.

    Returns:
        A tuple of:
            - list[OutboxWrite]: List of outbox events to publish.
            - Journal: Updated Journal model with status="posted".
    """
    journal_update = Journal(
        id=command.journal_id,
        tenant_id=tenant_id,
        journal_number=journal_number,
        description="",
        status="posted",
        posted_by=command.actor_id,
        posted_at=datetime.now(UTC),
    )

    event = journal_posted_event(
        tenant_id=tenant_id,
        journal_id=command.journal_id,
        journal_number=journal_number,
        reference_type=reference_type,
        reference_id=reference_id,
        actor_id=command.actor_id,
        correlation_id=command.correlation_id,
    )
    outbox = [
        OutboxWrite(event=event, aggregate_type="journal", aggregate_id=str(command.journal_id))
    ]
    return outbox, journal_update


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMISSION PLANNING
# ═══════════════════════════════════════════════════════════════════════════════


def plan_record_commission(
    command: CommissionRecordCommand,
) -> tuple[CommissionResult, CommissionLedger]:
    """Plan the recording of a sales commission for an employee/cashier.

    Args:
        command: The CommissionRecordCommand containing commission details.
            Must include: tenant_id, sale_id, user_id, amount, rate_pct.

    Returns:
        A tuple of:
            - CommissionResult: Pydantic result schema for API responses.
            - CommissionLedger: SQLAlchemy model instance for database persistence.
    """
    commission = CommissionLedger(
        id=uuid4(),
        tenant_id=command.tenant_id,
        sale_id=command.sale_id,
        user_id=command.user_id,
        amount=command.amount,
        rate_pct=command.rate_pct,
        status="pending",
    )
    result = CommissionResult(
        id=commission.id,
        tenant_id=commission.tenant_id,
        sale_id=commission.sale_id,
        user_id=commission.user_id,
        amount=commission.amount,
        rate_pct=commission.rate_pct,
        status=commission.status,
    )
    return result, commission


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS RECEIVABLE (AR) PLANNING
# ═══════════════════════════════════════════════════════════════════════════════


def plan_create_accounts_receivable(
    command: AccountsReceivableCreateCommand,
) -> tuple[AccountsReceivableResult, AccountReceivable]:
    """Plan the creation of an Accounts Receivable record.

    AR is created when an invoice is issued to a customer or a sale is made
    on credit. The initial balance equals the full invoice amount.

    Args:
        command: The AccountsReceivableCreateCommand containing AR details.
            Must include: tenant_id, customer_id, customer_name, invoice_number,
            amount, due_date.
            Optional: invoice_id.

    Returns:
        A tuple of:
            - AccountsReceivableResult: Pydantic result schema for API responses.
            - AccountReceivable: SQLAlchemy model instance for database persistence.
    """
    ar = AccountReceivable(
        id=uuid4(),
        tenant_id=command.tenant_id,
        invoice_id=command.invoice_id,
        customer_id=command.customer_id,
        customer_name=command.customer_name,
        invoice_number=command.invoice_number,
        amount=command.amount,
        amount_paid=0,
        balance=command.amount,
        due_date=command.due_date,
        status="pending",
    )
    result = AccountsReceivableResult(
        id=ar.id,
        tenant_id=ar.tenant_id,
        customer_id=ar.customer_id,
        customer_name=ar.customer_name,
        invoice_number=ar.invoice_number,
        amount=ar.amount,
        amount_paid=ar.amount_paid,
        balance=ar.balance,
        due_date=ar.due_date,
        status=ar.status,
    )
    return result, ar


def plan_record_ar_payment(
    command: PaymentRecordCommand,
    ar_id: UUID,
    tenant_id: UUID,
) -> tuple[AccountsReceivableResult, Journal, list[JournalEntry], list[OutboxWrite]]:
    """Plan recording a payment against an Accounts Receivable record.

    When a customer pays their invoice:
        1. Debit: Cash Account (1000) -- we received money
        2. Credit: Accounts Receivable (1100) -- customer owes less

    This function creates the journal entries and returns the updated AR result.
    The actual AR update (amount_paid, balance, status) is done by the repository.

    Args:
        command: The PaymentRecordCommand containing payment details.
            Must include: tenant_id, amount, payment_date.
        ar_id: The UUID of the AR record being paid.
        tenant_id: The UUID of the business tenant.

    Returns:
        A tuple of:
            - AccountsReceivableResult: Updated AR result (caller updates balance).
            - Journal: The journal header for this payment.
            - list[JournalEntry]: The debit/credit lines for this payment.
            - list[OutboxWrite]: Empty list (events emitted later).

    Example:
        >>> command = PaymentRecordCommand(
        ...     tenant_id=uuid4(),
        ...     amount=25000,
        ...     payment_date=datetime.now(UTC),
        ... )
        >>> ar_result, journal, entries, events = plan_record_ar_payment(
        ...     command, ar_id=ar.id, tenant_id=tenant_id,
        ... )
    """
    journal_id = uuid4()
    journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"

    journal = Journal(
        id=journal_id,
        tenant_id=tenant_id,
        journal_number=journal_number,
        description=f"AR Payment - {command.amount} NGN",
        reference_id=ar_id,
        reference_type="ar_payment",
        status="draft",
    )

    # Debit: Cash (we received money)
    cash_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=tenant_id,
        account_id=uuid4(),  # Will be resolved by bridge layer
        account_code="1000",
        debit=command.amount,
        credit=0,
        description=f"Cash received - AR payment",
        type="asset",
        status="draft",
        amount=command.amount,
    )

    # Credit: Accounts Receivable (customer owes less)
    ar_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=tenant_id,
        account_id=uuid4(),  # Will be resolved by bridge layer
        account_code="1100",
        debit=0,
        credit=command.amount,
        description=f"AR reduction - customer payment",
        type="asset",
        status="draft",
        amount=command.amount,
    )

    ar_result = AccountsReceivableResult(
        id=ar_id,
        tenant_id=tenant_id,
        customer_id=uuid4(),
        customer_name="",
        invoice_number="",
        amount=0,
        amount_paid=0,
        balance=0,
        due_date=datetime.now(UTC),
        status="partial",
    )

    return ar_result, journal, [cash_entry, ar_entry], []


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE (AP) PLANNING
# ═══════════════════════════════════════════════════════════════════════════════


def plan_create_accounts_payable(
    command: AccountsPayableCreateCommand,
) -> tuple[AccountsPayableResult, AccountPayable]:
    """Plan the creation of an Accounts Payable record.

    AP is created when a bill is received from a vendor or a purchase order
    is fulfilled but not yet paid. The initial balance equals the full bill amount.

    Args:
        command: The AccountsPayableCreateCommand containing AP details.
            Must include: tenant_id, bill_number, vendor_name, amount, due_date.
            Optional: description.

    Returns:
        A tuple of:
            - AccountsPayableResult: Pydantic result schema for API responses.
            - AccountPayable: SQLAlchemy model instance for database persistence.
    """
    ap = AccountPayable(
        id=uuid4(),
        tenant_id=command.tenant_id,
        bill_number=command.bill_number,
        vendor_name=command.vendor_name,
        description=command.description,
        amount=command.amount,
        amount_paid=0,
        balance=command.amount,
        due_date=command.due_date,
        status="pending",
    )
    result = AccountsPayableResult(
        id=ap.id,
        tenant_id=ap.tenant_id,
        bill_number=ap.bill_number,
        vendor_name=ap.vendor_name,
        description=ap.description,
        amount=ap.amount,
        amount_paid=ap.amount_paid,
        balance=ap.balance,
        due_date=ap.due_date,
        status=ap.status,
    )
    return result, ap


def plan_record_ap_payment(
    command: PaymentRecordCommand,
    ap_id: UUID,
    tenant_id: UUID,
) -> tuple[AccountsPayableResult, Journal, list[JournalEntry], list[OutboxWrite]]:
    """Plan recording a payment against an Accounts Payable record.

    When the business pays a vendor:
        1. Debit: Accounts Payable (2000) -- we owe less
        2. Credit: Cash Account (1000) -- we paid out money

    This function creates the journal entries and returns the updated AP result.
    The actual AP update (amount_paid, balance, status) is done by the repository.

    Args:
        command: The PaymentRecordCommand containing payment details.
            Must include: tenant_id, amount, payment_date.
        ap_id: The UUID of the AP record being paid.
        tenant_id: The UUID of the business tenant.

    Returns:
        A tuple of:
            - AccountsPayableResult: Updated AP result (caller updates balance).
            - Journal: The journal header for this payment.
            - list[JournalEntry]: The debit/credit lines for this payment.
            - list[OutboxWrite]: Empty list (events emitted later).
    """
    journal_id = uuid4()
    journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"

    journal = Journal(
        id=journal_id,
        tenant_id=tenant_id,
        journal_number=journal_number,
        description=f"AP Payment - {command.amount} NGN",
        reference_id=ap_id,
        reference_type="ap_payment",
        status="draft",
    )

    # Debit: Accounts Payable (we owe less)
    ap_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=tenant_id,
        account_id=uuid4(),  # Will be resolved by bridge layer
        account_code="2000",
        debit=command.amount,
        credit=0,
        description=f"AP reduction - vendor payment",
        type="liability",
        status="draft",
        amount=command.amount,
    )

    # Credit: Cash (we paid out money)
    cash_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=tenant_id,
        account_id=uuid4(),  # Will be resolved by bridge layer
        account_code="1000",
        debit=0,
        credit=command.amount,
        description=f"Cash paid - vendor payment",
        type="asset",
        status="draft",
        amount=command.amount,
    )

    ap_result = AccountsPayableResult(
        id=ap_id,
        tenant_id=tenant_id,
        bill_number="",
        vendor_name="",
        description=None,
        amount=0,
        amount_paid=0,
        balance=0,
        due_date=datetime.now(UTC),
        status="partial",
    )

    return ap_result, journal, [ap_entry, cash_entry], []


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE PLANNING
# ═══════════════════════════════════════════════════════════════════════════════


def plan_create_expense(
    command: ExpenseCreateCommand,
) -> tuple[ExpenseResult, Expense, Journal, list[JournalEntry], list[OutboxWrite]]:
    """Plan the recording of a business expense.

    When an expense is recorded, a journal entry is automatically created:
        1. Debit: Expense Account (5xxx) -- increases expense
        2. Credit: Cash Account (1000) -- decreases cash

    The expense number is auto-generated: EXP-YYYYMMDD-XXXXXXXX.

    Args:
        command: The ExpenseCreateCommand containing expense details.
            Must include: tenant_id, category, description, amount,
            expense_date, account_id, created_by.
            Optional: vendor, receipt_url.

    Returns:
        A tuple of:
            - ExpenseResult: Pydantic result schema for API responses.
            - Expense: SQLAlchemy model instance for database persistence.
            - Journal: The journal header for this expense.
            - list[JournalEntry]: The debit/credit lines for this expense.
            - list[OutboxWrite]: Empty list (events emitted later).

    Example:
        >>> command = ExpenseCreateCommand(
        ...     tenant_id=uuid4(),
        ...     category="rent",
        ...     description="August shop rent",
        ...     amount=150000,
        ...     expense_date=datetime.now(UTC),
        ...     account_id=rent_account_id,
        ...     created_by=user_id,
        ... )
        >>> result, model, journal, entries, events = plan_create_expense(command)
    """
    expense_id = uuid4()
    expense_number = f"EXP-{datetime.now(UTC).strftime('%Y%m%d')}-{str(expense_id)[:8].upper()}"

    expense = Expense(
        id=expense_id,
        tenant_id=command.tenant_id,
        expense_number=expense_number,
        category=command.category,
        description=command.description,
        amount=command.amount,
        vendor=command.vendor,
        receipt_url=command.receipt_url,
        expense_date=command.expense_date,
        account_id=command.account_id,
        journal_id=None,  # Will be set after journal is created
        created_by=command.created_by,
    )

    # Create journal for the expense
    journal_id = uuid4()
    journal_number = f"JRN-{datetime.now(UTC).strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"

    journal = Journal(
        id=journal_id,
        tenant_id=command.tenant_id,
        journal_number=journal_number,
        description=f"Expense: {command.description}",
        reference_id=expense_id,
        reference_type="expense",
        status="draft",
    )

    # Debit: Expense account (increases expense)
    expense_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=command.tenant_id,
        account_id=command.account_id,
        account_code="",  # Will be resolved by bridge layer
        debit=command.amount,
        credit=0,
        description=f"Expense: {command.description}",
        type="expense",
        status="draft",
        amount=command.amount,
    )

    # Credit: Cash account (decreases cash)
    cash_entry = JournalEntry(
        id=uuid4(),
        journal_id=journal_id,
        tenant_id=command.tenant_id,
        account_id=uuid4(),  # Will be resolved by bridge layer
        account_code="1000",
        debit=0,
        credit=command.amount,
        description=f"Cash paid: {command.description}",
        type="asset",
        status="draft",
        amount=command.amount,
    )

    # Link journal to expense
    expense.journal_id = journal_id

    result = ExpenseResult(
        id=expense.id,
        tenant_id=expense.tenant_id,
        expense_number=expense.expense_number,
        category=expense.category,
        description=expense.description,
        amount=expense.amount,
        vendor=expense.vendor,
        receipt_url=expense.receipt_url,
        expense_date=expense.expense_date,
        account_id=expense.account_id,
        journal_id=expense.journal_id,
        created_by=expense.created_by,
    )

    return result, expense, journal, [expense_entry, cash_entry], []


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def _determine_entry_type(account_code: str) -> str:
    """Determine the financial type of an account based on its code.

    Account codes follow the convention:
        - 1xxx: Asset
        - 2xxx: Liability
        - 3xxx: Equity
        - 4xxx: Revenue
        - 5xxx: Expense

    Args:
        account_code: The account code string (e.g., "1000", "4000").

    Returns:
        The account type string: "asset", "liability", "equity", "revenue", or "expense".
    """
    if account_code.startswith("1"):
        return "asset"
    elif account_code.startswith("2"):
        return "liability"
    elif account_code.startswith("3"):
        return "equity"
    elif account_code.startswith("4"):
        return "revenue"
    elif account_code.startswith("5"):
        return "expense"
    else:
        return "asset"  # Default to asset for unknown codes
