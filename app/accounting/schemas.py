"""Pydantic schemas for accounting domain commands and results.

This module defines the input (Command) and output (Result) schemas used by
the accounting service layer. Commands represent user-initiated actions that
trigger business logic. Results represent the data returned after an operation.

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- AP: Accounts Payable -- money the business OWES to vendors/suppliers.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- JE: Journal Entry -- a record of a financial transaction (debit/credit line).
- FK: Foreign Key -- a reference from one table to another.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS (COA) SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class ChartOfAccountCreateCommand(BaseModel):
    """Command to create a new account in the Chart of Accounts.

    Attributes:
        tenant_id: The business tenant this account belongs to.
        code: The account number/code (e.g., "1000" for Cash). Must be unique per tenant.
        name: Human-readable account name (e.g., "Cash", "Sales Revenue").
        account_type: The category of this account. One of: "asset", "liability",
            "equity", "revenue", "expense".
        parent_id: Optional parent account UUID for hierarchical grouping.
    """

    tenant_id: UUID
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    account_type: str = Field(..., min_length=1, max_length=30)
    parent_id: UUID | None = None


class ChartOfAccountResult(BaseModel):
    """Result returned after creating or querying a Chart of Accounts entry.

    Attributes:
        id: Unique identifier for this account (UUID).
        tenant_id: The business tenant this account belongs to.
        code: The account number/code.
        name: Human-readable account name.
        account_type: The category of this account.
        status: Account status ("active" or "inactive").
    """

    id: UUID
    tenant_id: UUID
    code: str
    name: str
    account_type: str
    status: str


# ═══════════════════════════════════════════════════════════════════════════════
#  JOURNAL SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class JournalCreateCommand(BaseModel):
    """Command to create a new journal (header record for a financial transaction).

    A journal contains one or more journal entries (debit/credit lines) that
    must balance (total debits == total credits).

    Attributes:
        tenant_id: The business tenant this journal belongs to.
        description: Human-readable description of the transaction.
        reference_id: Optional UUID linking to the source entity (e.g., sale_id).
        reference_type: The type of the referenced entity (e.g., "sale", "payment").
        actor_id: UUID of the user creating this journal.
        correlation_id: Optional request correlation ID for distributed tracing.
    """

    tenant_id: UUID
    description: str = Field(..., min_length=1)
    reference_id: UUID | None = None
    reference_type: str | None = None
    actor_id: UUID | None = None
    correlation_id: str | None = None


class JournalEntryLine(BaseModel):
    """A single debit or credit line within a journal entry.

    In double-entry bookkeeping, each journal contains at least two lines:
    one debit and one credit. The total debits must equal total credits.

    Attributes:
        account_id: Foreign key to the ChartOfAccount being debited/credited.
        account_code: The account code (e.g., "1000" for Cash). Denormalized for display.
        debit: The debit amount in NGN. Must be >= 0. Zero means credit-only line.
        credit: The credit amount in NGN. Must be >= 0. Zero means debit-only line.
        description: Optional line-item description.
    """

    account_id: UUID
    account_code: str = Field(..., min_length=1, max_length=20)
    debit: float = 0
    credit: float = 0
    description: str | None = None


class JournalPostCommand(BaseModel):
    """Command to post (finalize) a journal, making it immutable.

    Posting a journal transitions its status from "draft" to "posted" and
    records who posted it and when. Posted journals cannot be edited or deleted.

    Attributes:
        journal_id: The UUID of the journal to post.
        actor_id: UUID of the user posting this journal.
        correlation_id: Optional request correlation ID for distributed tracing.
    """

    journal_id: UUID
    actor_id: UUID | None = None
    correlation_id: str | None = None


class JournalResult(BaseModel):
    """Result returned after creating or querying a journal.

    Attributes:
        id: Unique identifier for this journal (UUID).
        tenant_id: The business tenant this journal belongs to.
        journal_number: Auto-generated journal number (format: JRN-YYYYMMDD-XXXXXXXX).
        description: Human-readable description of the transaction.
        status: Journal status ("draft" or "posted").
        reference_id: Optional UUID of the referenced entity.
        reference_type: The type of the referenced entity.
    """

    id: UUID
    tenant_id: UUID
    journal_number: str
    description: str
    status: str
    reference_id: str | None
    reference_type: str | None


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMISSION SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class CommissionRecordCommand(BaseModel):
    """Command to record a sales commission for an employee/cashier.

    Commission amount is calculated as: sale_total * (rate_pct / 100).

    Attributes:
        tenant_id: The business tenant this commission belongs to.
        sale_id: The UUID of the Sale that earned this commission.
        user_id: The UUID of the User (employee/cashier) who earned it.
        amount: The commission amount in NGN.
        rate_pct: The commission rate as a percentage (e.g., 5.00 means 5%).
        correlation_id: Optional request correlation ID for distributed tracing.
    """

    tenant_id: UUID
    sale_id: UUID
    user_id: UUID
    amount: float
    rate_pct: float
    correlation_id: str | None = None


class CommissionResult(BaseModel):
    """Result returned after recording or querying a commission.

    Attributes:
        id: Unique identifier for this commission record (UUID).
        tenant_id: The business tenant this commission belongs to.
        sale_id: The UUID of the Sale that earned this commission.
        user_id: The UUID of the User who earned it.
        amount: The commission amount in NGN.
        rate_pct: The commission rate as a percentage.
        status: Payment status ("pending" or "paid").
    """

    id: UUID
    tenant_id: UUID
    sale_id: UUID
    user_id: UUID
    amount: float
    rate_pct: float
    status: str


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS RECEIVABLE (AR) SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class AccountsReceivableCreateCommand(BaseModel):
    """Command to create a new Accounts Receivable (AR) record.

    AR is created when an invoice is issued to a customer or a sale is made
    on credit. The AR tracks how much the customer owes and when payment is due.

    Attributes:
        tenant_id: The business tenant this receivable belongs to.
        invoice_id: Optional UUID of the Document (invoice) that created this AR.
        customer_id: The UUID of the Customer who owes the money.
        customer_name: The customer's name (denormalized for display).
        invoice_number: The invoice number (e.g., "INV-20260001").
        amount: The total invoice amount in NGN.
        due_date: The date by which payment is expected.
    """

    tenant_id: UUID
    invoice_id: UUID | None = None
    customer_id: UUID
    customer_name: str = Field(..., min_length=1, max_length=200)
    invoice_number: str = Field(..., min_length=1, max_length=50)
    amount: float = Field(..., gt=0)
    due_date: datetime


class AccountsReceivableResult(BaseModel):
    """Result returned after creating or querying an AR record.

    Attributes:
        id: Unique identifier for this receivable (UUID).
        tenant_id: The business tenant this receivable belongs to.
        customer_id: The UUID of the Customer who owes the money.
        customer_name: The customer's name.
        invoice_number: The invoice number.
        amount: The total invoice amount in NGN.
        amount_paid: How much the customer has paid so far in NGN.
        balance: The remaining amount owed (amount - amount_paid) in NGN.
        due_date: The date by which payment is expected.
        status: Payment status ("pending", "overdue", "partial", "paid").
    """

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


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE (AP) SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class AccountsPayableCreateCommand(BaseModel):
    """Command to create a new Accounts Payable (AP) record.

    AP is created when a bill is received from a vendor or a purchase order
    is fulfilled but not yet paid. The AP tracks how much the business owes
    and when payment should be made.

    Attributes:
        tenant_id: The business tenant this payable belongs to.
        bill_number: The bill/invoice number from the vendor.
        vendor_name: The name of the vendor or supplier owed.
        description: Description of what was purchased.
        amount: The total bill amount in NGN.
        due_date: The date by which payment should be made.
    """

    tenant_id: UUID
    bill_number: str = Field(..., min_length=1, max_length=50)
    vendor_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    amount: float = Field(..., gt=0)
    due_date: datetime


class AccountsPayableResult(BaseModel):
    """Result returned after creating or querying an AP record.

    Attributes:
        id: Unique identifier for this payable (UUID).
        tenant_id: The business tenant this payable belongs to.
        bill_number: The bill/invoice number from the vendor.
        vendor_name: The name of the vendor or supplier.
        description: Description of what was purchased.
        amount: The total bill amount in NGN.
        amount_paid: How much has been paid so far in NGN.
        balance: The remaining amount owed (amount - amount_paid) in NGN.
        due_date: The date by which payment should be made.
        status: Payment status ("pending", "overdue", "partial", "paid").
    """

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


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPENSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class ExpenseCreateCommand(BaseModel):
    """Command to record a new business expense.

    When an expense is recorded, a journal entry is automatically created:
    Debit Expense Account, Credit Cash Account.

    The expense account is automatically determined from the category:
        rent -> 5100, utilities -> 5200, salaries -> 5300, etc.

    Valid expense categories:
        "rent", "utilities", "salaries", "supplies", "transport",
        "marketing", "bank_charges", "phone_internet", "maintenance",
        "insurance", "taxes", "other"

    Attributes:
        tenant_id: The business tenant this expense belongs to.
        category: The expense category (one of the valid categories above).
        description: Detailed description of the expense.
        amount: The expense amount in NGN.
        expense_date: The date the expense was incurred.
        account_id: Optional UUID of the expense account. If not provided,
            automatically determined from the category.
        created_by: The UUID of the User recording this expense.
        vendor: Optional name of the vendor/supplier.
        receipt_url: Optional URL to an uploaded receipt image.
    """

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
    """Result returned after creating or querying an expense.

    Attributes:
        id: Unique identifier for this expense (UUID).
        tenant_id: The business tenant this expense belongs to.
        expense_number: Auto-generated expense number (format: EXP-YYYYMMDD-XXXXXXXX).
        category: The expense category.
        description: Detailed description of the expense.
        amount: The expense amount in NGN.
        vendor: Optional name of the vendor/supplier.
        receipt_url: Optional URL to an uploaded receipt image.
        expense_date: The date the expense was incurred.
        account_id: The UUID of the expense account in the Chart of Accounts.
        journal_id: Optional UUID of the automatically created Journal record.
        created_by: The UUID of the User who recorded this expense.
    """

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


# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENT RECORDING SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class PaymentRecordCommand(BaseModel):
    """Command to record a payment against an AR or AP record.

    When a payment is recorded:
        - For AR: Debit Cash, Credit Receivable (customer paid us)
        - For AP: Debit Payable, Credit Cash (we paid vendor)

    Attributes:
        tenant_id: The business tenant this payment belongs to.
        amount: The payment amount in NGN. Must be > 0 and <= balance due.
        payment_date: The date the payment was made.
        notes: Optional notes about the payment.
    """

    tenant_id: UUID
    amount: float = Field(..., gt=0)
    payment_date: datetime
    notes: str | None = None
