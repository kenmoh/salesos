"""Pydantic schemas for accounting domain.

Two schema layers:
- Command schemas (with tenant_id) — used by bridge.py / service layer
- Request/Response schemas (without tenant_id) — used by route handlers
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
#  SERVICE-LAYER COMMAND SCHEMAS (used by bridge.py / service)
# ═══════════════════════════════════════════════════════════════════════════════


class ChartOfAccountCreateCommand(BaseModel):
    tenant_id: UUID
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    account_type: str = Field(..., min_length=1, max_length=30)
    parent_id: UUID | None = None


class ChartOfAccountResult(BaseModel):
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    account_type: str
    status: str


class JournalCreateCommand(BaseModel):
    tenant_id: UUID
    description: str = Field(..., min_length=1)
    reference_id: UUID | None = None
    reference_type: str | None = None
    actor_id: UUID | None = None
    correlation_id: str | None = None


class JournalEntryLine(BaseModel):
    account_id: UUID
    account_code: str = Field(..., min_length=1, max_length=20)
    debit: float = 0
    credit: float = 0
    description: str | None = None


class JournalPostCommand(BaseModel):
    journal_id: UUID
    actor_id: UUID | None = None
    correlation_id: str | None = None


class JournalResult(BaseModel):
    id: UUID
    tenant_id: UUID
    journal_number: str
    description: str
    status: str
    reference_id: str | None
    reference_type: str | None


class CommissionRecordCommand(BaseModel):
    tenant_id: UUID
    sale_id: UUID
    user_id: UUID
    amount: float
    rate_pct: float
    correlation_id: str | None = None


class CommissionResult(BaseModel):
    id: UUID
    tenant_id: UUID
    sale_id: UUID
    user_id: UUID
    amount: float
    rate_pct: float
    status: str


class AccountsReceivableCreateCommand(BaseModel):
    tenant_id: UUID
    invoice_id: UUID | None = None
    customer_id: UUID
    customer_name: str = Field(..., min_length=1, max_length=200)
    invoice_number: str = Field(..., min_length=1, max_length=50)
    amount: float = Field(..., gt=0)
    due_date: datetime


class AccountsReceivableResult(BaseModel):
    id: UUID
    tenant_id: UUID
    customer_id: UUID
    customer_name: str
    invoice_number: str
    amount: float
    amount_paid: float
    balance: float
    due_date: datetime
    status: str


class AccountsPayableCreateCommand(BaseModel):
    tenant_id: UUID
    bill_number: str = Field(..., min_length=1, max_length=50)
    vendor_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    amount: float = Field(..., gt=0)
    due_date: datetime


class AccountsPayableResult(BaseModel):
    id: UUID
    tenant_id: UUID
    bill_number: str
    vendor_name: str
    description: str | None
    amount: float
    amount_paid: float
    balance: float
    due_date: datetime
    status: str


class ExpenseCreateCommand(BaseModel):
    tenant_id: UUID
    category: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    expense_date: datetime
    account_id: UUID | None = None
    created_by: UUID
    vendor: str | None = None
    receipt_url: str | None = None


class ExpenseResult(BaseModel):
    id: UUID
    tenant_id: UUID
    expense_number: str
    category: str
    description: str
    amount: float
    vendor: str | None
    receipt_url: str | None
    expense_date: datetime
    account_id: UUID
    journal_id: UUID | None
    created_by: UUID


class PaymentRecordCommand(BaseModel):
    tenant_id: UUID
    amount: float = Field(..., gt=0)
    payment_date: datetime
    notes: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTE REQUEST SCHEMAS (tenant_id injected from auth context)
# ═══════════════════════════════════════════════════════════════════════════════


class CreateAccountRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    account_type: str = Field(..., min_length=1, max_length=30)
    parent_id: str | None = None


class JournalEntryLineRequest(BaseModel):
    account_id: str
    account_code: str = Field(..., min_length=1, max_length=20)
    debit: float = 0
    credit: float = 0
    description: str | None = None


class CreateJournalRequest(BaseModel):
    description: str = Field(..., min_length=1)
    entries: list[JournalEntryLineRequest]
    reference_id: str | None = None
    ref_type: str | None = None


class CreateReceivableRequest(BaseModel):
    customer_id: str
    customer_name: str = Field(..., min_length=1, max_length=200)
    invoice_number: str = Field(..., min_length=1, max_length=50)
    amount: float = Field(..., gt=0)
    due_date: str
    invoice_id: str | None = None


class RecordPaymentRequest(BaseModel):
    amount: float = Field(..., gt=0)
    payment_date: str
    notes: str | None = None


class CreatePayableRequest(BaseModel):
    bill_number: str = Field(..., min_length=1, max_length=50)
    vendor_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    amount: float = Field(..., gt=0)
    due_date: str


class CreateExpenseRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    expense_date: str
    vendor: str | None = None
    receipt_url: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTE RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class AccountResponse(BaseModel):
    id: str = ""
    tenant_id: str = ""
    code: str = ""
    name: str = ""
    account_type: str = ""
    status: str = "active"


class JournalCreatedResponse(BaseModel):
    journal_id: str = ""


class JournalListItem(BaseModel):
    id: str = ""
    journal_number: str = ""
    description: str = ""
    status: str = "draft"
    entry_count: int = 0
    total_debit: float = 0
    total_credit: float = 0
    created_at: str | None = None


class TrialBalanceItem(BaseModel):
    account_id: str = ""
    account_code: str = ""
    account_name: str = ""
    account_type: str = ""
    debit: float = 0
    credit: float = 0


class PnLLineItem(BaseModel):
    account_id: str = ""
    account_code: str = ""
    account_name: str = ""
    amount: float = 0


class ProfitAndLossResponse(BaseModel):
    revenue: list[PnLLineItem] = []
    expenses: list[PnLLineItem] = []
    total_revenue: float = 0
    total_expenses: float = 0


class BalanceSheetResponse(BaseModel):
    assets: list[PnLLineItem] = []
    liabilities: list[PnLLineItem] = []
    equity: list[PnLLineItem] = []
    total_assets: float = 0
    total_liabilities: float = 0
    total_equity: float = 0


class CashFlowResponse(BaseModel):
    inflows: list[PnLLineItem] = []
    outflows: list[PnLLineItem] = []
    net_cash_flow: float = 0


class ReceivableResponse(BaseModel):
    id: str = ""
    tenant_id: str = ""
    customer_id: str = ""
    customer_name: str = ""
    invoice_number: str = ""
    amount: float = 0
    amount_paid: float = 0
    balance: float = 0
    due_date: str = ""
    status: str = "pending"


class PayableResponse(BaseModel):
    id: str = ""
    tenant_id: str = ""
    bill_number: str = ""
    vendor_name: str = ""
    description: str | None = None
    amount: float = 0
    amount_paid: float = 0
    balance: float = 0
    due_date: str = ""
    status: str = "pending"


class ExpenseResponse(BaseModel):
    id: str = ""
    tenant_id: str = ""
    expense_number: str = ""
    category: str = ""
    description: str = ""
    amount: float = 0
    vendor: str | None = None
    receipt_url: str | None = None
    expense_date: str = ""
    account_id: str = ""
    journal_id: str | None = None
    created_by: str = ""


class FinancialDashboardResponse(BaseModel):
    cash_balance: float = 0
    outstanding_receivable: float = 0
    outstanding_payable: float = 0
    total_expenses_this_month: float = 0
    expense_by_category: dict[str, float] = {}
