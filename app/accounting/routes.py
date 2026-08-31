"""Accounting HTTP endpoints for the mini accounting system.

This module defines the FastAPI routes for all accounting operations including:
- Chart of Accounts (COA) management
- Journal entry creation and listing
- Financial statements (Trial Balance, Profit & Loss, Balance Sheet, Cash Flow)
- Accounts Receivable (AR) management
- Accounts Payable (AP) management
- Expense tracking
- Financial dashboard

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- AP: Accounts Payable -- money the business OWES to vendors/suppliers.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- P&L: Profit and Loss statement -- shows revenue, expenses, and net profit.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- JWT: JSON Web Token -- used for authentication and authorization.
- RBAC: Role-Based Access Control -- permission system restricting endpoint access.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from . import rpc

router = APIRouter(prefix="/accounting", tags=["Accounting"])


# ═══════════════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS (COA) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/accounts",
    response_model=DataResponse[list[dict]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_accounts(ctx: TenantDep):
    """List all accounts in the Chart of Accounts for this tenant.

    Returns accounts ordered by code (1xxx assets first, then 2xxx liabilities, etc.).
    Requires accounting:read permission.
    """
    accounts = await rpc.list_accounts(
        session=ctx.session,
        business_id=ctx.user.business_id,
    )
    return ok(accounts)


@router.post(
    "/accounts",
    status_code=201,
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_account(payload: dict, ctx: TenantDep):
    """Create a new account in the Chart of Accounts.

    Account codes must be unique per tenant. Valid account types:
    asset, liability, equity, revenue, expense.
    Requires accounting:write permission.
    """
    result = await rpc.create_account(
        session=ctx.session,
        business_id=ctx.user.business_id,
        code=payload["code"],
        name=payload["name"],
        account_type=payload["account_type"],
        parent_id=payload.get("parent_id"),
    )
    return ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/journals",
    status_code=201,
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_journal(payload: dict, ctx: TenantDep):
    """Create a new journal with balanced debit/credit entries.

    Total debits must equal total credits. Journal starts in "draft" status,
    then is immediately posted.

    Requires accounting:write permission.

    Example payload:
        {
            "description": "Sale INV-20260001",
            "entries": [
                {"account_id": "...", "account_code": "1000", "debit": 50000, "credit": 0},
                {"account_id": "...", "account_code": "4000", "debit": 0, "credit": 50000}
            ],
            "reference_id": null,
            "ref_type": null
        }
    """
    journal_id = await rpc.post_journal(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=ctx.user.user_id,
        description=payload["description"],
        entries=payload["entries"],
        ref_id=payload.get("reference_id"),
        ref_type=payload.get("ref_type"),
    )
    return ok({"journal_id": journal_id})


@router.get(
    "/journals",
    response_model=PaginatedResponse[dict],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_journals(
    ctx: TenantDep,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
):
    """List journals with pagination, ordered by creation date descending.

    Requires accounting:read permission.
    """
    result = await rpc.list_journals(
        session=ctx.session,
        business_id=ctx.user.business_id,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  FINANCIAL STATEMENTS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/trial-balance",
    response_model=DataResponse[list[dict]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def trial_balance(
    ctx: TenantDep,
    as_at: str | None = Query(None, description="Date in YYYY-MM-DD format"),
):
    """Get the Trial Balance as of a specific date.

    The Trial Balance lists all accounts and their balances. Total debits
    must equal total credits. Requires accounting:read permission.
    """
    result = await rpc.trial_balance(
        session=ctx.session,
        business_id=ctx.user.business_id,
        as_at=as_at,
    )
    return ok(result)


@router.get(
    "/profit-and-loss",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def profit_and_loss(
    ctx: TenantDep,
    from_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    to_date: str = Query(..., description="End date in YYYY-MM-DD format"),
):
    """Get the Profit and Loss (P&L) statement for a date range.

    Shows revenue, cost of goods sold (COGS), gross profit, expenses,
    and net profit. Requires accounting:read permission.
    """
    result = await rpc.profit_and_loss(
        session=ctx.session,
        business_id=ctx.user.business_id,
        from_date=from_date,
        to_date=to_date,
    )
    return ok(result)


@router.get(
    "/balance-sheet",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def balance_sheet(
    ctx: TenantDep,
    as_at: str | None = Query(None, description="Date in YYYY-MM-DD format (default: today)"),
):
    """Get the Balance Sheet as of a specific date.

    The Balance Sheet shows the business's financial position:
        Assets = Liabilities + Equity

    Requires accounting:read permission.
    """
    from uuid import UUID

    as_at_date = datetime.fromisoformat(as_at) if as_at else None
    from .repository import get_balance_sheet

    result = await get_balance_sheet(ctx.session, UUID(ctx.user.business_id), as_at_date)
    return ok(result)


@router.get(
    "/cash-flow",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def cash_flow(
    ctx: TenantDep,
    from_date: str | None = Query(None, description="Start date (default: 30 days ago)"),
    to_date: str | None = Query(None, description="End date (default: today)"),
):
    """Get the Cash Flow Statement for a date range.

    Shows cash inflows and outflows from operating activities.
    Requires accounting:read permission.
    """
    from uuid import UUID

    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None
    from .repository import get_cash_flow

    result = await get_cash_flow(ctx.session, UUID(ctx.user.business_id), from_dt, to_dt)
    return ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS RECEIVABLE (AR) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/receivable",
    response_model=DataResponse[list[dict]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_receivable(
    ctx: TenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
):
    """List Accounts Receivable (who owes us money).

    Optionally filter by status. Returns AR records ordered by due_date ascending.
    Requires accounting:read permission.
    """
    ar_list = await rpc.list_accounts_receivable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        status_filter=status,
    )
    return ok(ar_list)


@router.post(
    "/receivable",
    status_code=201,
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_receivable(
    payload: dict,
    ctx: TenantDep,
):
    """Create a new Accounts Receivable record (invoice).

    Tracks money owed by a customer. Requires accounting:write permission.
    """
    result = await rpc.create_accounts_receivable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        customer_id=payload["customer_id"],
        customer_name=payload["customer_name"],
        invoice_number=payload["invoice_number"],
        amount=payload["amount"],
        due_date=payload["due_date"],
        invoice_id=payload.get("invoice_id"),
    )
    return ok(result)


@router.post(
    "/receivable/{ar_id}/payment",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def record_ar_payment(
    ar_id: str,
    payload: dict,
    ctx: TenantDep,
):
    """Record a payment against an Accounts Receivable record.

    When a customer pays their invoice, this endpoint:
    1. Updates the AR record (amount_paid, balance, status)
    2. Creates journal entries (Debit Cash, Credit AR)
    Requires accounting:write permission.
    """
    result = await rpc.record_ar_payment(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ar_id=ar_id,
        amount=payload["amount"],
        payment_date=payload["payment_date"],
        notes=payload.get("notes"),
    )
    return ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE (AP) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/payable",
    response_model=DataResponse[list[dict]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_payable(
    ctx: TenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
):
    """List Accounts Payable (who we owe money to).

    Optionally filter by status. Returns AP records ordered by due_date ascending.
    Requires accounting:read permission.
    """
    ap_list = await rpc.list_accounts_payable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        status_filter=status,
    )
    return ok(ap_list)


@router.post(
    "/payable",
    status_code=201,
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_payable(
    payload: dict,
    ctx: TenantDep,
):
    """Create a new Accounts Payable record (bill).

    Tracks money owed to a vendor/supplier. Requires accounting:write permission.
    """
    result = await rpc.create_accounts_payable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        bill_number=payload["bill_number"],
        vendor_name=payload["vendor_name"],
        amount=payload["amount"],
        due_date=payload["due_date"],
        description=payload.get("description"),
    )
    return ok(result)


@router.post(
    "/payable/{ap_id}/payment",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def record_ap_payment(
    ap_id: str,
    payload: dict,
    ctx: TenantDep,
):
    """Record a payment against an Accounts Payable record.

    When the business pays a vendor, this endpoint:
    1. Updates the AP record (amount_paid, balance, status)
    2. Creates journal entries (Debit AP, Credit Cash)
    Requires accounting:write permission.
    """
    result = await rpc.record_ap_payment(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ap_id=ap_id,
        amount=payload["amount"],
        payment_date=payload["payment_date"],
        notes=payload.get("notes"),
    )
    return ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/expenses",
    response_model=DataResponse[list[dict]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_expenses(
    ctx: TenantDep,
    category: str | None = Query(None, description="Filter by category"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
):
    """List expenses with optional filtering by category and date range.

    Returns expenses ordered by expense_date descending (newest first).
    Requires accounting:read permission.
    """
    expenses = await rpc.list_expenses(
        session=ctx.session,
        business_id=ctx.user.business_id,
        category=category,
        from_date=from_date,
        to_date=to_date,
    )
    return ok(expenses)


@router.post(
    "/expenses",
    status_code=201,
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_expense(
    payload: dict,
    ctx: TenantDep,
):
    """Record a new business expense.

    Valid categories: rent, utilities, salaries, supplies, transport,
    marketing, bank_charges, phone_internet, maintenance, insurance, taxes, other.

    The expense account is automatically determined from the category:
        rent -> 5100 (Rent), utilities -> 5200 (Utilities), etc.

    Automatically creates journal entries (Debit Expense, Credit Cash).
    Requires accounting:write permission.

    Example payload for NEPA bill:
        {
            "category": "utilities",
            "description": "NEPA bill - September 2026",
            "amount": 3000,
            "expense_date": "2026-09-01T00:00:00Z",
            "vendor": "Ikeja Electric"
        }
    """
    result = await rpc.create_expense(
        session=ctx.session,
        business_id=ctx.user.business_id,
        category=payload["category"],
        description=payload["description"],
        amount=payload["amount"],
        expense_date=payload["expense_date"],
        created_by=ctx.user.user_id,
        vendor=payload.get("vendor"),
        receipt_url=payload.get("receipt_url"),
    )
    return ok(result)


@router.get(
    "/expenses/summary",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def expense_summary(
    ctx: TenantDep,
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get expense summary grouped by category.

    Returns a dictionary mapping category names to total amounts.
    Useful for expense breakdown charts. Requires accounting:read permission.
    """
    summary = await rpc.expense_summary(
        session=ctx.session,
        business_id=ctx.user.business_id,
        from_date=from_date,
        to_date=to_date,
    )
    return ok(summary)


# ═══════════════════════════════════════════════════════════════════════════════
#  FINANCIAL DASHBOARD ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/dashboard",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def financial_dashboard(ctx: TenantDep):
    """Get key financial metrics for the dashboard.

    Returns:
        - cash_balance: Current cash balance in NGN
        - outstanding_receivable: Total AR (money owed by customers)
        - outstanding_payable: Total AP (money owed to vendors)
        - total_expenses_this_month: Total expenses for current month
        - expense_by_category: Expense breakdown by category

    Requires accounting:read permission.
    """
    dashboard = await rpc.financial_dashboard(
        session=ctx.session,
        business_id=ctx.user.business_id,
    )
    return ok(dashboard)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMISSION ENDPOINTS (LEGACY)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/commission",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def commission(ctx: TenantDep):
    """Get commission summary (legacy endpoint).

    Requires accounting:read permission.
    """
    return ok({})


@router.post(
    "/commission/{sale_id}/record",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def record_commission(sale_id: str, ctx: TenantDep):
    """Record a sales commission (legacy endpoint).

    Requires accounting:write permission.
    """
    return ok({"success": True})
