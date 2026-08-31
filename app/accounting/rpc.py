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
    return rows[0] if rows else {}


async def list_accounts(session: AsyncSession, *, business_id: str) -> list[dict]:
    """List all accounts in the Chart of Accounts.

    Calls Postgres fn_list_accounts which returns accounts ordered by code.
    """
    return await call(
        session,
        "fn_list_accounts",
        p_tenant_id=UUID(business_id),
    )


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
    )
    return str(journal_id)


async def list_journals(
    session: AsyncSession,
    *,
    business_id: str,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List journals with pagination.

    Calls Postgres fn_list_journals which returns paginated journals
    with entry counts.
    """
    rows = await call(
        session,
        "fn_list_journals",
        p_tenant_id=UUID(business_id),
        p_limit=page_size,
        p_offset=(page - 1) * page_size,
    )
    return {
        "items": rows,
        "total": rows[0].get("total_count") if rows else 0,
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
) -> list[dict]:
    """Get the Trial Balance as of a date.

    Calls Postgres fn_trial_balance which calculates all account balances
    in a single query.
    """
    return await call(
        session,
        "fn_trial_balance",
        p_tenant_id=UUID(business_id),
        p_at=as_at,
    )


async def profit_and_loss(
    session: AsyncSession,
    *,
    business_id: str,
    from_date: str,
    to_date: str,
) -> dict:
    """Get the Profit and Loss statement.

    Calls Postgres fn_profit_and_loss which aggregates revenue and expenses.
    """
    rows = await call(
        session,
        "fn_profit_and_loss",
        p_tenant_id=UUID(business_id),
        p_from=from_date,
        p_to=to_date,
    )
    revenue = [r for r in rows if r.get("account_type") == "revenue"]
    expenses = [r for r in rows if r.get("account_type") == "expense"]
    return {
        "revenue": revenue,
        "expenses": expenses,
        "total_revenue": sum(r.get("amount", 0) for r in revenue),
        "total_expenses": sum(e.get("amount", 0) for e in expenses),
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
) -> list[dict]:
    """List Accounts Receivable."""
    return await call(
        session,
        "fn_list_accounts_receivable",
        p_tenant_id=UUID(business_id),
        p_status=status_filter,
    )


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
) -> dict:
    """Create a new Accounts Receivable record."""
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
    )
    return rows[0] if rows else {}


async def record_ar_payment(
    session: AsyncSession,
    *,
    business_id: str,
    ar_id: str,
    amount: float,
    payment_date: str,
    notes: str | None = None,
) -> dict:
    """Record a payment against an AR record.

    Calls Postgres fn_record_ar_payment which:
    - Updates the AR record (amount_paid, balance, status)
    - Creates journal entries (Debit Cash, Credit AR)
    """
    rows = await call(
        session,
        "fn_record_ar_payment",
        p_tenant_id=UUID(business_id),
        p_ar_id=UUID(ar_id),
        p_amount=amount,
        p_payment_date=payment_date,
        p_notes=notes,
    )
    return rows[0] if rows else {}


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def list_accounts_payable(
    session: AsyncSession,
    *,
    business_id: str,
    status_filter: str | None = None,
) -> list[dict]:
    """List Accounts Payable."""
    return await call(
        session,
        "fn_list_accounts_payable",
        p_tenant_id=UUID(business_id),
        p_status=status_filter,
    )


async def create_accounts_payable(
    session: AsyncSession,
    *,
    business_id: str,
    bill_number: str,
    vendor_name: str,
    amount: float,
    due_date: str,
    description: str | None = None,
) -> dict:
    """Create a new Accounts Payable record."""
    rows = await call(
        session,
        "fn_create_accounts_payable",
        p_tenant_id=UUID(business_id),
        p_bill_number=bill_number,
        p_vendor_name=vendor_name,
        p_amount=amount,
        p_due_date=due_date,
        p_description=description,
    )
    return rows[0] if rows else {}


async def record_ap_payment(
    session: AsyncSession,
    *,
    business_id: str,
    ap_id: str,
    amount: float,
    payment_date: str,
    notes: str | None = None,
) -> dict:
    """Record a payment against an AP record.

    Calls Postgres fn_record_ap_payment which:
    - Updates the AP record (amount_paid, balance, status)
    - Creates journal entries (Debit AP, Credit Cash)
    """
    rows = await call(
        session,
        "fn_record_ap_payment",
        p_tenant_id=UUID(business_id),
        p_ap_id=UUID(ap_id),
        p_amount=amount,
        p_payment_date=payment_date,
        p_notes=notes,
    )
    return rows[0] if rows else {}


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
) -> list[dict]:
    """List expenses with optional filtering."""
    return await call(
        session,
        "fn_list_expenses",
        p_tenant_id=UUID(business_id),
        p_category=category,
        p_from=from_date,
        p_to=to_date,
    )


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
) -> dict:
    """Record a new business expense.

    Calls Postgres fn_create_expense which:
    - Auto-determines expense account from category
    - Creates the expense record
    - Creates journal entries (Debit Expense, Credit Cash)
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
    )
    return rows[0] if rows else {}


async def expense_summary(
    session: AsyncSession,
    *,
    business_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Get expense summary grouped by category."""
    rows = await call(
        session,
        "fn_expense_summary",
        p_tenant_id=UUID(business_id),
        p_from=from_date,
        p_to=to_date,
    )
    return {r["category"]: round(float(r["total"]), 2) for r in rows}


# ═══════════════════════════════════════════════════════════════════════════════
#  FINANCIAL DASHBOARD RPC
# ═══════════════════════════════════════════════════════════════════════════════


async def financial_dashboard(
    session: AsyncSession,
    *,
    business_id: str,
) -> dict:
    """Get key financial metrics for the dashboard.

    Calls Postgres fn_financial_dashboard which aggregates:
    - cash_balance
    - outstanding_receivable
    - outstanding_payable
    - total_expenses_this_month
    - expense_by_category
    """
    rows = await call(
        session,
        "fn_financial_dashboard",
        p_tenant_id=UUID(business_id),
    )
    return rows[0] if rows else {}
