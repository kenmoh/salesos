"""Accounting domain models for the StoreFlow mini accounting system.

This module defines the SQLAlchemy ORM models for double-entry bookkeeping,
chart of accounts, journal entries, accounts receivable, accounts payable,
and expense tracking. All models reside in the PostgreSQL "accounting" schema.

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- AP: Accounts Payable -- money the business OWES to vendors/suppliers.
- COA: Chart of Accounts -- the complete list of all accounts used by the business.
- JE: Journal Entry -- a record of a financial transaction in double-entry format.
- FK: Foreign Key -- a constraint that links two tables together.
- PK: Primary Key -- a unique identifier for each row in a table.
- UUID: Universally Unique Identifier -- a 128-bit identifier used for primary keys.
- UTC: Coordinated Universal Time -- the primary time standard by which time is regulated.
- PG_UUID: PostgreSQL UUID type -- a native UUID column type for PostgreSQL databases.
- Mapped: SQLAlchemy type annotation that maps a Python type to a database column.
- Numeric(p, s): A fixed-precision decimal type with p total digits and s digits after decimal.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase



class ChartOfAccount(StoreFlowBase):
    """Represents a single account in the business's Chart of Accounts (COA).

    The Chart of Accounts is the complete list of all financial accounts used
    to organize the business's transactions. Accounts are categorized by type:
    asset, liability, equity, revenue, or expense. Each account has a unique
    code (e.g., "1000" for Cash, "4000" for Revenue) and can optionally have
    a parent account for hierarchical grouping.

    Account Code Conventions (Nigerian Small Business):
        - 1xxx: Asset accounts (Cash, Bank, Inventory, Equipment)
        - 2xxx: Liability accounts (Accounts Payable, Loans)
        - 3xxx: Equity accounts (Owner's Capital, Retained Earnings)
        - 4xxx: Revenue accounts (Sales Revenue, Service Revenue)
        - 5xxx: Expense accounts (COGS, Rent, Utilities, Salaries)

    Example:
        A business with code "1000" named "Cash" would represent the main
        cash account where daily sales are deposited.

    Attributes:
        id: Unique identifier for this account (UUID, auto-generated).
        tenant_id: The business tenant this account belongs to (multi-tenant isolation).
        code: The account number/code (e.g., "1000", "4000"). Must be unique per tenant.
        name: Human-readable account name (e.g., "Cash", "Sales Revenue").
        account_type: The category of this account. One of: "asset", "liability",
            "equity", "revenue", "expense". Determines how the account behaves
            in financial statements (debits increase assets/expenses, credits
            increase liabilities/equity/revenue).
        parent_id: Optional reference to a parent account for hierarchical grouping.
            For example, "Bank Account" could be a child of "Cash".
        status: Account status. "active" means the account can be used in transactions.
            "inactive" means it is archived but historical data is preserved.
        created_at: Timestamp when this account was created (UTC).
    """

    __tablename__ = "chart_of_accounts"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_type: Mapped[str] = mapped_column(String(30), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Journal(StoreFlowBase):
    """Represents a journal -- the header record for a financial transaction.

    In double-entry bookkeeping, every financial transaction is recorded as a
    journal. A journal contains one or more journal entries (debit/credit lines)
    that must balance (total debits == total credits). The journal serves as the
    primary audit trail for all financial activity.

    Journal Lifecycle:
        1. DRAFT: Created but not yet posted. Can be edited or deleted.
        2. POSTED: Finalized and immutable. Entries are reflected in the
           general ledger and financial statements.

    Journal Number Format:
        JRN-YYYYMMDD-XXXXXXXX
        Example: JRN-20260826-A1B2C3D4
        Where YYYYMMDD is the date and XXXXXXXX is a random 8-char suffix.

    Attributes:
        id: Unique identifier for this journal (UUID, auto-generated).
        tenant_id: The business tenant this journal belongs to.
        journal_number: Auto-generated unique journal number (format: JRN-YYYYMMDD-XXXXXXXX).
        description: Human-readable description of the transaction.
            Example: "Sale INV-20260001" or "Payment received from Customer X".
        reference_id: Optional UUID linking to the source document/entity.
            For example, the sale_id when a sale is confirmed, or payment_id
            when a payment succeeds.
        reference_type: The type of the referenced entity. Examples: "sale",
            "payment", "expense", "adjustment". Used for polymorphic linking.
        status: Journal status. "draft" or "posted". Posted journals are immutable.
        posted_by: UUID of the user who posted this journal. None if still in draft.
        posted_at: Timestamp when this journal was posted (UTC). None if still in draft.
        created_at: Timestamp when this journal was created (UTC).
    """

    __tablename__ = "journals"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    journal_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    posted_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class JournalEntry(StoreFlowBase):
    """Represents a single debit or credit line within a journal.

    In double-entry bookkeeping, each journal contains at least two journal
    entries: one debit and one credit. The total debits must equal total credits
    for the journal to be balanced and postable.

    Debit vs Credit Rules:
        - Asset accounts:    Debit = increase, Credit = decrease
        - Liability accounts: Debit = decrease, Credit = increase
        - Equity accounts:   Debit = decrease, Credit = increase
        - Revenue accounts:  Debit = decrease, Credit = increase
        - Expense accounts:  Debit = increase, Credit = decrease

    Example:
        A sale of NGN 50,000 cash would produce:
        - Debit:  Account 1000 (Cash)           NGN 50,000
        - Credit: Account 4000 (Sales Revenue)  NGN 50,000

    Attributes:
        id: Unique identifier for this entry (UUID, auto-generated).
        journal_id: Foreign key linking to the parent Journal record.
        tenant_id: The business tenant this entry belongs to (denormalized from
            Journal for efficient direct queries without JOIN operations).
        account_id: Foreign key linking to the ChartOfAccount being debited/credited.
        account_code: The account code (denormalized for display purposes).
            Example: "1000" for Cash, "4000" for Revenue.
        debit: The debit amount in the business's base currency (NGN).
            Must be >= 0. Zero means this line is a credit-only entry.
        credit: The credit amount in the business's base currency (NGN).
            Must be >= 0. Zero means this line is a debit-only entry.
        description: Optional line-item description. Example: "Samsung Galaxy A14 sale".
        type: The financial category of this entry. One of: "asset", "liability",
            "equity", "revenue", "expense". Denormalized from the linked account
            for efficient filtering and aggregation in financial reports.
        status: The posting status of this entry. "draft" or "posted". Denormalized
            from the parent Journal for efficient filtering without JOINs.
        posted_at: Timestamp when this entry was posted (UTC). Denormalized from
            the parent Journal for date-range queries in financial reports.
        amount: The net amount of this entry (debit - credit for asset/expense types,
            credit - debit for liability/equity/revenue types). Denormalized for
            efficient aggregation in Profit & Loss and other financial reports.
    """

    __tablename__ = "journal_entries"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    journal_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    account_code: Mapped[str] = mapped_column(String(20), nullable=False)
    debit: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    credit: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="asset")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)


class CommissionLedger(StoreFlowBase):
    """Tracks sales commissions owed to employees/cashiers.

    When a sale is made by an employee, a commission record is created based
    on the configured commission rate for that employee or role. Commissions
    start as "pending" and are marked "paid" when the employee receives payment.

    Commission Calculation:
        commission_amount = sale_total * (rate_pct / 100)
        Example: NGN 100,000 sale at 5% rate = NGN 5,000 commission

    Attributes:
        id: Unique identifier for this commission record (UUID, auto-generated).
        tenant_id: The business tenant this commission belongs to.
        sale_id: Foreign key linking to the Sale that earned this commission.
        user_id: Foreign key linking to the User (employee/cashier) who earned it.
        amount: The commission amount in NGN (calculated as sale_total * rate / 100).
        rate_pct: The commission rate as a percentage (e.g., 5.00 means 5%).
        status: Payment status. "pending" means owed but not yet paid.
            "paid" means the employee has received the commission.
        paid_at: Timestamp when this commission was paid (UTC). None if still pending.
        created_at: Timestamp when this commission record was created (UTC).
    """

    __tablename__ = "commission_ledger"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    rate_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AccountReceivable(StoreFlowBase):
    """Tracks money owed TO the business by customers (Accounts Receivable).

    Accounts Receivable (AR) represents the total amount that customers owe
    the business for goods or services delivered but not yet paid for. This
    is an asset on the Balance Sheet -- it represents future cash inflows.

    AR is created when:
        - An invoice is issued to a customer (doc_type = "invoice")
        - A sale is made on credit (payment deferred)

    AR is reduced when:
        - A customer makes a partial payment
        - A customer pays in full

    AR Status Lifecycle:
        1. "pending": Invoice issued, awaiting payment.
        2. "overdue": Payment is past the due_date.
        3. "partial": Customer has made a partial payment.
        4. "paid": Customer has paid in full.

    Aging Buckets (for reporting):
        - Current (0-30 days): Not yet due
        - 31-60 days: Slightly overdue
        - 61-90 days: Moderately overdue
        - 90+ days: Severely overdue (may need collection action)

    Attributes:
        id: Unique identifier for this receivable (UUID, auto-generated).
        tenant_id: The business tenant this receivable belongs to.
        invoice_id: Optional foreign key linking to the Document (invoice) that
            created this receivable. None for manually created receivables.
        customer_id: Foreign key linking to the Customer who owes the money.
        customer_name: Denormalized customer name for display without JOINs.
        invoice_number: The invoice number (e.g., "INV-20260001"). Denormalized
            for display and search without JOINs.
        amount: The total invoice amount in NGN (what the customer was billed).
        amount_paid: How much the customer has paid so far in NGN. Starts at 0.
        balance: The remaining amount owed (amount - amount_paid). Denormalized
            for efficient filtering and reporting.
        due_date: The date by which payment is expected. Used for aging reports.
        status: Payment status. One of: "pending", "overdue", "partial", "paid".
        created_at: Timestamp when this receivable record was created (UTC).
    """

    __tablename__ = "accounts_receivable"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    invoice_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    customer_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    amount_paid: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    balance: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AccountPayable(StoreFlowBase):
    """Tracks money the business OWES to vendors/suppliers (Accounts Payable).

    Accounts Payable (AP) represents the total amount that the business owes
    to vendors or suppliers for goods or services received but not yet paid for.
    This is a liability on the Balance Sheet -- it represents future cash outflows.

    AP is created when:
        - A bill is received from a vendor (e.g., rent, utilities, supplies)
        - A purchase order is fulfilled but not yet paid

    AP is reduced when:
        - The business makes a partial payment to the vendor
        - The business pays in full

    AP Status Lifecycle:
        1. "pending": Bill received, awaiting payment.
        2. "overdue": Payment is past the due_date.
        3. "partial": Business has made a partial payment.
        4. "paid": Business has paid in full.

    Attributes:
        id: Unique identifier for this payable (UUID, auto-generated).
        tenant_id: The business tenant this payable belongs to.
        bill_number: The bill/invoice number from the vendor (e.g., "BILL-001").
            Used for reference when making payments.
        vendor_name: The name of the vendor or supplier owed. Denormalized
            for display without JOINs.
        description: Description of what was purchased. Example: "August rent"
            or "Office supplies - September".
        amount: The total bill amount in NGN (what the business was billed).
        amount_paid: How much has been paid to the vendor so far in NGN. Starts at 0.
        balance: The remaining amount owed (amount - amount_paid). Denormalized
            for efficient filtering and reporting.
        due_date: The date by which payment should be made. Used for cash flow
            planning and aging reports.
        status: Payment status. One of: "pending", "overdue", "partial", "paid".
        created_at: Timestamp when this payable record was created (UTC).
    """

    __tablename__ = "accounts_payable"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    bill_number: Mapped[str] = mapped_column(String(50), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    amount_paid: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    balance: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Expense(StoreFlowBase):
    """Tracks manual expense entries for operating costs.

    Expenses represent money spent by the business on day-to-day operations.
    Each expense is categorized (rent, utilities, salaries, etc.) and linked
    to an expense account in the Chart of Accounts. When an expense is recorded,
    a journal entry is automatically created: Debit Expense Account, Credit Cash.

    Expense Categories (Nigerian Small Business):
        - "rent": Office/shop rent
        - "utilities": Electricity, water, waste disposal
        - "salaries": Employee wages and salaries
        - "supplies": Office supplies, packaging materials
        - "transport": Delivery, logistics, fuel costs
        - "marketing": Advertising, promotions, social media ads
        - "bank_charges": Transfer fees, POS charges, bank maintenance
        - "phone_internet": Airtime, data, internet subscription
        - "maintenance": Equipment repairs, building maintenance
        - "insurance": Business insurance premiums
        - "taxes": Government levies, permits, licenses
        - "other": Miscellaneous expenses that don't fit other categories

    Expense Number Format:
        EXP-YYYYMMDD-XXXXXXXX
        Example: EXP-20260826-F1E2D3C4
        Where YYYYMMDD is the date and XXXXXXXX is a random 8-char suffix.

    Attributes:
        id: Unique identifier for this expense (UUID, auto-generated).
        tenant_id: The business tenant this expense belongs to.
        expense_number: Auto-generated unique expense number (format: EXP-YYYYMMDD-XXXXXXXX).
        category: The expense category. One of the predefined categories listed above.
        description: Detailed description of the expense. Example: "August electricity bill"
            or "Office supplies from Stationery Store".
        amount: The expense amount in NGN.
        vendor: Optional name of the vendor/supplier. Example: "Ikeja Electric" for
            electricity or "Office Supplies Ltd" for supplies.
        receipt_url: Optional URL to an uploaded receipt image (stored in Cloudinary).
            Used for audit trails and tax documentation.
        expense_date: The date the expense was incurred (may differ from created_at
            if expenses are recorded in batch or after the fact).
        account_id: Foreign key linking to the ChartOfAccount expense account.
            Example: Account 5100 for "Rent" or 5200 for "Utilities".
        journal_id: Foreign key linking to the automatically created Journal record.
            None until the journal is posted.
        created_by: Foreign key linking to the User who recorded this expense.
        created_at: Timestamp when this expense record was created (UTC).
    """

    __tablename__ = "expenses"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    expense_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receipt_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expense_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    journal_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
