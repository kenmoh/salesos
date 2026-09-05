from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")


# ── Success responses ─────────────────────────────────────────────────────────


class SuccessResponse(_Base):
    success: bool = True


class PinStatusResponse(_Base):
    has_pin: bool = False
    expires_at: str | None = None


# ── Stores ────────────────────────────────────────────────────────────────────


class StoreCreated(_Base):
    id: str = ""
    name: str = ""
    address: str | None = None
    is_warehouse: bool = False
    status: str = "active"
    created_at: str | None = None


class StoreSummary(_Base):
    id: str = ""
    name: str = ""
    address: str | None = None
    is_warehouse: bool = False
    status: str = "active"
    created_at: str | None = None


class StoreCategory(_Base):
    id: str
    name: str
    description: str | None = None


class StoreProduct(_Base):
    id: str
    name: str
    sku: str | None = None
    selling_price: float = 0
    qty: float = 0
    reserved_qty: float = 0
    committed_qty: float = 0
    available: float = 0
    status: str = "active"


class StoreInventoryHealth(_Base):
    total_products: int = 0
    low_stock: int = 0
    out_of_stock: int = 0


class TopProductItem(_Base):
    product_id: str = ""
    product_name: str = ""
    total_qty: float = 0
    total_revenue: float = 0


class StoreStatsPeriod(_Base):
    from_date: str | None = None
    to_date: str | None = None


class StoreStats(_Base):
    revenue: float = 0
    sales_count: int = 0
    avg_order_value: float = 0
    top_products: list[TopProductItem] = []
    inventory_health: StoreInventoryHealth = StoreInventoryHealth()
    period: StoreStatsPeriod = StoreStatsPeriod()


class StoreDetails(_Base):
    store: StoreSummary | None = None
    categories: list[StoreCategory] = []
    products: list[StoreProduct] = []
    stats: StoreStats | None = None


# ── Categories ────────────────────────────────────────────────────────────────


class CategoryCreated(_Base):
    category_id: str = ""
    name: str = ""


class CategoryDetail(_Base):
    id: str = ""
    name: str = ""
    description: str | None = None


# ── Store Products ────────────────────────────────────────────────────────────


class StoreProductAdded(_Base):
    product_id: str = ""
    store_id: str = ""
    name: str | None = None
    sku: str | None = None
    selling_price: float | None = None
    qty: float = 0


class ProductCreatedForStore(_Base):
    product_id: str = ""
    store_id: str = ""
    name: str = ""
    sku: str | None = None
    selling_price: float = 0
    qty: float = 0


class StoreProductListItem(_Base):
    id: str = ""
    name: str = ""
    sku: str | None = None
    selling_price: float = 0
    reorder_point: int = 0
    qty: float = 0
    available: float = 0
    status: str = "active"
    category: str | None = None
    qr_url: str | None = None


class StoreProductUpdated(_Base):
    product_id: str = ""
    store_id: str = ""
    name: str = ""
    sku: str | None = None
    selling_price: float = 0
    cost_price: float = 0
    status: str = "active"


# ── Stock ─────────────────────────────────────────────────────────────────────


class StockAdjustmentResult(_Base):
    adjustment_id: str = ""
    new_balance: float = 0


class StockBalanceItem(_Base):
    product_id: str = ""
    product_name: str | None = None
    sku: str | None = None
    qty: float = 0
    reserved_qty: float = 0
    committed_qty: float = 0
    available: float = 0
    min_stock_level: float = 0
    unit_cost: float | None = None


class StockMovementItem(_Base):
    id: str
    product_id: str
    product_name: str | None = None
    product_sku: str | None = None
    store_id: str
    store_name: str | None = None
    movement_type: str
    qty_change: float
    balance_before: float
    balance_after: float
    reference_type: str | None = None
    reference_id: str | None = None
    reason: str | None = None
    unit_cost: float | None = None
    notes: str | None = None
    created_by: str | None = None
    created_at: datetime


class StoreProductDetail(_Base):
    id: str = ""
    name: str = ""
    sku: str | None = None
    selling_price: float = 0
    cost_price: float = 0
    reorder_point: int = 0
    qty: float = 0
    reserved_qty: float = 0
    committed_qty: float = 0
    available: float = 0
    min_stock_level: float = 0
    unit_cost: float | None = None
    status: str = "active"
    image_url: str | None = None
    qr_url: str | None = None
    category: str | None = None
    history: list[StockMovementItem] = []


class LowStockItem(_Base):
    product_id: str = ""
    product_name: str = ""
    sku: str | None = None
    store_id: str = ""
    store_name: str | None = None
    qty: float = 0
    min_stock_level: float = 0
    available: float = 0


