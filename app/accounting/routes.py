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

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.core.approval import require_supervisor_approval
from app.core.dependencies import DbTenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from app.common.scope_cache import (
    ACCOUNTING,
    DOCUMENTS,
    REPORTS,
    SALES,
    TENANT_SCOPE,
    scope_bump,
    scope_read,
)
from . import rpc
from .models import Journal
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
    MoveToStoreRequest,
    PaymentResponse,
    PayableResponse,
    ProfitAndLossResponse,
    RecordPaymentRequest,
    ReceivableResponse,
    TaxCollectedItem,
    TaxOwedLine,
    TaxRemitRequest,
    TaxRemittanceItem,
    TaxRemitResponse,
    TaxesCollectedResponse,
    ToggleAccountStatusRequest,
    TrialBalanceItem,
    UpdateAccountRequest,
)

router = APIRouter(prefix="/accounting", tags=["Accounting"])


# The ledger screens re-run date-range sums on the whole journal table, so
# they ride the versioned cache; every posting here and at checkout bumps
# the scope, and the 45-60 s TTL bounds anything the bumps can miss.
def _acct_read(ctx, fetch, fields: dict, store_id: str | None = None, ttl: int = 45):
    return scope_read(
        tenant_id=ctx.user.business_id,
        store_id=store_id or TENANT_SCOPE,
        scope=ACCOUNTING,
        fields=fields,
        ttl=ttl,
        fetch=fetch,
    )


async def _log_account_action(ctx, action: str, details: dict, request: Request | None = None):
    """A curated tenant-visible audit row for the Activity Logs screen."""
    import json

    from app.identity.models import AuthAuditLog

    ctx.session.add(
        AuthAuditLog(
            user_id=UUID(ctx.user.user_id),
            tenant_id=UUID(ctx.user.business_id),
            action=action,
            ip_address=request.client.host if request and request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:500]
            or None
            if request
            else None,
            details=json.dumps(details),
        )
    )


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


def _required_store_id(store_id: str | None, context: str) -> str:
    """Creation postings must belong to one concrete store.

    An untagged row is invisible to store-filtered views -- every store
    screen filters on store_id, so a liability created across all stores
    never stops showing as owed in each store even after what the merchant
    already paid. All Stores is for viewing; anything that posts money
    against a store names that store.
    """
    value = _validated_store_id(store_id)
    if value is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Pass the store this {context} belongs to; tax and books "
                "post per store, All Stores is view-only"
            ),
        )
    return value


def _paginate_list(items: list, page: int | None, page_size: int | None) -> dict:
    """Wrap a full list in the paginated envelope.

    Without page/page_size the whole list is returned with meta describing
    it (page 1, page_size = list length), so callers that send no paging
    params keep receiving every row while still getting the pagination
    data. With them, the requested window is sliced.
    """
    total = len(items)
    if page is None or page_size is None:
        return paginated(items, total=total, page=1, page_size=max(total, 1))
    start = (page - 1) * page_size
    window = items[start : start + page_size]
    return paginated(window, total=total, page=page, page_size=page_size)


def _receivable_to_response(receivable) -> dict:
    """Serialise an Accounts Receivable row for the API."""
    return {
        "id": str(receivable.id),
        "tenant_id": str(receivable.tenant_id),
        "customer_id": str(receivable.customer_id) if receivable.customer_id else None,
        "customer_name": receivable.customer_name,
        "invoice_number": receivable.invoice_number,
        "amount": float(receivable.amount),
        "amount_paid": float(receivable.amount_paid),
        "balance": float(receivable.balance),
        "due_date": receivable.due_date.isoformat() if receivable.due_date else "",
        "status": receivable.status,
        "store_id": str(receivable.store_id) if receivable.store_id else None,
    }


