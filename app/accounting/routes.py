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
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import DbTenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from . import rpc
from .repository import get_account_by_code
from .service import _determine_entry_type
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
    ToggleAccountStatusRequest,
    TrialBalanceItem,
    UpdateAccountRequest,
)

router = APIRouter(prefix="/accounting", tags=["Accounting"])


def _validated_store_id(store_id: str | None) -> str | None:
    """Validate an optional store_id (query param or payload field).

    Returns None when absent (business-wide / all stores) and raises 400
    for anything that is not a valid UUID. No per-role guard: omitting
    store_id already returns every store, and results are tenant-scoped.
    """
    if not store_id:
        return None
    try:
        UUID(store_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="store_id must be a valid UUID")
    return store_id


async def _post_payment_journal(
    ctx,
    *,
    amount: float,
    debit_code: str,
    credit_code: str,
    description: str,
    ref_id: str,
    ref_type: str,
) -> None:
    """Post the cash leg of an AR/AP payment (Dr cash / Cr receivable, etc.).

    ``fn_record_ar_payment``/``fn_record_ap_payment`` only update balances;
    without this journal the cash movement never reaches the ledger and the
    dashboard's cash figure drifts. Runs in the request's transaction, so a
    failure rolls back the balance update too.
    """
    from uuid import UUID

    tenant_id = UUID(ctx.user.business_id)
    debit_acct = await get_account_by_code(ctx.session, tenant_id, debit_code)
    credit_acct = await get_account_by_code(ctx.session, tenant_id, credit_code)
    if not debit_acct or not credit_acct:
        raise HTTPException(
            status_code=400,
            detail=f"Chart of accounts missing account {debit_code} or {credit_code}",
        )

    entries = [
        {
            "account_id": str(debit_acct.id),
            "account_code": debit_acct.code,
            "debit": amount,
            "credit": 0,
            "description": description,
            "type": _determine_entry_type(debit_acct.code),
        },
        {
            "account_id": str(credit_acct.id),
            "account_code": credit_acct.code,
            "debit": 0,
            "credit": amount,
            "description": description,
            "type": _determine_entry_type(credit_acct.code),
        },
    ]
    await rpc.post_journal(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=ctx.user.user_id,
        description=description,
        entries=entries,
        ref_id=ref_id,
        ref_type=ref_type,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS (COA) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/accounts",
    response_model=DataResponse[list[AccountResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_accounts(ctx: DbTenantDep):
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
async def create_account(payload: CreateAccountRequest, ctx: DbTenantDep):
    result = await rpc.create_account(
        session=ctx.session,
        business_id=ctx.user.business_id,
        code=payload.code,
        name=payload.name,
        account_type=payload.account_type,
        parent_id=payload.parent_id,
    )
    return ok(result)


@router.put(
    "/accounts/{account_id}",
    response_model=DataResponse[AccountResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def update_account(account_id: str, payload: UpdateAccountRequest, ctx: DbTenantDep):
    from .repository import update_account as repo_update
    from uuid import UUID

    account = await repo_update(
        ctx.session,
        account_id=UUID(account_id),
        tenant_id=UUID(ctx.user.business_id),
        name=payload.name,
    )
    if not account:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Account not found"})
    return ok({
        "id": str(account.id),
        "tenant_id": str(account.tenant_id),
        "code": account.code,
        "name": account.name,
        "account_type": account.account_type,
        "status": account.status,
    })


@router.patch(
    "/accounts/{account_id}/status",
    response_model=DataResponse[AccountResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def toggle_account_status(account_id: str, payload: ToggleAccountStatusRequest, ctx: DbTenantDep):
    from .repository import toggle_account_status as repo_toggle
    from uuid import UUID

    account = await repo_toggle(
        ctx.session,
        account_id=UUID(account_id),
        tenant_id=UUID(ctx.user.business_id),
        status=payload.status,
    )
    if not account:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Account not found"})
    return ok({
        "id": str(account.id),
        "tenant_id": str(account.tenant_id),
        "code": account.code,
        "name": account.name,
        "account_type": account.account_type,
        "status": account.status,
    })


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def delete_account(account_id: str, ctx: DbTenantDep):
    from .repository import delete_account as repo_delete
    from uuid import UUID

    deleted, error = await repo_delete(
        ctx.session,
        account_id=UUID(account_id),
        tenant_id=UUID(ctx.user.business_id),
    )
    if not deleted:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"detail": error})
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/journals",
    status_code=201,
    response_model=DataResponse[JournalCreatedResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_journal(payload: CreateJournalRequest, ctx: DbTenantDep):
    journal_id = await rpc.post_journal(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=ctx.user.user_id,
        description=payload.description,
        entries=[e.model_dump() for e in payload.entries],
        ref_id=payload.reference_id,
        ref_type=payload.ref_type,
        store_id=_validated_store_id(payload.store_id),
    )
    return ok({"journal_id": journal_id})


@router.get(
    "/journals",
    response_model=PaginatedResponse[JournalListItem],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_journals(
    ctx: DbTenantDep,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    result = await rpc.list_journals(
        session=ctx.session,
        business_id=ctx.user.business_id,
        page=page,
        page_size=page_size,
        store_id=_validated_store_id(store_id),
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
    ctx: DbTenantDep,
    as_at: str | None = Query(None, description="Date in YYYY-MM-DD format"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    result = await rpc.trial_balance(
        session=ctx.session,
        business_id=ctx.user.business_id,
        as_at=as_at,
        store_id=_validated_store_id(store_id),
    )
    return ok(result)


@router.get(
    "/profit-and-loss",
    response_model=DataResponse[ProfitAndLossResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def profit_and_loss(
    ctx: DbTenantDep,
    from_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    to_date: str = Query(..., description="End date in YYYY-MM-DD format"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    result = await rpc.profit_and_loss(
        session=ctx.session,
        business_id=ctx.user.business_id,
        from_date=from_date,
        to_date=to_date,
        store_id=_validated_store_id(store_id),
    )
    return ok(result)


@router.get(
    "/balance-sheet",
    response_model=DataResponse[BalanceSheetResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def balance_sheet(
    ctx: DbTenantDep,
    as_at: str | None = Query(None, description="Date in YYYY-MM-DD format (default: today)"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    as_at_date = datetime.fromisoformat(as_at) if as_at else None
    from .repository import get_balance_sheet

    result = await get_balance_sheet(
        ctx.session, UUID(ctx.user.business_id), as_at_date,
        store_id=UUID(_validated_store_id(store_id)) if store_id else None,
    )
    asset_accounts = [{"account_id": "", "account_code": a["code"], "account_name": a["name"], "amount": a["balance"]} for a in result.get("asset_accounts", [])]
    liability_accounts = [{"account_id": "", "account_code": a["code"], "account_name": a["name"], "amount": a["balance"]} for a in result.get("liability_accounts", [])]
    equity_accounts = [{"account_id": "", "account_code": a["code"], "account_name": a["name"], "amount": a["balance"]} for a in result.get("equity_accounts", [])]
    return ok({
        "assets": asset_accounts,
        "liabilities": liability_accounts,
        "equity": equity_accounts,
        "total_assets": result.get("assets", 0),
        "total_liabilities": result.get("liabilities", 0),
        "total_equity": result.get("equity", 0),
    })


@router.get(
    "/cash-flow",
    response_model=DataResponse[CashFlowResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def cash_flow(
    ctx: DbTenantDep,
    from_date: str | None = Query(None, description="Start date (default: 30 days ago)"),
    to_date: str | None = Query(None, description="End date (default: today)"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None
    from .repository import get_cash_flow

    result = await get_cash_flow(
        ctx.session, UUID(ctx.user.business_id), from_dt, to_dt,
        store_id=UUID(_validated_store_id(store_id)) if store_id else None,
    )
    inflows = [{"account_id": i.get("journal_number", ""), "account_code": i.get("journal_number", ""), "account_name": i.get("description", ""), "amount": float(i.get("amount", 0))} for i in result.get("operating", {}).get("inflows", [])]
    outflows = [{"account_id": o.get("journal_number", ""), "account_code": o.get("journal_number", ""), "account_name": o.get("description", ""), "amount": float(o.get("amount", 0))} for o in result.get("operating", {}).get("outflows", [])]
    return ok({
        "inflows": inflows,
        "outflows": outflows,
        "net_cash_flow": result.get("net_cash_flow", 0),
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS RECEIVABLE (AR) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/receivable",
    response_model=DataResponse[list[ReceivableResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_receivable(
    ctx: DbTenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    ar_list = await rpc.list_accounts_receivable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        status_filter=status,
        store_id=_validated_store_id(store_id),
    )
    return ok(ar_list)


@router.post(
    "/receivable",
    status_code=201,
    response_model=DataResponse[ReceivableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_receivable(payload: CreateReceivableRequest, ctx: DbTenantDep):
    result = await rpc.create_accounts_receivable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        invoice_number=payload.invoice_number,
        amount=payload.amount,
        due_date=payload.due_date,
        invoice_id=payload.invoice_id,
        store_id=_validated_store_id(payload.store_id),
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
    ctx: DbTenantDep,
):
    result = await rpc.record_ar_payment(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ar_id=ar_id,
        amount=payload.amount,
        payment_date=payload.payment_date,
        notes=payload.notes,
    )
    await _post_payment_journal(
        ctx,
        amount=payload.amount,
        debit_code="1000",
        credit_code="1100",
        description=f"AR payment: {result.get('invoice_number') or ar_id}",
        ref_id=ar_id,
        ref_type="ar_payment",
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
    ctx: DbTenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    ap_list = await rpc.list_accounts_payable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        status_filter=status,
        store_id=_validated_store_id(store_id),
    )
    return ok(ap_list)


@router.post(
    "/payable",
    status_code=201,
    response_model=DataResponse[PayableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_payable(payload: CreatePayableRequest, ctx: DbTenantDep):
    result = await rpc.create_accounts_payable(
        session=ctx.session,
        business_id=ctx.user.business_id,
        bill_number=payload.bill_number,
        vendor_name=payload.vendor_name,
        amount=payload.amount,
        due_date=payload.due_date,
        description=payload.description,
        store_id=_validated_store_id(payload.store_id),
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
    ctx: DbTenantDep,
):
    result = await rpc.record_ap_payment(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ap_id=ap_id,
        amount=payload.amount,
        payment_date=payload.payment_date,
        notes=payload.notes,
    )
    await _post_payment_journal(
        ctx,
        amount=payload.amount,
        debit_code="2000",
        credit_code="1000",
        description=f"AP payment: {result.get('bill_number') or ap_id}",
        ref_id=ap_id,
        ref_type="ap_payment",
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
    ctx: DbTenantDep,
    category: str | None = Query(None, description="Filter by category"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    expenses = await rpc.list_expenses(
        session=ctx.session,
        business_id=ctx.user.business_id,
        category=category,
        from_date=from_date,
        to_date=to_date,
        store_id=_validated_store_id(store_id),
    )
    return ok(expenses)


@router.post(
    "/expenses",
    status_code=201,
    response_model=DataResponse[ExpenseResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def create_expense(payload: CreateExpenseRequest, ctx: DbTenantDep):
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
        store_id=_validated_store_id(payload.store_id),
    )
    return ok(result)


@router.get(
    "/expenses/summary",
    response_model=DataResponse[dict[str, float]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def expense_summary(
    ctx: DbTenantDep,
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    summary = await rpc.expense_summary(
        session=ctx.session,
        business_id=ctx.user.business_id,
        from_date=from_date,
        to_date=to_date,
        store_id=_validated_store_id(store_id),
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
async def financial_dashboard(
    ctx: DbTenantDep,
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
):
    dashboard = await rpc.financial_dashboard(
        session=ctx.session,
        business_id=ctx.user.business_id,
        store_id=_validated_store_id(store_id),
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
async def commission(ctx: DbTenantDep):
    return ok({})


@router.post(
    "/commission/{sale_id}/record",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def record_commission(sale_id: str, ctx: DbTenantDep):
    return ok({"success": True})
