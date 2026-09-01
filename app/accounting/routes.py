"""Accounting HTTP endpoints for the mini accounting system.

This module defines the FastAPI routes for all accounting operations including:
- Chart of Accounts (COA) management
- Journal entry creation and listing
- Financial statements (Trial Balance, Profit & Loss, Balance Sheet, Cash Flow)
- Accounts Receivable (AR) management
- Accounts Payable (AP) management
- Expense tracking
- Financial dashboard
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from . import rpc
from .schemas import (
    AccountResponse,
    BalanceSheetResponse,
    CashFlowResponse,
    CreateAccountRequest,
    CreateExpenseRequest,
    CreateJournalRequest,
    CreatePayableRequest,
    CreateReceivableRequest,
    ExpenseResponse,
    FinancialDashboardResponse,
    JournalCreatedResponse,
    JournalListItem,
    PayableResponse,
    ProfitAndLossResponse,
    RecordPaymentRequest,
    ReceivableResponse,
    TrialBalanceItem,
)

router = APIRouter(prefix="/accounting", tags=["Accounting"])


# ═══════════════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS (COA) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/accounts",
    response_model=DataResponse[list[AccountResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_accounts(ctx: TenantDep):
    accounts = await rpc.list_accounts(
        session=ctx.session,
        business_id=ctx.user.business_id,
    )
    return ok(accounts)


@router.post(
    "/accounts",
    status_code=201,
    response_model=DataResponse[AccountResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_account(payload: CreateAccountRequest, ctx: TenantDep):
    result = await rpc.create_account(
        session=ctx.session,
        business_id=ctx.user.business_id,
        code=payload.code,
        name=payload.name,
        account_type=payload.account_type,
        parent_id=payload.parent_id,
    )
    return ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/journals",
    status_code=201,
    response_model=DataResponse[JournalCreatedResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_journal(payload: CreateJournalRequest, ctx: TenantDep):
    journal_id = await rpc.post_journal(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=ctx.user.user_id,
        description=payload.description,
        entries=[e.model_dump() for e in payload.entries],
        ref_id=payload.reference_id,
        ref_type=payload.ref_type,
    )
    return ok({"journal_id": journal_id})


@router.get(
    "/journals",
    response_model=PaginatedResponse[JournalListItem],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_journals(
    ctx: TenantDep,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
):
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
    response_model=DataResponse[list[TrialBalanceItem]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def trial_balance(
    ctx: TenantDep,
    as_at: str | None = Query(None, description="Date in YYYY-MM-DD format"),
):
    result = await rpc.trial_balance(
        session=ctx.session,
        business_id=ctx.user.business_id,
        as_at=as_at,
    )
    return ok(result)


@router.get(
    "/profit-and-loss",
    response_model=DataResponse[ProfitAndLossResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def profit_and_loss(
    ctx: TenantDep,
    from_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    to_date: str = Query(..., description="End date in YYYY-MM-DD format"),
):
    result = await rpc.profit_and_loss(
        session=ctx.session,
        business_id=ctx.user.business_id,
        from_date=from_date,
        to_date=to_date,
    )
    return ok(result)


@router.get(
    "/balance-sheet",
    response_model=DataResponse[BalanceSheetResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def balance_sheet(
    ctx: TenantDep,
    as_at: str | None = Query(None, description="Date in YYYY-MM-DD format (default: today)"),
):
    from uuid import UUID

    as_at_date = datetime.fromisoformat(as_at) if as_at else None
    from .repository import get_balance_sheet

    result = await get_balance_sheet(ctx.session, UUID(ctx.user.business_id), as_at_date)
    return ok(result)


@router.get(
    "/cash-flow",
    response_model=DataResponse[CashFlowResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def cash_flow(
    ctx: TenantDep,
    from_date: str | None = Query(None, description="Start date (default: 30 days ago)"),
    to_date: str | None = Query(None, description="End date (default: today)"),
):
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
    response_model=DataResponse[list[ReceivableResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_receivable(
    ctx: TenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
):
    ar_list = await rpc.list_accounts_receivable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        status_filter=status,
    )
    return ok(ar_list)


@router.post(
    "/receivable",
    status_code=201,
    response_model=DataResponse[ReceivableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_receivable(payload: CreateReceivableRequest, ctx: TenantDep):
    result = await rpc.create_accounts_receivable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        invoice_number=payload.invoice_number,
        amount=payload.amount,
        due_date=payload.due_date,
        invoice_id=payload.invoice_id,
    )
    return ok(result)


@router.post(
    "/receivable/{ar_id}/payment",
    response_model=DataResponse[ReceivableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def record_ar_payment(
    ar_id: str,
    payload: RecordPaymentRequest,
    ctx: TenantDep,
):
    result = await rpc.record_ar_payment(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ar_id=ar_id,
        amount=payload.amount,
        payment_date=payload.payment_date,
        notes=payload.notes,
    )
    return ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE (AP) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/payable",
    response_model=DataResponse[list[PayableResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_payable(
    ctx: TenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
):
    ap_list = await rpc.list_accounts_payable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        status_filter=status,
    )
    return ok(ap_list)


@router.post(
    "/payable",
    status_code=201,
    response_model=DataResponse[PayableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_payable(payload: CreatePayableRequest, ctx: TenantDep):
    result = await rpc.create_accounts_payable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        bill_number=payload.bill_number,
        vendor_name=payload.vendor_name,
        amount=payload.amount,
        due_date=payload.due_date,
        description=payload.description,
    )
    return ok(result)


@router.post(
    "/payable/{ap_id}/payment",
    response_model=DataResponse[PayableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def record_ap_payment(
    ap_id: str,
    payload: RecordPaymentRequest,
    ctx: TenantDep,
):
    result = await rpc.record_ap_payment(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ap_id=ap_id,
        amount=payload.amount,
        payment_date=payload.payment_date,
        notes=payload.notes,
    )
    return ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/expenses",
    response_model=DataResponse[list[ExpenseResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_expenses(
    ctx: TenantDep,
    category: str | None = Query(None, description="Filter by category"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
):
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
    response_model=DataResponse[ExpenseResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_expense(payload: CreateExpenseRequest, ctx: TenantDep):
    result = await rpc.create_expense(
        session=ctx.session,
        business_id=ctx.user.business_id,
        category=payload.category,
        description=payload.description,
        amount=payload.amount,
        expense_date=payload.expense_date,
        created_by=ctx.user.user_id,
        vendor=payload.vendor,
        receipt_url=payload.receipt_url,
    )
    return ok(result)


@router.get(
    "/expenses/summary",
    response_model=DataResponse[dict[str, float]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def expense_summary(
    ctx: TenantDep,
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
):
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
    response_model=DataResponse[FinancialDashboardResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def financial_dashboard(ctx: TenantDep):
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
    return ok({})


@router.post(
    "/commission/{sale_id}/record",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def record_commission(sale_id: str, ctx: TenantDep):
    return ok({"success": True})