def _payable_to_response(payable) -> dict:
    """Serialise an Accounts Payable row for the API."""
    return {
        "id": str(payable.id),
        "tenant_id": str(payable.tenant_id),
        "bill_number": payable.bill_number,
        "vendor_name": payable.vendor_name,
        "description": payable.description,
        "amount": float(payable.amount),
        "amount_paid": float(payable.amount_paid),
        "balance": float(payable.balance),
        "due_date": payable.due_date.isoformat() if payable.due_date else "",
        "status": payable.status,
        "store_id": str(payable.store_id) if payable.store_id else None,
    }


async def _record_store_correction(
    ctx,
    *,
    action: str,
    entity: str,
    entity_id: str,
    reference: str,
    from_store_id: str | None,
    to_store_id: str | None,
    supervisor_id: str | None,
    request: Request,
) -> None:
    """Log a store re-attribution to the auth audit trail.

    Added to the request's own session so the correction and its audit entry
    commit together: a rollback leaves no orphaned trail, and a success can
    never go unlogged. ``supervisor_id`` is null when the actor was allowed to
    correct without a second signature.
    """
    import json

    from app.identity.models import AuthAuditLog

    ctx.session.add(
        AuthAuditLog(
            user_id=UUID(ctx.user.user_id),
            tenant_id=UUID(ctx.user.business_id),
            action=action,
            ip_address=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            details=json.dumps(
                {
                    "entity": entity,
                    "entity_id": entity_id,
                    "reference": reference,
                    "from_store_id": from_store_id,
                    "to_store_id": to_store_id,
                    "actor_role": ctx.user.role,
                    "supervisor_id": supervisor_id,
                }
            ),
        )
    )