class DistributeResult(_Base):
    from_adjustment_id: str = ""
    to_adjustment_id: str = ""
    from_new_balance: float = 0
    to_new_balance: float = 0


class SyncResult(_Base):
    synced: int
    skipped: int


class MinStockLevelResult(_Base):
    store_id: str = ""
    product_id: str = ""
    min_stock_level: float = 0


# ── Transfer Requests ─────────────────────────────────────────────────────────


class TransferRequestCreated(_Base):
    request_id: str = ""
    product_id: str = ""
    requesting_store_id: str = ""
    supplying_store_id: str = ""
    requested_qty: float = 0
    status: str = "pending"


class TransferRequestDetail(_Base):
    id: str = ""
    product_id: str = ""
    requesting_store_id: str = ""
    supplying_store_id: str = ""
    requested_qty: float = 0
    approved_qty: float | None = None
    status: str = "pending"
    notes: str | None = None
    rejection_reason: str | None = None
    created_by: str | None = None
    approved_by: str | None = None
    created_at: datetime | None = None


class TransferRequestApproved(_Base):
    request_id: str = ""
    status: str = "pending"
    approved_qty: float | None = None


class TransferFulfilled(_Base):
    request_id: str = ""
    status: str = "fulfilled"
    from_adjustment_id: str = ""
    to_adjustment_id: str = ""
    from_new_balance: float = 0
    to_new_balance: float = 0


# ── Auth ──────────────────────────────────────────────────────────────────────


class TokenPair(_Base):
    access_token: str = ""
    refresh_token: str = ""


class AuthUser(_Base):
    id: str = ""
    user_id: str = ""
    email: str = ""
    full_name: str | None = None
    role: str | None = None
    business_id: str | None = None
    status: str | None = None
    permissions: list[str] = []
    totp_enabled: bool | None = None
    auto_create_cart: bool | None = None
    last_login_at: str | None = None
    avatar_url: str | None = None
    store_id: str | None = None


class LoginResponse(_Base):
    tokens: TokenPair | None = None
    user: AuthUser | None = None
    requires_totp: bool = False


class RegistrationResponse(_Base):
    tenant: dict = {}
    user: AuthUser | None = None


class SessionInfo(_Base):
    sid: str = ""
    user_agent: str | None = None
    ip: str | None = None
    device: str | None = None
    last_active: str | None = None


class TOTPSetupResponse(_Base):
    secret: str = ""
    otpauth_url: str = ""
    issuer: str | None = None


class EmployeeCreated(_Base):
    id: str = ""
    email: str = ""
    full_name: str | None = None
    role: str | None = None
    store_id: str | None = None


class EmployeeListItem(_Base):
    id: str = ""
    email: str = ""
    full_name: str | None = None
    role: str | None = None
    is_active: bool = True
    store_id: str | None = None


class RoleCreated(_Base):
    id: str = ""
    name: str = ""
    description: str | None = None


class RoleDetail(_Base):
    id: str = ""
    name: str = ""
    description: str | None = None
    permissions: list[str] = []


class PermissionItem(_Base):
    id: str = ""
    name: str = ""
    description: str | None = None


class UserRoleResult(_Base):
    user_id: str = ""
    role: str = ""


class AuditLogItem(_Base):
    id: str = ""
    user_id: str | None = None
    action: str = ""
    resource: str | None = None
    details: dict | None = None
    created_at: str | None = None


# ── Cart ──────────────────────────────────────────────────────────────────────


# ── Sales ─────────────────────────────────────────────────────────────────────


class SaleCreated(_Base):
    id: str = ""
    sale_number: str = ""
    total: float = 0
    amount_paid: float = 0
    status: str = "pending"


class SaleDetail(_Base):
    id: str = ""
    sale_number: str = ""
    status: str = "pending"
    customer_name: str | None = None
    customer_phone: str | None = None
    subtotal: float = 0
    discount: float = 0
    tax: float = 0
    total: float = 0
    amount_paid: float = 0
    payment_methods: dict | None = None
    notes: str | None = None
    items: list[dict] = []
    created_at: datetime | None = None


class SaleListItem(_Base):
    id: str = ""
    sale_number: str = ""
    status: str = "pending"
    customer_name: str | None = None
    total: float = 0
    amount_paid: float = 0
    created_at: datetime | None = None


class SaleReturnResult(_Base):
    sale_id: str = ""
    status: str = "pending"
    refund_amount: float = 0


# ── Payments ──────────────────────────────────────────────────────────────────


