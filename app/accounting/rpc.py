"""Accounting RPC functions — thin wrappers around Postgres stored procedures.

Each function calls a Postgres function via helpers.call() / helpers.call_scalar().
The Postgres functions do ALL the aggregation in a single DB round-trip.

Pattern: route → rpc.xxx() → helpers.call(session, "fn_xxx", ...) → Postgres
"""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.helpers import call, call_scalar


def _stringify(rows: list[dict] | dict) -> list[dict] | dict:
    """Convert UUID/datetime objects to strings for Pydantic compatibility."""
    def _convert(obj):
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(v) for v in obj]
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    if isinstance(rows, list):
        return [_convert(r) for r in rows]
    return _convert(rows)


# ═══════════════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def create_account(
    session: AsyncSession,
    *,
    business_id: str,
    code: str,
    name: str,
    account_type: str,
    parent_id: str | None = None,
) -> dict:
    """Create a new account in the Chart of Accounts.

    Calls Postgres fn_create_account which handles:
    - Uniqueness check (tenant_id + code)
    - Insert into chart_of_accounts
    - Returns the created account
    """
    rows = await call(
        session,
        "fn_create_account",
        p_tenant_id=UUID(business_id),
        p_code=code,
        p_name=name,
        p_account_type=account_type,
        p_parent_id=UUID(parent_id) if parent_id else None,
    )
    return _stringify(rows[0]) if rows else {}


async def list_accounts(session: AsyncSession, *, business_id: str) -> list[dict]:
    """List all accounts in the Chart of Accounts.

    Calls Postgres fn_list_accounts which returns accounts ordered by code.
    """
    return _stringify(await call(
        session,
        "fn_list_accounts",
        p_tenant_id=UUID(business_id),
    ))


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def post_journal(
    session: AsyncSession,
    *,
    business_id: str,
    user_id: str,
    description: str,
    entries: list[dict],
    ref_id: str | None = None,
    ref_type: str | None = None,
    store_id: str | None = None,
) -> str:
    """Create and post a journal with balanced debit/credit entries.

    Calls Postgres fn_post_journal which handles:
    - Validating debits == credits
    - Creating the journal (status = posted)
    - Creating all journal entry lines
    - Returns the journal UUID
    """
    journal_id = await call_scalar(
        session,
        "fn_post_journal",
        p_tenant_id=UUID(business_id),
        p_uid=UUID(user_id),
        p_desc=description,
        p_entries=json.dumps(entries, default=str),
        p_ref_id=UUID(ref_id) if ref_id else None,
        p_ref_type=ref_type,
        p_store_id=UUID(store_id) if store_id else None,
    )
    return str(journal_id)