async def _post_payment_journal(
    ctx,
    *,
    amount: float,
    debit_code: str,
    credit_code: str,
    description: str,
    ref_id: str,
    ref_type: str,
    store_id: str | None = None,
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
        store_id=store_id,
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
    accounts = await _acct_read(
        ctx,
        fetch=lambda: rpc.list_accounts(
            session=ctx.session,
            business_id=ctx.user.business_id,
        ),
        fields={"accounts": 1},
        ttl=300,
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
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING,))
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
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING,))
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
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING,))
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
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING,))
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
        store_id=_required_store_id(payload.store_id, "journal"),
    )
    await _log_account_action(
        ctx,
        "journal_created",
        {
            "journal_id": str(journal_id),
            "description": payload.description,
            "total": float(sum(e.credit for e in payload.entries)),
            "entry_count": len(payload.entries),
        },
    )
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
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
    result = await _acct_read(
        ctx,
        fetch=lambda: rpc.list_journals(
            session=ctx.session,
            business_id=ctx.user.business_id,
            page=page,
            page_size=page_size,
            store_id=_validated_store_id(store_id),
        ),
        fields={"journals": 1, "page": page, "page_size": page_size, "store": store_id or ""},
        store_id=store_id,
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
    result = await _acct_read(
        ctx,
        fetch=lambda: rpc.trial_balance(
            session=ctx.session,
            business_id=ctx.user.business_id,
            as_at=as_at,
            store_id=_validated_store_id(store_id),
        ),
        fields={"trial_balance": 1, "as_at": as_at or "", "store": store_id or ""},
        store_id=store_id,
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
    result = await _acct_read(
        ctx,
        fetch=lambda: rpc.profit_and_loss(
            session=ctx.session,
            business_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
            store_id=_validated_store_id(store_id),
        ),
        fields={"pnl": 1, "from": from_date, "to": to_date, "store": store_id or ""},
        store_id=store_id,
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

    result = await _acct_read(
        ctx,
        fetch=lambda: get_balance_sheet(
            ctx.session, UUID(ctx.user.business_id), as_at_date,
            store_id=UUID(_validated_store_id(store_id)) if store_id else None,
        ),
        fields={"balance_sheet": 1, "as_at": as_at or "", "store": store_id or ""},
        store_id=store_id,
    )
    asset_accounts = [{"account_id": a.get("account_id", ""), "account_code": a["code"], "account_name": a["name"], "amount": a["balance"]} for a in result.get("asset_accounts", [])]
    liability_accounts = [{"account_id": a.get("account_id", ""), "account_code": a["code"], "account_name": a["name"], "amount": a["balance"]} for a in result.get("liability_accounts", [])]
    equity_accounts = [{"account_id": a.get("account_id", ""), "account_code": a["code"], "account_name": a["name"], "amount": a["balance"]} for a in result.get("equity_accounts", [])]
    return ok({
        "assets": asset_accounts,
        "liabilities": liability_accounts,
        "equity": equity_accounts,
        "total_assets": result.get("assets", 0),
        "total_liabilities": result.get("liabilities", 0),
        "total_equity": result.get("equity", 0),
        "capital": result.get("capital", 0),
        "current_earnings": result.get("current_earnings", 0),
        "total_revenue": result.get("revenue", 0),
        "total_expenses": result.get("expenses", 0),
        "balance_check": result.get("balance_check", 0),
        "receivable_subledger": result.get("receivable_subledger", 0),
        "receivable_control": result.get("receivable_control", 0),
        "receivable_difference": result.get("receivable_difference", 0),
        "payable_subledger": result.get("payable_subledger", 0),
        "payable_control": result.get("payable_control", 0),
        "payable_difference": result.get("payable_difference", 0),
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
    # A bare date means "the whole of that day": journal legs posted during
    # the to_date itself must counted, not silently dropped at midnight.
    if to_date and len(to_date) == 10 and to_dt is not None:
        to_dt = to_dt + timedelta(days=1) - timedelta(microseconds=1)
    from .repository import get_cash_flow

    result = await _acct_read(
        ctx,
        fetch=lambda: get_cash_flow(
            ctx.session, UUID(ctx.user.business_id), from_dt, to_dt,
            store_id=UUID(_validated_store_id(store_id)) if store_id else None,
        ),
        fields={"cash_flow": 1, "from": from_date or "", "to": to_date or "", "store": store_id or ""},
        store_id=store_id,
    )
    inflows = [{"account_id": i.get("journal_number", ""), "account_code": i.get("journal_number", ""), "account_name": i.get("description", ""), "amount": float(i.get("amount", 0))} for i in result.get("operating", {}).get("inflows", [])]
    outflows = [{"account_id": o.get("journal_number", ""), "account_code": o.get("journal_number", ""), "account_name": o.get("description", ""), "amount": float(o.get("amount", 0))} for o in result.get("operating", {}).get("outflows", [])]
    return ok({
        "inflows": inflows,
        "outflows": outflows,
        "net_cash_flow": result.get("net_cash_flow", 0),
    })


@router.get(
    "/taxes-collected",
    response_model=DataResponse[TaxesCollectedResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def taxes_collected(
    ctx: DbTenantDep,
    from_date: str | None = Query(None, description="Start date (default: 30 days ago)"),
    to_date: str | None = Query(None, description="End date (default: today)"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None
    if to_date and len(to_date) == 10 and to_dt is not None:
        to_dt = to_dt + timedelta(days=1) - timedelta(microseconds=1)
    from .repository import get_taxes_collected

    result = await _acct_read(
        ctx,
        fetch=lambda: get_taxes_collected(
            ctx.session, UUID(ctx.user.business_id), from_dt, to_dt,
            store_id=UUID(_validated_store_id(store_id)) if store_id else None,
            page=page,
            page_size=page_size,
        ),
        fields={"taxes_collected": 1, "from": from_date or "", "to": to_date or "", "store": store_id or "", "page": page, "page_size": page_size},
        store_id=store_id,
    )
    return ok({
        "taxes": result["taxes"],
        "total_collected": result["total_collected"],
        "zero_tax_sales": result["zero_tax_sales"],
        "items": result["items"],
        "page": result["page"],
        "page_size": result["page_size"],
        "total_items": result["total_items"],
        "has_more": result["has_more"],
    })


@router.get(
    "/taxes-owed",
    response_model=DataResponse[list[TaxOwedLine]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def taxes_owed(ctx: DbTenantDep, store_id: str | None = Query(None)):
    """What each tax account has collected, had remitted, and still owes.

    The three figures come straight off the ledger, so a remittance that
    already posted cannot be remitted a second time -- what it debited is
    subtracted from the balance the caller sees. ``store_id`` scopes the
    ledger sums to journals tagged with that store.
    """
    from .repository import get_taxes_owed

    lines = await _acct_read(
        ctx,
        fetch=lambda: get_taxes_owed(
            ctx.session,
            UUID(ctx.user.business_id),
            store_id=_validated_store_id(store_id),
        ),
        fields={"taxes_owed": 1, "store": store_id or ""},
        store_id=store_id,
    )
    return ok(lines)


@router.get(
    "/taxes-remit",
    response_model=PaginatedResponse[TaxRemittanceItem],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def tax_remit_history(
    ctx: DbTenantDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store_id: str | None = Query(None),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Past remittances (journals tagged ``tax_remittance``), newest first."""
    from .repository import get_tax_remittances

    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None
    if to_date and len(to_date) == 10 and to_dt is not None:
        to_dt = to_dt + timedelta(days=1) - timedelta(microseconds=1)

    result = await _acct_read(
        ctx,
        fetch=lambda: get_tax_remittances(
            ctx.session,
            UUID(ctx.user.business_id),
            page=page,
            page_size=page_size,
            store_id=_validated_store_id(store_id),
            from_date=from_dt,
            to_date=to_dt,
        ),
        fields={"tax_remits": 1, "page": page, "page_size": page_size, "store": store_id or "", "from": from_date or "", "to": to_date or ""},
        store_id=store_id,
    )
    return paginated(
        [TaxRemittanceItem(**r) for r in result["remittances"]],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


@router.post(
    "/taxes-remit",
    response_model=DataResponse[TaxRemitResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def remit_tax(payload: TaxRemitRequest, ctx: DbTenantDep, request: Request):
    """Remit collected tax: Dr the tax liability, Cr cash/bank.

    Refuses an amount above the balance still owed on the account, so a tax
    that was already remitted cannot be re-paid: what is left after earlier
    remittance journals is the ceiling. The posting is audited like the other
    corrections.
    """
    from datetime import UTC

    from .repository import _get_account_balance, get_taxes_owed

    if payload.method not in ("cash", "bank"):
        raise HTTPException(400, "method must be cash or bank")

    # Taxing operates per store: every remittance must name the store it
    # left from. An untagged remittance is invisible to store-scoped owed
    # balances (this sums journal.store_id), so stores would keep showing
    # tax as owed that the business already paid -- make it impossible
    # rather than letting the aggregate lie.
    store_uuid = _validated_store_id(payload.store_id)
    if store_uuid is None:
        raise HTTPException(
            400,
            "Pass the store the tax was remitted from; tax is remitted "
            "per store, so All Stores only views",
        )

    tenant_id = UUID(ctx.user.business_id)
    store_id = UUID(store_uuid)
    lines = await get_taxes_owed(ctx.session, tenant_id, store_id=store_id)
    line = next((l for l in lines if l["account_code"] == payload.account_code), None)
    if line is None:
        raise HTTPException(
            400,
            f"No tax is owed on account {payload.account_code}; pick a tax "
            "liability account",
        )
    owed = line["still_owed"]
    if payload.amount > owed + 0.009:
        raise HTTPException(
            400,
            f"Cannot remit more than still owed: {owed} remains on "
            f"{line['account_name']} ({payload.account_code}); earlier "
            "remittances are already subtracted",
        )

    credit_code = "1010" if payload.method == "bank" else "1000"
    credit_acct = await get_account_by_code(ctx.session, tenant_id, credit_code)
    if credit_acct is None and credit_code == "1010":
        credit_code = "1000"
        credit_acct = await get_account_by_code(ctx.session, tenant_id, credit_code)
    if credit_acct is None:
        raise HTTPException(400, "Chart of accounts has no cash or bank account")
    debit_acct = await get_account_by_code(ctx.session, tenant_id, payload.account_code)
    if debit_acct is None:
        raise HTTPException(400, f"Unknown account {payload.account_code}")

    description = f"Tax remittance: {line['account_name']} ({line['account_code']})"
    if payload.notes:
        description += f" — {payload.notes}"

    journal_id = await rpc.post_journal(
        session=ctx.session,
        business_id=ctx.user.business_id,
        user_id=ctx.user.user_id,
        description=description,
        entries=[
            {
                "account_id": str(debit_acct.id),
                "account_code": debit_acct.code,
                "debit": payload.amount,
                "credit": 0,
                "description": f"Tax remitted: {line['account_name']}",
                "type": _determine_entry_type(debit_acct.code),
            },
            {
                "account_id": str(credit_acct.id),
                "account_code": credit_acct.code,
                "debit": 0,
                "credit": payload.amount,
                "description": (
                    "Cash paid out" if payload.method == "cash" else "Bank transfer"
                ),
                "type": _determine_entry_type(credit_acct.code),
            },
        ],
        ref_type="tax_remittance",
        ref_id=None,
        store_id=store_uuid,
    )

    import json

    from app.identity.models import AuthAuditLog

    ctx.session.add(
        AuthAuditLog(
            user_id=UUID(ctx.user.user_id),
            tenant_id=tenant_id,
            action="tax_remittance",
            ip_address=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            details=json.dumps(
                {
                    "account_code": payload.account_code,
                    "amount": payload.amount,
                    "method": payload.method,
                    "payment_date": payload.payment_date,
                    "store_id": store_uuid,
                    "actor_role": ctx.user.role,
                }
            ),
        )
    )

    try:
        journal = (
            await ctx.session.execute(
                select(Journal.journal_number).where(Journal.id == UUID(journal_id))
            )
        ).scalar_one()
    except Exception:
        journal = None
    journal_number = journal or journal_id

    remaining = owed
    if debit_acct is not None:
        # The posting above ran in this transaction, so reading the ledger
        # now reports the balance the app will show on refresh.
        remaining = await _get_account_balance(
            ctx.session,
            debit_acct.id,
            debit_acct.account_type,
            datetime.now(UTC),
        )
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))

    return ok(
        TaxRemitResponse(
            account_code=payload.account_code,
            account_name=line["account_name"],
            amount=payload.amount,
            still_owed=round(remaining, 2),
            journal_number=journal_number,
        )
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS RECEIVABLE (AR) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/receivable",
    response_model=PaginatedResponse[ReceivableResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_receivable(
    ctx: DbTenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
    page: int | None = Query(None, ge=1, description="1-based page (omit to receive the full list)"),
    page_size: int | None = Query(None, ge=1, le=200, description="Rows per page"),
):
    ar_list = await _acct_read(
        ctx,
        fetch=lambda: rpc.list_accounts_receivable(
            session=ctx.session,
            business_id=ctx.user.business_id,
            status_filter=status,
            store_id=_validated_store_id(store_id),
        ),
        fields={"receivable": 1, "status": status or "", "store": store_id or ""},
        store_id=store_id,
    )
    return _paginate_list(ar_list, page, page_size)


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
        store_id=_required_store_id(payload.store_id, "receivable"),
        created_by=ctx.user.user_id,
    )
    await _log_account_action(
        ctx,
        "receivable_created",
        {
            "invoice_number": payload.invoice_number,
            "customer_name": payload.customer_name,
            "amount": float(payload.amount),
            "due_date": payload.due_date,
        },
    )
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
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
        recorded_by=ctx.user.user_id,
    )
    # The ledger row and the balance update happen together in one SQL
    # function; a refusal leaves both untouched, so stop before the journal.
    if error := result.get("error"):
        raise HTTPException(status_code=400, detail=error)
    await _post_payment_journal(
        ctx,
        amount=payload.amount,
        debit_code="1000",
        credit_code="1100",
        description=f"AR payment: {result.get('invoice_number') or ar_id}",
        ref_id=ar_id,
        ref_type="ar_payment",
        store_id=result.get("store_id"),
    )
    await _log_account_action(
        ctx,
        "ar_payment_received",
        {
            "invoice_number": result.get("invoice_number"),
            "amount": float(payload.amount),
            "payment_date": payload.payment_date,
        },
    )
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
    return ok(result)


@router.get(
    "/receivable/{ar_id}/payments",
    response_model=DataResponse[list[PaymentResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_ar_payments(ar_id: str, ctx: DbTenantDep):
    payments = await rpc.list_ar_payments(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ar_id=ar_id,
    )
    return ok(payments)


@router.patch(
    "/receivable/{ar_id}/store",
    response_model=DataResponse[ReceivableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def move_receivable_to_store(
    ar_id: str,
    payload: MoveToStoreRequest,
    ctx: DbTenantDep,
    request: Request,
):
    """Move a receivable to another store.

    A receivable raised against the wrong store is otherwise stranded: it is
    tagged with a store the user did not mean, so it disappears from the store
    they were working in. The journals booked against it move too, otherwise
    per-store cash and receivable figures would keep the original attribution
    and the two would disagree.

    Rewriting that attribution is a correction, not a create, so it needs a
    supervisor PIN unless the caller is an owner or already holds
    ``accounting:write``. Either way it is audited.
    """
    from sqlalchemy import select, update

    from app.accounting.models import AccountReceivable, Journal

    tenant_id = UUID(ctx.user.business_id)
    store_id = _validated_store_id(payload.store_id)
    supervisor_id = await require_supervisor_approval(
        ctx,
        permission="accounting:write",
        supervisor_pin=payload.supervisor_pin,
        request=request,
    )

    result = await ctx.session.execute(
        select(AccountReceivable).where(
            AccountReceivable.id == UUID(ar_id),
            AccountReceivable.tenant_id == tenant_id,
        )
    )
    receivable = result.scalar_one_or_none()
    if receivable is None:
        raise HTTPException(status_code=404, detail="Receivable not found")

    previous_store_id = str(receivable.store_id) if receivable.store_id else None
    receivable.store_id = UUID(store_id) if store_id else None
    await ctx.session.execute(
        update(Journal)
        .where(
            Journal.tenant_id == tenant_id,
            Journal.reference_id == UUID(ar_id),
            Journal.reference_type.in_(("receivable", "ar_payment")),
        )
        .values(store_id=UUID(store_id) if store_id else None)
    )
    await _record_store_correction(
        ctx,
        action="receivable_store_corrected",
        entity="receivable",
        entity_id=ar_id,
        reference=receivable.invoice_number,
        from_store_id=previous_store_id,
        to_store_id=store_id,
        supervisor_id=supervisor_id,
        request=request,
    )
    await ctx.session.flush()

    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
    return ok(_receivable_to_response(receivable))


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE (AP) ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/payable",
    response_model=PaginatedResponse[PayableResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_payable(
    ctx: DbTenantDep,
    status: str | None = Query(None, description="Filter by status: pending, overdue, partial, paid"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
    page: int | None = Query(None, ge=1, description="1-based page (omit to receive the full list)"),
    page_size: int | None = Query(None, ge=1, le=200, description="Rows per page"),
):
    ap_list = await _acct_read(
        ctx,
        fetch=lambda: rpc.list_accounts_payable(
            session=ctx.session,
            business_id=ctx.user.business_id,
            status_filter=status,
            store_id=_validated_store_id(store_id),
        ),
        fields={"payable": 1, "status": status or "", "store": store_id or ""},
        store_id=store_id,
    )
    return _paginate_list(ap_list, page, page_size)


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
        store_id=_required_store_id(payload.store_id, "payable"),
        expense_category=payload.expense_category,
        created_by=ctx.user.user_id,
        vendor_id=payload.vendor_id,
    )
    await _log_account_action(
        ctx,
        "payable_created",
        {
            "bill_number": payload.bill_number,
            "vendor_name": payload.vendor_name,
            "amount": float(payload.amount),
            "due_date": payload.due_date,
        },
    )
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
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
        recorded_by=ctx.user.user_id,
    )
    if error := result.get("error"):
        raise HTTPException(status_code=400, detail=error)
    await _post_payment_journal(
        ctx,
        amount=payload.amount,
        debit_code="2000",
        credit_code="1000",
        description=f"AP payment: {result.get('bill_number') or ap_id}",
        ref_id=ap_id,
        ref_type="ap_payment",
        store_id=result.get("store_id"),
    )
    await _log_account_action(
        ctx,
        "ap_payment_recorded",
        {
            "bill_number": result.get("bill_number"),
            "amount": float(payload.amount),
            "payment_date": payload.payment_date,
        },
    )
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
    return ok(result)


@router.get(
    "/payable/{ap_id}/payments",
    response_model=DataResponse[list[PaymentResponse]],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_ap_payments(ap_id: str, ctx: DbTenantDep):
    payments = await rpc.list_ap_payments(
        session=ctx.session,
        business_id=ctx.user.business_id,
        ap_id=ap_id,
    )
    return ok(payments)


@router.patch(
    "/payable/{ap_id}/store",
    response_model=DataResponse[PayableResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def move_payable_to_store(
    ap_id: str,
    payload: MoveToStoreRequest,
    ctx: DbTenantDep,
    request: Request,
):
    """Move a payable to another store, journals included.

    Mirrors the receivable endpoint: a bill raised against the wrong store is
    stranded there otherwise, and leaving its journal behind would keep
    per-store figures pointing at the store the user did not mean. Gated and
    audited the same way.
    """
    from sqlalchemy import select, update

    from app.accounting.models import AccountPayable, Journal

    tenant_id = UUID(ctx.user.business_id)
    store_id = _validated_store_id(payload.store_id)
    supervisor_id = await require_supervisor_approval(
        ctx,
        permission="accounting:write",
        supervisor_pin=payload.supervisor_pin,
        request=request,
    )

    result = await ctx.session.execute(
        select(AccountPayable).where(
            AccountPayable.id == UUID(ap_id),
            AccountPayable.tenant_id == tenant_id,
        )
    )
    payable = result.scalar_one_or_none()
    if payable is None:
        raise HTTPException(status_code=404, detail="Payable not found")

    previous_store_id = str(payable.store_id) if payable.store_id else None
    payable.store_id = UUID(store_id) if store_id else None
    await ctx.session.execute(
        update(Journal)
        .where(
            Journal.tenant_id == tenant_id,
            Journal.reference_id == UUID(ap_id),
            Journal.reference_type.in_(("payable", "ap_payment")),
        )
        .values(store_id=UUID(store_id) if store_id else None)
    )
    await _record_store_correction(
        ctx,
        action="payable_store_corrected",
        entity="payable",
        entity_id=ap_id,
        reference=payable.bill_number,
        from_store_id=previous_store_id,
        to_store_id=store_id,
        supervisor_id=supervisor_id,
        request=request,
    )
    await ctx.session.flush()

    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
    return ok(_payable_to_response(payable))


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


def _expense_to_response(expense) -> dict:
    return {
        "id": str(expense.id),
        "tenant_id": str(expense.tenant_id),
        "expense_number": expense.expense_number,
        "category": expense.category,
        "description": expense.description,
        "amount": float(expense.amount),
        "vendor": expense.vendor,
        "receipt_url": expense.receipt_url,
        "expense_date": expense.expense_date.isoformat() if expense.expense_date else "",
        "account_id": str(expense.account_id) if expense.account_id else "",
        "journal_id": str(expense.journal_id) if expense.journal_id else None,
        "created_by": str(expense.created_by) if expense.created_by else "",
        "store_id": str(expense.store_id) if expense.store_id else None,
    }


@router.patch(
    "/expenses/{expense_id}/store",
    response_model=DataResponse[ExpenseResponse],
    dependencies=[Depends(require_permission("accounting:write"))],
)
async def move_expense_to_store(
    expense_id: str,
    payload: MoveToStoreRequest,
    ctx: DbTenantDep,
    request: Request,
):
    """Move an expense to another store, journals included.

    Legacy expenses recorded before store tagging existed carry no store, so
    they vanish from a store-filtered list. This mirrors the receivable and
    payable endpoints: the expense is re-tagged together with its journal so
    per-store cash flow counts the outflow again. Gated and audited the same
    way.
    """
    from sqlalchemy import select, update

    from app.accounting.models import Expense, Journal

    tenant_id = UUID(ctx.user.business_id)
    store_id = _validated_store_id(payload.store_id)
    supervisor_id = await require_supervisor_approval(
        ctx,
        permission="accounting:write",
        supervisor_pin=payload.supervisor_pin,
        request=request,
    )

    result = await ctx.session.execute(
        select(Expense).where(
            Expense.id == UUID(expense_id),
            Expense.tenant_id == tenant_id,
        )
    )
    expense = result.scalar_one_or_none()
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")

    previous_store_id = str(expense.store_id) if expense.store_id else None
    expense.store_id = UUID(store_id) if store_id else None
    if expense.journal_id:
        await ctx.session.execute(
            update(Journal)
            .where(
                Journal.tenant_id == tenant_id,
                Journal.id == expense.journal_id,
            )
            .values(store_id=UUID(store_id) if store_id else None)
        )
    await _record_store_correction(
        ctx,
        action="expense_store_corrected",
        entity="expense",
        entity_id=expense_id,
        reference=expense.expense_number,
        from_store_id=previous_store_id,
        to_store_id=store_id,
        supervisor_id=supervisor_id,
        request=request,
    )
    await ctx.session.flush()

    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
    return ok(_expense_to_response(expense))


@router.get(
    "/expenses",
    response_model=PaginatedResponse[ExpenseResponse],
    dependencies=[Depends(require_permission("accounting:read"))],
)
async def list_expenses(
    ctx: DbTenantDep,
    category: str | None = Query(None, description="Filter by category"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    store_id: str | None = Query(None, description="Filter to one store (omit for all stores)"),
    page: int | None = Query(None, ge=1, description="1-based page (omit to receive the full list)"),
    page_size: int | None = Query(None, ge=1, le=200, description="Rows per page"),
):
    expenses = await _acct_read(
        ctx,
        fetch=lambda: rpc.list_expenses(
            session=ctx.session,
            business_id=ctx.user.business_id,
            category=category,
            from_date=from_date,
            to_date=to_date,
            store_id=_validated_store_id(store_id),
        ),
        fields={"expenses": 1, "category": category or "", "from": from_date or "", "to": to_date or "", "store": store_id or ""},
        store_id=store_id,
    )
    return _paginate_list(expenses, page, page_size)


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
        store_id=_required_store_id(payload.store_id, "expense"),
    )
    await _log_account_action(
        ctx,
        "expense_created",
        {
            "amount": float(payload.amount),
            "category": payload.category,
            "description": payload.description,
            "vendor": payload.vendor,
            "store_id": result.get("store_id"),
        },
    )
    await scope_bump(ctx.user.business_id, TENANT_SCOPE, (ACCOUNTING, REPORTS,))
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
    dashboard = await _acct_read(
        ctx,
        fetch=lambda: rpc.financial_dashboard(
            session=ctx.session,
            business_id=ctx.user.business_id,
            store_id=_validated_store_id(store_id),
        ),
        fields={"dashboard": 1, "store": store_id or ""},
        store_id=store_id,
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