class CashPaymentResult(_Base):
    payment_id: str = ""
    sale_id: str = ""
    amount: float = 0
    method: str = "cash"


class CardPaymentResult(_Base):
    payment_id: str = ""
    sale_id: str = ""
    amount: float = 0
    status: str = "pending"
    method: str = "card"
    flutterwave_ref: str | None = None
    payment_link: str | None = None
    payment_url: str | None = None
    qr_code_base64: str | None = None
    tx_ref: str | None = None


class TransferPaymentResult(_Base):
    payment_id: str = ""
    sale_id: str = ""
    amount: float = 0
    status: str = "pending"
    method: str = "transfer"
    account_number: str | None = None
    bank_name: str | None = None
    tx_ref: str | None = None
    expiry_date: str | None = None


class SplitSuggestion(_Base):
    cash: float = 0
    card: float = 0
    transfer: float = 0
    total: float = 0


class SplitPaymentResult(_Base):
    payment_id: str = ""
    sale_id: str = ""
    splits: dict = {}
    sale_status: str | None = None
    balance: float | None = None


class SubaccountCreated(_Base):
    id: str = ""
    account_number: str | None = None
    bank_name: str | None = None
    status: str = "active"


class BankInfo(_Base):
    name: str = ""
    code: str = ""


class ResolvedAccount(_Base):
    account_number: str = ""
    account_name: str = ""
    bank_code: str = ""
    bank_name: str | None = None


class PaymentIntentStatus(_Base):
    method: str = ""
    status: str = ""
    tx_ref: str | None = None
    amount: float = 0


class PaymentStatusResponse(_Base):
    sale_id: str = ""
    status: str = "pending"
    amount_paid: float = 0
    total: float = 0
    intents: list[PaymentIntentStatus] = []


class PendingPaymentSummary(_Base):
    sale_id: str = ""
    sale_number: str = ""
    method: str = ""
    amount: float = 0
    created_at: str = ""
    authorization_url: str | None = None
    qr_code_base64: str | None = None
    account_number: str | None = None
    bank_name: str | None = None
    tx_ref: str | None = None
    expiry_date: str | None = None


# ── Reports ───────────────────────────────────────────────────────────────────


class _MetricDelta(_Base):
    current: float = 0
    previous: float = 0
    change_pct: float = 0


class DashboardSummary(_Base):
    revenue: _MetricDelta = _MetricDelta()
    sales_count: _MetricDelta = _MetricDelta()
    avg_order_value: _MetricDelta = _MetricDelta()
    top_product: dict | None = None
    low_stock_count: int = 0
    active_users: int = 0
    pending_documents: int = 0
    payment_collection_rate: float = 0


class SalesSummary(_Base):
    total_revenue: float = 0
    total_sales: int = 0
    avg_order_value: float = 0
    period: dict = {}


class TopProduct(_Base):
    product_id: str
    product_name: str
    total_qty: float = 0
    total_revenue: float = 0


class PaymentBreakdown(_Base):
    cash: float = 0
    card: float = 0
    transfer: float = 0
    total: float = 0


class CashierPerformanceItem(_Base):
    cashier_id: str
    cashier_name: str | None = None
    total_sales: int = 0
    total_revenue: float = 0
    avg_order_value: float = 0


class InventoryAlertsSummary(_Base):
    total_products: int = 0
    low_stock: int = 0
    out_of_stock: int = 0


class InventoryAlertsResult(_Base):
    summary: InventoryAlertsSummary
    items: list[dict] = []


class ProfitLossResult(_Base):
    revenue: float = 0
    cost_of_goods: float = 0
    gross_profit: float = 0
    expenses: float = 0
    net_profit: float = 0
    items: list[dict] = []
    totals: dict = {}


class CustomerInsightsResult(_Base):
    total_customers: int = 0
    repeat_customers: int = 0
    avg_order_value: float = 0
    top_customers: list[dict] = []


class DocumentSummaryResult(_Base):
    total_invoices: int = 0
    total_quotes: int = 0
    total_receipts: int = 0
    outstanding_amount: float = 0


# ── Documents ─────────────────────────────────────────────────────────────────


class DocumentCreated(_Base):
    doc_id: str = ""
    doc_type: str = ""
    doc_number: str = ""
    status: str = "draft"


class DocumentDetail(_Base):
    id: str = ""
    doc_type: str = ""
    doc_number: str = ""
    status: str = "draft"
    customer_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    total: float = 0
    items: list[dict] = []
    created_at: datetime | None = None


class DocumentListItem(_Base):
    id: str = ""
    doc_type: str = ""
    doc_number: str = ""
    status: str = "draft"
    customer_name: str | None = None
    total: float = 0
    created_at: datetime | None = None


