"""Repository layer for accounting domain database operations.

This module provides async database CRUD (Create, Read, Update, Delete) operations
for all accounting domain models. All functions accept an AsyncSession and follow
the repository pattern -- they handle SQL queries and return model instances or
 dictionaries, keeping the service layer free of database concerns.

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- AP: Accounts Payable -- money the business OWES to vendors/suppliers.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- JE: Journal Entry -- a record of a financial transaction (debit/credit line).
- CRUD: Create, Read, Update, Delete -- the four basic database operations.
- FK: Foreign Key -- a reference from one table to another.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- SQL: Structured Query Language -- the language used to query databases.
- ASC: Ascending order -- smallest to largest (A-Z, 0-9).
- DESC: Descending order -- largest to smallest (Z-A, 9-0).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Date, case, cast, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounting.models import AccountPayable, AccountReceivable, ChartOfAccount, CommissionLedger, Expense, Journal, JournalEntry

# from storeflow_accounting.models import (
#     AccountPayable,
#     AccountReceivable,
#     ChartOfAccount,
#     CommissionLedger,
#     Expense,
#     Journal,
#     JournalEntry,
# )


# ═══════════════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS (COA) REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def create_account(
    session: AsyncSession,
    account: ChartOfAccount,
) -> ChartOfAccount:
    """Insert a new account into the Chart of Accounts.

    Args:
        session: The async SQLAlchemy database session to use.
        account: The ChartOfAccount model instance to persist.

    Returns:
        The same ChartOfAccount instance with the database-assigned ID.
    """
    session.add(account)
    await session.flush()
    return account


async def get_account_by_code(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
) -> ChartOfAccount | None:
    """Retrieve a single account by its code and tenant.

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        code: The account code to look up (e.g., "1000" for Cash).

    Returns:
        The ChartOfAccount if found, or None if no matching account exists.
    """
    result = await session.execute(
        select(ChartOfAccount).where(
            ChartOfAccount.tenant_id == tenant_id,
            ChartOfAccount.code == code,
        )
    )
    return result.scalar_one_or_none()


async def get_account_by_id(
    session: AsyncSession,
    account_id: UUID,
) -> ChartOfAccount | None:
    """Retrieve a single account by its UUID.

    Args:
        session: The async SQLAlchemy database session to use.
        account_id: The UUID of the account to retrieve.

    Returns:
        The ChartOfAccount if found, or None if no matching account exists.
    """
    result = await session.execute(
        select(ChartOfAccount).where(ChartOfAccount.id == account_id)
    )
    return result.scalar_one_or_none()


async def list_accounts(
    session: AsyncSession,
    tenant_id: UUID,
) -> list[ChartOfAccount]:
    """List all accounts in the Chart of Accounts for a tenant.

    Results are ordered by account code in ascending order (1xxx before 2xxx, etc.).

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.

    Returns:
        A list of ChartOfAccount instances, ordered by code.
    """
    result = await session.execute(
        select(ChartOfAccount)
        .where(ChartOfAccount.tenant_id == tenant_id)
        .order_by(ChartOfAccount.code)
    )
    return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def create_journal(
    session: AsyncSession,
    journal: Journal,
) -> Journal:
    """Insert a new journal record.

    Args:
        session: The async SQLAlchemy database session to use.
        journal: The Journal model instance to persist.

    Returns:
        The same Journal instance with the database-assigned ID.
    """
    session.add(journal)
    await session.flush()
    return journal


async def get_journal_by_id(
    session: AsyncSession,
    journal_id: UUID,
) -> Journal | None:
    """Retrieve a single journal by its UUID.

    Args:
        session: The async SQLAlchemy database session to use.
        journal_id: The UUID of the journal to retrieve.

    Returns:
        The Journal if found, or None if no matching journal exists.
    """
    result = await session.execute(
        select(Journal).where(Journal.id == journal_id)
    )
    return result.scalar_one_or_none()


async def list_journals(
    session: AsyncSession,
    tenant_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Journal], int]:
    """List journals for a tenant with pagination.

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        limit: Maximum number of journals to return (default: 50).
        offset: Number of journals to skip for pagination (default: 0).

    Returns:
        A tuple of (list of Journal instances, total count for pagination).
    """
    count_result = await session.execute(
        select(func.count(Journal.id)).where(Journal.tenant_id == tenant_id)
    )
    total = count_result.scalar() or 0

    result = await session.execute(
        select(Journal)
        .where(Journal.tenant_id == tenant_id)
        .order_by(desc(Journal.created_at))
        .limit(limit)
        .offset(offset)
    )
    journals = list(result.scalars().all())
    return journals, total


async def post_journal(
    session: AsyncSession,
    journal_id: UUID,
    posted_by: UUID | None,
) -> Journal:
    """Transition a journal from "draft" to "posted" status.

    Posting a journal makes it immutable -- it can no longer be edited or deleted.
    The journal entries are now reflected in the general ledger and financial statements.

    Args:
        session: The async SQLAlchemy database session to use.
        journal_id: The UUID of the journal to post.
        posted_by: The UUID of the user posting this journal.

    Returns:
        The updated Journal instance with status="posted" and posted_at set.

    Raises:
        ZeroFetch: If no journal with the given ID exists.
    """
    result = await session.execute(
        select(Journal).where(Journal.id == journal_id)
    )
    journal = result.scalar_one()
    journal.status = "posted"
    journal.posted_by = posted_by
    journal.posted_at = datetime.now(UTC)
    await session.flush()
    return journal


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL ENTRY REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def create_journal_entry(
    session: AsyncSession,
    entry: JournalEntry,
) -> JournalEntry:
    """Insert a new journal entry (debit or credit line).

    Args:
        session: The async SQLAlchemy database session to use.
        entry: The JournalEntry model instance to persist.

    Returns:
        The same JournalEntry instance with the database-assigned ID.
    """
    session.add(entry)
    await session.flush()
    return entry


async def get_journal_entries(
    session: AsyncSession,
    journal_id: UUID,
) -> list[JournalEntry]:
    """List all journal entries for a specific journal.

    Args:
        session: The async SQLAlchemy database session to use.
        journal_id: The UUID of the parent journal.

    Returns:
        A list of JournalEntry instances belonging to the journal.
    """
    result = await session.execute(
        select(JournalEntry)
        .where(JournalEntry.journal_id == journal_id)
        .order_by(JournalEntry.id)
    )
    return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMISSION LEDGER REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def create_commission(
    session: AsyncSession,
    commission: CommissionLedger,
) -> CommissionLedger:
    """Insert a new commission record.

    Args:
        session: The async SQLAlchemy database session to use.
        commission: The CommissionLedger model instance to persist.

    Returns:
        The same CommissionLedger instance with the database-assigned ID.
    """
    session.add(commission)
    await session.flush()
    return commission


async def mark_commission_paid(
    session: AsyncSession,
    commission_id: UUID,
) -> CommissionLedger:
    """Mark a commission as paid.

    Args:
        session: The async SQLAlchemy database session to use.
        commission_id: The UUID of the commission to mark as paid.

    Returns:
        The updated CommissionLedger instance with status="paid" and paid_at set.

    Raises:
        ZeroFetch: If no commission with the given ID exists.
    """
    result = await session.execute(
        select(CommissionLedger).where(CommissionLedger.id == commission_id)
    )
    commission = result.scalar_one()
    commission.status = "paid"
    commission.paid_at = datetime.now(UTC)
    await session.flush()
    return commission


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS RECEIVABLE (AR) REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def create_accounts_receivable(
    session: AsyncSession,
    ar: AccountReceivable,
) -> AccountReceivable:
    """Insert a new Accounts Receivable record.

    Args:
        session: The async SQLAlchemy database session to use.
        ar: The AccountReceivable model instance to persist.

    Returns:
        The same AccountReceivable instance with the database-assigned ID.
    """
    session.add(ar)
    await session.flush()
    return ar


async def get_accounts_receivable_by_id(
    session: AsyncSession,
    ar_id: UUID,
) -> AccountReceivable | None:
    """Retrieve a single AR record by its UUID.

    Args:
        session: The async SQLAlchemy database session to use.
        ar_id: The UUID of the AR record to retrieve.

    Returns:
        The AccountReceivable if found, or None if no matching record exists.
    """
    result = await session.execute(
        select(AccountReceivable).where(AccountReceivable.id == ar_id)
    )
    return result.scalar_one_or_none()


async def list_accounts_receivable(
    session: AsyncSession,
    tenant_id: UUID,
    status_filter: str | None = None,
) -> list[AccountReceivable]:
    """List all Accounts Receivable records for a tenant.

    Optionally filters by status (e.g., "pending", "overdue", "partial", "paid").
    Results are ordered by due_date in ascending order (earliest due first).

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        status_filter: Optional status to filter by. If None, returns all statuses.

    Returns:
        A list of AccountReceivable instances, ordered by due_date ascending.
    """
    query = select(AccountReceivable).where(AccountReceivable.tenant_id == tenant_id)
    if status_filter:
        query = query.where(AccountReceivable.status == status_filter)
    query = query.order_by(AccountReceivable.due_date)
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_ar_payment(
    session: AsyncSession,
    ar_id: UUID,
    payment_amount: float,
) -> AccountReceivable:
    """Record a payment against an AR record and update its status.

    This function:
        1. Adds the payment_amount to amount_paid
        2. Subtracts the payment_amount from balance
        3. Updates status to "partial" or "paid" based on remaining balance

    Args:
        session: The async SQLAlchemy database session to use.
        ar_id: The UUID of the AR record to update.
        payment_amount: The payment amount in NGN to apply.

    Returns:
        The updated AccountReceivable instance.

    Raises:
        ZeroFetch: If no AR record with the given ID exists.
        ValueError: If payment_amount exceeds the outstanding balance.
    """
    result = await session.execute(
        select(AccountReceivable).where(AccountReceivable.id == ar_id)
    )
    ar = result.scalar_one()

    if payment_amount > ar.balance:
        raise ValueError(
            f"Payment amount ({payment_amount}) exceeds outstanding balance ({ar.balance})"
        )

    ar.amount_paid = float(ar.amount_paid) + payment_amount
    ar.balance = float(ar.amount) - float(ar.amount_paid)

    if ar.balance <= 0:
        ar.status = "paid"
    else:
        ar.status = "partial"

    await session.flush()
    return ar


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE (AP) REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def create_accounts_payable(
    session: AsyncSession,
    ap: AccountPayable,
) -> AccountPayable:
    """Insert a new Accounts Payable record.

    Args:
        session: The async SQLAlchemy database session to use.
        ap: The AccountPayable model instance to persist.

    Returns:
        The same AccountPayable instance with the database-assigned ID.
    """
    session.add(ap)
    await session.flush()
    return ap


async def get_accounts_payable_by_id(
    session: AsyncSession,
    ap_id: UUID,
) -> AccountPayable | None:
    """Retrieve a single AP record by its UUID.

    Args:
        session: The async SQLAlchemy database session to use.
        ap_id: The UUID of the AP record to retrieve.

    Returns:
        The AccountPayable if found, or None if no matching record exists.
    """
    result = await session.execute(
        select(AccountPayable).where(AccountPayable.id == ap_id)
    )
    return result.scalar_one_or_none()


async def list_accounts_payable(
    session: AsyncSession,
    tenant_id: UUID,
    status_filter: str | None = None,
) -> list[AccountPayable]:
    """List all Accounts Payable records for a tenant.

    Optionally filters by status (e.g., "pending", "overdue", "partial", "paid").
    Results are ordered by due_date in ascending order (earliest due first).

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        status_filter: Optional status to filter by. If None, returns all statuses.

    Returns:
        A list of AccountPayable instances, ordered by due_date ascending.
    """
    query = select(AccountPayable).where(AccountPayable.tenant_id == tenant_id)
    if status_filter:
        query = query.where(AccountPayable.status == status_filter)
    query = query.order_by(AccountPayable.due_date)
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_ap_payment(
    session: AsyncSession,
    ap_id: UUID,
    payment_amount: float,
) -> AccountPayable:
    """Record a payment against an AP record and update its status.

    This function:
        1. Adds the payment_amount to amount_paid
        2. Subtracts the payment_amount from balance
        3. Updates status to "partial" or "paid" based on remaining balance

    Args:
        session: The async SQLAlchemy database session to use.
        ap_id: The UUID of the AP record to update.
        payment_amount: The payment amount in NGN to apply.

    Returns:
        The updated AccountPayable instance.

    Raises:
        ZeroFetch: If no AP record with the given ID exists.
        ValueError: If payment_amount exceeds the outstanding balance.
    """
    result = await session.execute(
        select(AccountPayable).where(AccountPayable.id == ap_id)
    )
    ap = result.scalar_one()

    if payment_amount > ap.balance:
        raise ValueError(
            f"Payment amount ({payment_amount}) exceeds outstanding balance ({ap.balance})"
        )

    ap.amount_paid = float(ap.amount_paid) + payment_amount
    ap.balance = float(ap.amount) - float(ap.amount_paid)

    if ap.balance <= 0:
        ap.status = "paid"
    else:
        ap.status = "partial"

    await session.flush()
    return ap


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def create_expense(
    session: AsyncSession,
    expense: Expense,
) -> Expense:
    """Insert a new expense record.

    Args:
        session: The async SQLAlchemy database session to use.
        expense: The Expense model instance to persist.

    Returns:
        The same Expense instance with the database-assigned ID.
    """
    session.add(expense)
    await session.flush()
    return expense


async def list_expenses(
    session: AsyncSession,
    tenant_id: UUID,
    category: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[Expense]:
    """List expenses for a tenant with optional filtering.

    Supports filtering by:
        - category: Filter by expense category (e.g., "rent", "utilities").
        - from_date: Only include expenses on or after this date.
        - to_date: Only include expenses on or before this date.

    Results are ordered by expense_date in descending order (newest first).

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        category: Optional expense category to filter by.
        from_date: Optional start date for date range filtering.
        to_date: Optional end date for date range filtering.

    Returns:
        A list of Expense instances matching the filters, ordered by date descending.
    """
    query = select(Expense).where(Expense.tenant_id == tenant_id)

    if category:
        query = query.where(Expense.category == category)
    if from_date:
        query = query.where(Expense.expense_date >= from_date)
    if to_date:
        query = query.where(Expense.expense_date <= to_date)

    query = query.order_by(desc(Expense.expense_date))
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_expense_summary(
    session: AsyncSession,
    tenant_id: UUID,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, float]:
    """Get a summary of expenses grouped by category.

    Returns a dictionary where keys are category names and values are the
    total amount spent in that category. Useful for the expense breakdown
    chart on the financial dashboard.

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        from_date: Optional start date for date range filtering.
        to_date: Optional end date for date range filtering.

    Returns:
        A dictionary mapping category names to total amounts.
        Example: {"rent": 500000.0, "utilities": 120000.0, "supplies": 45000.0}
    """
    query = select(
        Expense.category,
        func.sum(Expense.amount).label("total"),
    ).where(Expense.tenant_id == tenant_id)

    if from_date:
        query = query.where(Expense.expense_date >= from_date)
    if to_date:
        query = query.where(Expense.expense_date <= to_date)

    query = query.group_by(Expense.category)
    result = await session.execute(query)

    return {row.category: float(row.total) for row in result.all()}


# ═══════════════════════════════════════════════════════════════════════════════
#  BALANCE SHEET REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def get_balance_sheet(
    session: AsyncSession,
    tenant_id: UUID,
    as_at_date: datetime | None = None,
) -> dict:
    """Calculate the Balance Sheet as of a specific date.

    The Balance Sheet shows the business's financial position at a point in time:
        Assets = Liabilities + Equity

    This function calculates:
        1. Assets: Sum of all asset account balances (debit balances)
        2. Liabilities: Sum of all liability account balances (credit balances)
        3. Equity: Owner's Capital + Retained Earnings (from P&L)

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to calculate for.
        as_at_date: The date to calculate the balance sheet for. If None,
            uses the current date.

    Returns:
        A dictionary containing:
            - "assets": Total assets in NGN
            - "liabilities": Total liabilities in NGN
            - "equity": Total equity in NGN (assets - liabilities)
            - "asset_accounts": List of individual asset accounts with balances
            - "liability_accounts": List of individual liability accounts with balances
            - "equity_accounts": List of individual equity accounts with balances
    """
    if as_at_date is None:
        as_at_date = datetime.now(UTC)

    # Get all active accounts for the tenant
    accounts = await list_accounts(session, tenant_id)

    # Separate accounts by type
    asset_accounts = [a for a in accounts if a.account_type == "asset"]
    liability_accounts = [a for a in accounts if a.account_type == "liability"]
    equity_accounts = [a for a in accounts if a.account_type == "equity"]

    # Calculate balances from journal entries
    asset_total = 0.0
    liability_total = 0.0
    equity_total = 0.0

    asset_details = []
    liability_details = []
    equity_details = []

    for account in asset_accounts:
        balance = await _get_account_balance(
            session=session,
            account_id=account.id,
            account_type="asset",
            as_at_date=as_at_date,
        )
        if balance != 0:
            asset_total += balance
            asset_details.append({
                "code": account.code,
                "name": account.name,
                "balance": round(balance, 2),
            })

    for account in liability_accounts:
        balance = await _get_account_balance(
            session=session,
            account_id=account.id,
            account_type="liability",
            as_at_date=as_at_date,
        )
        if balance != 0:
            liability_total += balance
            liability_details.append({
                "code": account.code,
                "name": account.name,
                "balance": round(balance, 2),
            })

    for account in equity_accounts:
        balance = await _get_account_balance(
            session=session,
            account_id=account.id,
            account_type="equity",
            as_at_date=as_at_date,
        )
        if balance != 0:
            equity_total += balance
            equity_details.append({
                "code": account.code,
                "name": account.name,
                "balance": round(balance, 2),
            })

    return {
        "as_at_date": as_at_date.date().isoformat() if hasattr(as_at_date, "date") else str(as_at_date),
        "assets": round(asset_total, 2),
        "liabilities": round(liability_total, 2),
        "equity": round(asset_total - liability_total, 2),
        "asset_accounts": asset_details,
        "liability_accounts": liability_details,
        "equity_accounts": equity_details,
    }


async def _get_account_balance(
    session: AsyncSession,
    account_id: UUID,
    account_type: str,
    as_at_date: datetime,
) -> float:
    """Calculate the balance of a single account as of a specific date.

    For asset and expense accounts: balance = total debits - total credits
    For liability, equity, and revenue accounts: balance = total credits - total debits

    Args:
        session: The async SQLAlchemy database session to use.
        account_id: The UUID of the account to calculate balance for.
        account_type: The type of account ("asset", "liability", "equity", etc.).
        as_at_date: The date to calculate the balance for.

    Returns:
        The account balance as a float in NGN.
    """
    query = select(
        func.coalesce(func.sum(JournalEntry.debit), 0).label("total_debit"),
        func.coalesce(func.sum(JournalEntry.credit), 0).label("total_credit"),
    ).where(
        JournalEntry.account_id == account_id,
        JournalEntry.status == "posted",
        JournalEntry.posted_at <= as_at_date,
    )

    result = await session.execute(query)
    row = result.one()

    total_debit = float(row.total_debit)
    total_credit = float(row.total_credit)

    # Asset/Expense: debit increases, credit decreases
    # Liability/Equity/Revenue: credit increases, debit decreases
    if account_type in ("asset", "expense"):
        return total_debit - total_credit
    else:
        return total_credit - total_debit


# ═══════════════════════════════════════════════════════════════════════════════
#  CASH FLOW REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def get_cash_flow(
    session: AsyncSession,
    tenant_id: UUID,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict:
    """Calculate the Cash Flow Statement for a period.

    The Cash Flow Statement shows how cash moved in and out of the business
    during a specific period. It categorizes cash flows into three activities:

    1. Operating Activities: Cash from sales, payments to suppliers, expenses
    2. Investing Activities: Cash from asset purchases/sales (future feature)
    3. Financing Activities: Cash from loans, owner contributions (future feature)

    For now, we focus on Operating Activities since that's what small businesses
    need most. Investing and Financing activities can be added later.

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to calculate for.
        from_date: The start of the period. If None, defaults to 30 days ago.
        to_date: The end of the period. If None, defaults to today.

    Returns:
        A dictionary containing:
            - "period": {"from": "...", "to": "..."}
            - "operating": {"inflows": [...], "outflows": [...], "net": float}
            - "total_inflows": float
            - "total_outflows": float
            - "net_cash_flow": float
    """
    if from_date is None:
        from_date = datetime.now(UTC) - timedelta(days=30)
    if to_date is None:
        to_date = datetime.now(UTC)

    # Cash inflows: Debits to Cash account (1000) from revenue journals
    inflows = await _get_cash_flows_by_type(
        session=session,
        tenant_id=tenant_id,
        account_code="1000",
        flow_type="inflow",
        from_date=from_date,
        to_date=to_date,
    )

    # Cash outflows: Credits to Cash account (1000) from expense/payment journals
    outflows = await _get_cash_flows_by_type(
        session=session,
        tenant_id=tenant_id,
        account_code="1000",
        flow_type="outflow",
        from_date=from_date,
        to_date=to_date,
    )

    total_inflows = sum(item["amount"] for item in inflows)
    total_outflows = sum(item["amount"] for item in outflows)

    return {
        "period": {
            "from": from_date.date().isoformat() if hasattr(from_date, "date") else str(from_date),
            "to": to_date.date().isoformat() if hasattr(to_date, "date") else str(to_date),
        },
        "operating": {
            "inflows": inflows,
            "outflows": outflows,
            "net": round(total_inflows - total_outflows, 2),
        },
        "total_inflows": round(total_inflows, 2),
        "total_outflows": round(total_outflows, 2),
        "net_cash_flow": round(total_inflows - total_outflows, 2),
    }


async def _get_cash_flows_by_type(
    session: AsyncSession,
    tenant_id: UUID,
    account_code: str,
    flow_type: str,
    from_date: datetime,
    to_date: datetime,
) -> list[dict]:
    """Get cash inflows or outflows for a specific account code.

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        account_code: The account code to query (e.g., "1000" for Cash).
        flow_type: Either "inflow" (debits to cash) or "outflow" (credits to cash).
        from_date: Start of the date range.
        to_date: End of the date range.

    Returns:
        A list of dictionaries with "date", "description", and "amount" keys.
    """
    if flow_type == "inflow":
        # Cash inflows are debits to the cash account
        amount_col = JournalEntry.debit
        filter_condition = JournalEntry.debit > 0
    else:
        # Cash outflows are credits to the cash account
        amount_col = JournalEntry.credit
        filter_condition = JournalEntry.credit > 0

    query = select(
        JournalEntry.description,
        func.sum(amount_col).label("amount"),
        Journal.posted_at,
    ).join(
        Journal, Journal.id == JournalEntry.journal_id
    ).where(
        JournalEntry.account_code == account_code,
        Journal.tenant_id == tenant_id,
        Journal.status == "posted",
        filter_condition,
        Journal.posted_at >= from_date,
        Journal.posted_at <= to_date,
    ).group_by(
        JournalEntry.description,
        Journal.posted_at,
    ).order_by(
        desc(Journal.posted_at),
    )

    result = await session.execute(query)
    flows = []
    for row in result.all():
        flows.append({
            "date": row.posted_at.date().isoformat() if row.posted_at else None,
            "description": row.description or "Unknown",
            "amount": round(float(row.amount), 2),
        })

    return flows


# ═══════════════════════════════════════════════════════════════════════════════
#  FINANCIAL DASHBOARD REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════


async def get_financial_dashboard(
    session: AsyncSession,
    tenant_id: UUID,
) -> dict:
    """Get key financial metrics for the dashboard.

    This function aggregates multiple financial metrics into a single response
    for display on the financial dashboard. It provides a quick overview of the
    business's financial health.

    Metrics included:
        1. Total Revenue (this month)
        2. Total Expenses (this month)
        3. Net Profit (this month)
        4. Cash Balance (current)
        5. Outstanding AR (total owed by customers)
        6. Outstanding AP (total owed to vendors)
        7. Top Expense Categories (this month)

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to calculate for.

    Returns:
        A dictionary containing all dashboard metrics.
    """
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Cash balance from journal entries
    cash_balance = await _get_account_balance(
        session=session,
        account_id=await _get_account_id_by_code(session, tenant_id, "1000"),
        account_type="asset",
        as_at_date=now,
    )

    # Outstanding AR
    ar_result = await session.execute(
        select(func.coalesce(func.sum(AccountReceivable.balance), 0)).where(
            AccountReceivable.tenant_id == tenant_id,
            AccountReceivable.status.in_(["pending", "overdue", "partial"]),
        )
    )
    outstanding_ar = float(ar_result.scalar() or 0)

    # Outstanding AP
    ap_result = await session.execute(
        select(func.coalesce(func.sum(AccountPayable.balance), 0)).where(
            AccountPayable.tenant_id == tenant_id,
            AccountPayable.status.in_(["pending", "overdue", "partial"]),
        )
    )
    outstanding_ap = float(ap_result.scalar() or 0)

    # Expense summary this month
    expense_summary = await get_expense_summary(
        session=session,
        tenant_id=tenant_id,
        from_date=month_start,
        to_date=now,
    )
    total_expenses = sum(expense_summary.values())

    return {
        "cash_balance": round(cash_balance, 2),
        "outstanding_receivable": round(outstanding_ar, 2),
        "outstanding_payable": round(outstanding_ap, 2),
        "total_expenses_this_month": round(total_expenses, 2),
        "expense_by_category": {k: round(v, 2) for k, v in expense_summary.items()},
    }


async def _get_account_id_by_code(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
) -> UUID:
    """Helper to get account ID by code. Raises if not found.

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        code: The account code to look up.

    Returns:
        The UUID of the account.

    Raises:
        ValueError: If no account with the given code exists for the tenant.
    """
    account = await get_account_by_code(session, tenant_id, code)
    if not account:
        raise ValueError(f"Account with code '{code}' not found for tenant {tenant_id}")
    return account.id