async def list_journals(
    session: AsyncSession,
    *,
    business_id: str,
    page: int = 1,
    page_size: int = 50,
    store_id: str | None = None,
) -> dict:
    """List journals with pagination.

    Calls Postgres fn_list_journals which returns paginated journals
    with entry counts. store_id filters to one store (NULL = all stores).
    """
    rows = await call(
        session,
        "fn_list_journals",
        p_tenant_id=UUID(business_id),
        p_limit=page_size,
        p_offset=(page - 1) * page_size,
        p_store_id=UUID(store_id) if store_id else None,
    )
    return {
        "items": _stringify(rows),
        "total": len(rows),
        "page": page,
        "page_size": page_size,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  FINANCIAL STATEMENTS RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def trial_balance(
    session: AsyncSession,
    *,
    business_id: str,
    as_at: str | None = None,
    store_id: str | None = None,
) -> list[dict]:
    """Get the Trial Balance as of a date.

    Calls Postgres fn_trial_balance which calculates all account balances
    in a single query. store_id filters to one store (NULL = all stores).
    """
    return _stringify(await call(
        session,
        "fn_trial_balance",
        p_tenant_id=UUID(business_id),
        p_at=as_at,
        p_store_id=UUID(store_id) if store_id else None,
    ))


async def profit_and_loss(
    session: AsyncSession,
    *,
    business_id: str,
    from_date: str,
    to_date: str,
    store_id: str | None = None,
) -> dict:
    """Get the Profit and Loss statement.

    Calls Postgres fn_profit_and_loss which aggregates revenue and expenses.
    store_id filters to one store (NULL = all stores).
    """
    rows = _stringify(await call(
        session,
        "fn_profit_and_loss",
        p_tenant_id=UUID(business_id),
        p_from=from_date,
        p_to=to_date,
        p_store_id=UUID(store_id) if store_id else None,
    ))
    revenue = [r for r in rows if r.get("account_type") == "revenue"]
    expenses = [r for r in rows if r.get("account_type") == "expense"]
    return {
        "revenue": revenue,
        "expenses": expenses,
        "total_revenue": sum(float(r.get("amount", 0)) for r in revenue),
        "total_expenses": sum(float(r.get("amount", 0)) for r in expenses),
    }


async def balance_sheet(
    session: AsyncSession,
    *,
    business_id: str,
    as_at: str | None = None,
) -> dict:
    """Get the Balance Sheet as of a date.

    Calls Postgres fn_balance_sheet which aggregates assets, liabilities, equity.
    """
    rows = await call(
        session,
        "fn_balance_sheet",
        p_tenant_id=UUID(business_id),
        p_at=as_at,
    )
    return rows[0] if rows else {}


async def cash_flow(
    session: AsyncSession,
    *,
    business_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Get the Cash Flow Statement.

    Calls Postgres fn_cash_flow which aggregates inflows and outflows.
    """
    rows = await call(
        session,
        "fn_cash_flow",
        p_tenant_id=UUID(business_id),
        p_from=from_date,
        p_to=to_date,
    )
    return rows[0] if rows else {}


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS RECEIVABLE RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def list_accounts_receivable(
    session: AsyncSession,
    *,
    business_id: str,
    status_filter: str | None = None,
    store_id: str | None = None,
) -> list[dict]:
    """List Accounts Receivable."""
    return _stringify(await call(
        session,
        "fn_list_accounts_receivable",
        p_tenant_id=UUID(business_id),
        p_status=status_filter,
        p_store_id=UUID(store_id) if store_id else None,
    ))


async def create_accounts_receivable(
    session: AsyncSession,
    *,
    business_id: str,
    customer_id: str,
    customer_name: str,
    invoice_number: str,
    amount: float,
    due_date: str,
    invoice_id: str | None = None,
    store_id: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Create a new Accounts Receivable record.

    The SQL function also posts Dr 1100 Receivable / Cr 4000 Revenue so the
    general ledger and the receivable sub-ledger stay in step.
    """
    rows = await call(
        session,
        "fn_create_accounts_receivable",
        p_tenant_id=UUID(business_id),
        p_customer_id=UUID(customer_id),
        p_customer_name=customer_name,
        p_invoice_number=invoice_number,
        p_amount=amount,
        p_due_date=due_date,
        p_invoice_id=UUID(invoice_id) if invoice_id else None,
        p_store_id=UUID(store_id) if store_id else None,
        p_created_by=UUID(created_by) if created_by else None,
    )
    row = _stringify(rows[0]) if rows else {}
    if "out_id" in row:
        row["id"] = row.pop("out_id")
    if "out_tenant_id" in row:
        row["tenant_id"] = row.pop("out_tenant_id")
    if "out_customer_id" in row:
        row["customer_id"] = row.pop("out_customer_id")
    if "out_customer_name" in row:
        row["customer_name"] = row.pop("out_customer_name")
    return row


async def record_ar_payment(
    session: AsyncSession,
    *,
    business_id: str,
    ar_id: str,
    amount: float,
    payment_date: str,
    notes: str | None = None,
    recorded_by: str | None = None,
) -> dict:
    """Record a payment against an AR record.

    Calls Postgres fn_record_ar_payment which:
    - Locks the AR row and rejects an amount above the remaining balance
    - Updates the AR record (amount_paid, balance, status)
    - Appends a row to the ar_payments ledger

    Returns the updated AR row plus ``payment_id``, or ``error`` when the
    payment was refused (the caller turns that into a 400).
    """
    rows = await call(
        session,
        "fn_record_ar_payment",
        p_tenant_id=UUID(business_id),
        p_ar_id=UUID(ar_id),
        p_amount=amount,
        p_payment_date=payment_date,
        p_notes=notes,
        p_recorded_by=UUID(recorded_by) if recorded_by else None,
    )
    row = _stringify(rows[0]) if rows else {}
    if "out_id" in row:
        row["id"] = row.pop("out_id")
    if "out_tenant_id" in row:
        row["tenant_id"] = row.pop("out_tenant_id")
    if "out_payment_id" in row:
        row["payment_id"] = row.pop("out_payment_id")
    if "out_error" in row:
        row["error"] = row.pop("out_error")
    return row


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def list_accounts_payable(
    session: AsyncSession,
    *,
    business_id: str,
    status_filter: str | None = None,
    store_id: str | None = None,
) -> list[dict]:
    """List Accounts Payable."""
    return _stringify(await call(
        session,
        "fn_list_accounts_payable",
        p_tenant_id=UUID(business_id),
        p_status=status_filter,
        p_store_id=UUID(store_id) if store_id else None,
    ))


async def create_accounts_payable(
    session: AsyncSession,
    *,
    business_id: str,
    bill_number: str,
    vendor_name: str,
    amount: float,
    due_date: str,
    description: str | None = None,
    store_id: str | None = None,
    expense_category: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Create a new Accounts Payable record.

    The SQL function also posts Dr <expense> / Cr 2000 Payable so the accrual
    is recognised when the bill arrives. ``expense_category`` picks the
    counterpart account from the same map the expense endpoint uses.
    """
    rows = await call(
        session,
        "fn_create_accounts_payable",
        p_tenant_id=UUID(business_id),
        p_bill_number=bill_number,
        p_vendor_name=vendor_name,
        p_amount=amount,
        p_due_date=due_date,
        p_description=description,
        p_store_id=UUID(store_id) if store_id else None,
        p_expense_category=expense_category or "other",
        p_created_by=UUID(created_by) if created_by else None,
    )
    row = _stringify(rows[0]) if rows else {}
    if "out_id" in row:
        row["id"] = row.pop("out_id")
    if "out_tenant_id" in row:
        row["tenant_id"] = row.pop("out_tenant_id")
    return row


async def list_ar_payments(
    session: AsyncSession,
    *,
    business_id: str,
    ar_id: str,
) -> list[dict]:
    """Return the payment ledger for one receivable, newest payment first."""
    rows = await call(
        session,
        "fn_list_ar_payments",
        p_tenant_id=UUID(business_id),
        p_ar_id=UUID(ar_id),
    )
    return [_stringify(r) for r in rows]


async def list_ap_payments(
    session: AsyncSession,
    *,
    business_id: str,
    ap_id: str,
) -> list[dict]:
    """Return the payment ledger for one payable, newest payment first."""
    rows = await call(
        session,
        "fn_list_ap_payments",
        p_tenant_id=UUID(business_id),
        p_ap_id=UUID(ap_id),
    )
    return [_stringify(r) for r in rows]


async def record_ap_payment(
    session: AsyncSession,
    *,
    business_id: str,
    ap_id: str,
    amount: float,
    payment_date: str,
    notes: str | None = None,
    recorded_by: str | None = None,
) -> dict:
    """Record a payment against an AP record.

    Calls Postgres fn_record_ap_payment which:
    - Locks the AP row and rejects an amount above the remaining balance
    - Updates the AP record (amount_paid, balance, status)
    - Appends a row to the ap_payments ledger

    Returns the updated AP row plus ``payment_id``, or ``error`` when the
    payment was refused (the caller turns that into a 400).
    """
    rows = await call(
        session,
        "fn_record_ap_payment",
        p_tenant_id=UUID(business_id),
        p_ap_id=UUID(ap_id),
        p_amount=amount,
        p_payment_date=payment_date,
        p_notes=notes,
        p_recorded_by=UUID(recorded_by) if recorded_by else None,
    )
    row = _stringify(rows[0]) if rows else {}
    if "out_id" in row:
        row["id"] = row.pop("out_id")
    if "out_tenant_id" in row:
        row["tenant_id"] = row.pop("out_tenant_id")
    if "out_payment_id" in row:
        row["payment_id"] = row.pop("out_payment_id")
    if "out_error" in row:
        row["error"] = row.pop("out_error")
    return row


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def list_expenses(
    session: AsyncSession,
    *,
    business_id: str,
    category: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    store_id: str | None = None,
) -> list[dict]:
    """List expenses with optional filtering (store_id = NULL shows all stores)."""
    return _stringify(await call(
        session,
        "fn_list_expenses",
        p_tenant_id=UUID(business_id),
        p_category=category,
        p_from=from_date,
        p_to=to_date,
        p_store_id=UUID(store_id) if store_id else None,
    ))


async def create_expense(
    session: AsyncSession,
    *,
    business_id: str,
    category: str,
    description: str,
    amount: float,
    expense_date: str,
    created_by: str,
    vendor: str | None = None,
    receipt_url: str | None = None,
    store_id: str | None = None,
) -> dict:
    """Record a new business expense.

    Calls Postgres fn_create_expense which:
    - Auto-determines expense account from category
    - Creates the expense record
    - Creates journal entries (Debit Expense, Credit Cash)
    - Tags expense + journal with store_id when provided
    """
    rows = await call(
        session,
        "fn_create_expense",
        p_tenant_id=UUID(business_id),
        p_category=category,
        p_description=description,
        p_amount=amount,
        p_expense_date=expense_date,
        p_created_by=UUID(created_by),
        p_vendor=vendor,
        p_receipt_url=receipt_url,
        p_store_id=UUID(store_id) if store_id else None,
    )
    row = _stringify(rows[0]) if rows else {}
    if "out_id" in row:
        row["id"] = row.pop("out_id")
    if "out_tenant_id" in row:
        row["tenant_id"] = row.pop("out_tenant_id")
    return row


async def expense_summary(
    session: AsyncSession,
    *,
    business_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    store_id: str | None = None,
) -> dict:
    """Get expense summary grouped by category (store_id = NULL shows all stores)."""
    rows = await call(
        session,
        "fn_expense_summary",
        p_tenant_id=UUID(business_id),
        p_from=from_date,
        p_to=to_date,
        p_store_id=UUID(store_id) if store_id else None,
    )
    return {r["category"]: round(float(r["total"]), 2) for r in rows}


# ═══════════════════════════════════════════════════════════════════════════════
#  FINANCIAL DASHBOARD RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def financial_dashboard(
    session: AsyncSession,
    *,
    business_id: str,
    store_id: str | None = None,
) -> dict:
    """Get key financial metrics for the dashboard.

    Calls Postgres fn_financial_dashboard which aggregates:
    - cash_balance (store-filtered when store_id given)
    - outstanding_receivable (always business-wide)
    - outstanding_payable (always business-wide)
    - total_expenses_this_month (store-filtered when store_id given)
    - expense_by_category (store-filtered when store_id given)
    """
    rows = await call(
        session,
        "fn_financial_dashboard",
        p_tenant_id=UUID(business_id),
        p_store_id=UUID(store_id) if store_id else None,
    )
    return rows[0] if rows else {}