class DocumentStatusUpdated(_Base):
    doc_id: str = ""
    status: str = ""


class DocumentConverted(_Base):
    sale_id: str = ""
    sale_number: str = ""
    total: float = 0


# ── Sync ──────────────────────────────────────────────────────────────────────


class SyncBatchResult(_Base):
    processed: int = 0
    failed: int = 0
    errors: list[dict] = []


class SyncPendingItem(_Base):
    id: str
    event_type: str
    payload: dict
    source: str | None = None
    created_at: datetime | None = None


class BusinessSettings(_Base):
    name: str | None = None
    phone: str | None = None
    address: str | None = None
    tax_rate: float | None = None
    currency: str | None = None
    settings: dict = {}


class PermissionDetail(_Base):
    id: str
    name: str
    description: str | None = None


# ── Admin Security ────────────────────────────────────────────────────────────


class AuditEvent(_Base):
    id: str = ""
    user_id: str | None = None
    action: str = ""
    resource: str | None = None
    details: dict | None = None
    ip: str | None = None
    created_at: str | None = None


class IPBanResult(_Base):
    ip: str = ""
    banned: bool = False
    reason: str | None = None
    banned_at: str | None = None


class RateLimitInfo(_Base):
    ip: str
    windows: list[str] = []


class RateLimitResetResult(_Base):
    success: bool = True
    ip: str
    reset: int = 0


# ── Accounting ────────────────────────────────────────────────────────────────


class AccountCreated(_Base):
    id: str = ""
    code: str = ""
    name: str = ""
    account_type: str = ""


class AccountDetail(_Base):
    id: str = ""
    code: str = ""
    name: str = ""
    account_type: str = ""
    balance: float = 0


class JournalCreated(_Base):
    journal_id: str = ""
    description: str = ""
    status: str = "posted"


class JournalListItem(_Base):
    id: str = ""
    description: str = ""
    status: str = "posted"
    reference_id: str | None = None
    created_at: datetime | None = None


class TrialBalanceResult(_Base):
    accounts: list[dict] = []
    total_debit: float = 0
    total_credit: float = 0


class ProfitAndLossResult(_Base):
    revenue: float = 0
    expenses: float = 0
    net_income: float = 0
    period: dict = {}


class BalanceSheetResult(_Base):
    assets: list[dict] = []
    liabilities: list[dict] = []
    equity: list[dict] = []
    total_assets: float = 0
    total_liabilities: float = 0
    total_equity: float = 0


class CashFlowResult(_Base):
    operating: list[dict] = []
    investing: list[dict] = []
    financing: list[dict] = []
    net_cash: float = 0


class AccountsReceivableItem(_Base):
    id: str
    customer_name: str | None = None
    amount: float = 0
    due_date: str | None = None
    status: str = "pending"


class ARCreated(_Base):
    id: str = ""
    customer_name: str | None = None
    amount: float = 0
    due_date: str | None = None


class ARPaymentResult(_Base):
    id: str = ""
    amount_paid: float = 0
    balance_remaining: float = 0


class AccountsPayableItem(_Base):
    id: str
    vendor_name: str | None = None
    amount: float = 0
    due_date: str | None = None
    status: str = "pending"


class APCreated(_Base):
    id: str = ""
    vendor_name: str | None = None
    amount: float = 0
    due_date: str | None = None


class APPaymentResult(_Base):
    id: str = ""
    amount_paid: float = 0
    balance_remaining: float = 0


class ExpenseItem(_Base):
    id: str = ""
    description: str = ""
    amount: float = 0
    category: str | None = None
    created_at: datetime | None = None


class ExpenseCreated(_Base):
    id: str = ""
    description: str = ""
    amount: float = 0
    category: str | None = None


class ExpenseSummary(_Base):
    total_expenses: float = 0
    by_category: list[dict] = []


class FinancialDashboard(_Base):
    total_revenue: float = 0
    total_expenses: float = 0
    net_income: float = 0
    accounts_receivable: float = 0
    accounts_payable: float = 0


class CommissionRecorded(_Base):
    sale_id: str = ""
    commission_amount: float = 0
    status: str = "pending"


# ── AI ────────────────────────────────────────────────────────────────────────


class ConversationDeleted(_Base):
    conversation_id: str
    deleted: bool = True


# ── Scan ──────────────────────────────────────────────────────────────────────


class ScannedProduct(_Base):
    id: str = ""
    name: str = ""
    sku: str | None = None
    selling_price: float = 0
    store_id: str | None = None
    stock_qty: float | None = None
