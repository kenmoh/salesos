"""Enable Row Level Security with tenant isolation policies.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from alembic import op
import sqlalchemy as sa

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "accounts_payable",
    "accounts_receivable",
    "auth_audit_logs",
    "carts",
    "cashier_performance",
    "categories",
    "chart_of_accounts",
    "commission_ledger",
    "conversation_messages",
    "conversations",
    "customers",
    "daily_sales_summary",
    "dedicated_virtual_accounts",
    "documents",
    "expenses",
    "ip_bans",
    "journal_entries",
    "journals",
    "notification_templates",
    "notifications",
    "outbox_events",
    "payment_intents",
    "payment_method_summary",
    "payments",
    "platform_fee_ledger",
    "product_performance",
    "products",
    "receipts",
    "roles",
    "sales",
    "stock_adjustments",
    "stock_balances",
    "stock_movements",
    "stock_reservations",
    "store_products",
    "stores",
    "subaccounts",
    "tenant_tier_projections",
    "transfer_requests",
    "users",
]

POLICY_SQL = """
CREATE POLICY tenant_isolation ON {table}
    USING (tenant_id::text = current_setting('app.business_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.business_id', true));
"""


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(POLICY_SQL.format(table=table))


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
