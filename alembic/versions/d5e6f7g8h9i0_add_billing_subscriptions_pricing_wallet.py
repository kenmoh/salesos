"""Add subscription, transaction-fee billing, and wallet system.

Revision ID: d5e6f7g8h9i0
Revises: b3c4d5e6f7g8
Create Date: 2026-11-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from uuid import uuid4

revision: str = "d5e6f7g8h9i0"
down_revision: Union[str, None] = "b3c4d5e6f7g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Global pricing configuration (no tenant RLS — platform-managed) ────
    op.execute("""
        CREATE TABLE IF NOT EXISTS subscription_plans (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(60) NOT NULL,
            slug VARCHAR(60) NOT NULL UNIQUE,
            description TEXT,
            monthly_price NUMERIC(12,2) NOT NULL DEFAULT 0,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_subscription_plans_price_nonneg CHECK (monthly_price >= 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_subscription_plans_active ON subscription_plans(is_active, sort_order)")

    # Dynamic, admin-configurable feature labels and limits.
    op.execute("""
        CREATE TABLE IF NOT EXISTS plan_features (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE CASCADE,
            feature_key VARCHAR(80) NOT NULL,
            feature_label VARCHAR(200) NOT NULL,
            feature_value VARCHAR(200),
            is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(plan_id, feature_key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_plan_features_plan ON plan_features(plan_id, sort_order)")

    # Pricing versions are immutable snapshots created whenever an admin
    # changes fees. Historical invoices/billable transactions reference the
    # version that was active at the time — never a mutable live config.
    op.execute("""
        CREATE TABLE IF NOT EXISTS pricing_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            version_number INTEGER NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            effective_until TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_pricing_versions_number UNIQUE(version_number),
            CONSTRAINT ck_pricing_versions_dates CHECK (effective_until IS NULL OR effective_until > effective_from)
        )
    """)

    # Dynamic transaction fee labels + fixed-NGN bands. Bands are contiguous,
    # non-overlapping, and the final band has NULL maximum (unlimited).
    op.execute("""
        CREATE TABLE IF NOT EXISTS transaction_fee_tiers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            pricing_version_id UUID NOT NULL REFERENCES pricing_versions(id) ON DELETE CASCADE,
            plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,
            label VARCHAR(100) NOT NULL,
            minimum_amount NUMERIC(15,2) NOT NULL,
            maximum_amount NUMERIC(15,2),
            fee_amount NUMERIC(12,2) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_tier_amounts CHECK (
                minimum_amount >= 0 AND fee_amount >= 0 AND
                (maximum_amount IS NULL OR maximum_amount > minimum_amount)
            )
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_fee_tiers_version_plan ON transaction_fee_tiers(pricing_version_id, plan_id, minimum_amount)")

    # ── Tenant-scoped subscription / billing / wallet tables (RLS) ──────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS merchant_subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            billing_interval VARCHAR(20) NOT NULL DEFAULT 'monthly',
            current_period_start TIMESTAMPTZ NOT NULL,
            current_period_end TIMESTAMPTZ NOT NULL,
            next_billing_date TIMESTAMPTZ NOT NULL,
            grace_period_end TIMESTAMPTZ,
            auto_debit_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(tenant_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_merchant_subs_next_billing ON merchant_subscriptions(next_billing_date) WHERE status = 'active'")

    op.execute("""
        CREATE TABLE IF NOT EXISTS billable_transactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            branch_id UUID,
            sale_id UUID NOT NULL,
            subscription_id UUID REFERENCES merchant_subscriptions(id) ON DELETE SET NULL,
            plan_id UUID REFERENCES subscription_plans(id) ON DELETE SET NULL,
            pricing_version_id UUID REFERENCES pricing_versions(id) ON DELETE SET NULL,
            fee_tier_id UUID REFERENCES transaction_fee_tiers(id) ON DELETE SET NULL,
            transaction_amount NUMERIC(15,2) NOT NULL,
            transaction_fee NUMERIC(12,2) NOT NULL,
            fee_label_snapshot VARCHAR(100),
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            transaction_status VARCHAR(20) NOT NULL DEFAULT 'billable',
            completed_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_billable_transaction_sale UNIQUE(tenant_id, sale_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_billable_tx_tenant_period ON billable_transactions(tenant_id, completed_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_billable_tx_branch ON billable_transactions(tenant_id, branch_id)")

    # Append-only fee ledger. Credits/reversals are separate rows — historical
    # entries are never mutated.
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_fee_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            sale_id UUID,
            billable_transaction_id UUID REFERENCES billable_transactions(id) ON DELETE SET NULL,
            entry_type VARCHAR(20) NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            billing_period_id UUID,
            reference_id UUID,
            description TEXT,
            actor_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_billing_entry_type CHECK (entry_type IN ('charge','credit','reversal','adjustment'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_ledger_tenant ON billing_fee_ledger(tenant_id, created_at)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_ledger_charge_tx ON billing_fee_ledger(billable_transaction_id) WHERE entry_type = 'charge'")

    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_periods (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            period_start TIMESTAMPTZ NOT NULL,
            period_end TIMESTAMPTZ NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            invoice_generated BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(tenant_id, period_start)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_invoices (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            billing_period_id UUID REFERENCES billing_periods(id) ON DELETE SET NULL,
            invoice_number VARCHAR(40) NOT NULL UNIQUE,
            subscription_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            transaction_fee_total NUMERIC(12,2) NOT NULL DEFAULT 0,
            adjustment_total NUMERIC(12,2) NOT NULL DEFAULT 0,
            credit_total NUMERIC(12,2) NOT NULL DEFAULT 0,
            total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            amount_paid NUMERIC(12,2) NOT NULL DEFAULT 0,
            amount_due NUMERIC(12,2) NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'issued',
            issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            due_at TIMESTAMPTZ NOT NULL,
            grace_until TIMESTAMPTZ NOT NULL,
            paid_at TIMESTAMPTZ,
            auto_debit_attempted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(tenant_id, billing_period_id),
            CONSTRAINT ck_invoice_dates CHECK (grace_until > due_at AND due_at > issued_at)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant ON billing_invoices(tenant_id, issued_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_invoices_status ON billing_invoices(status, due_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            invoice_id UUID NOT NULL REFERENCES billing_invoices(id) ON DELETE RESTRICT,
            tenant_id UUID NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            payment_method VARCHAR(20) NOT NULL DEFAULT 'wallet',
            reference VARCHAR(120),
            status VARCHAR(20) NOT NULL DEFAULT 'completed',
            paid_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_billing_payment_amount CHECK (amount > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_billing_payments_invoice ON billing_payments(invoice_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_wallets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL UNIQUE,
            balance NUMERIC(15,2) NOT NULL DEFAULT 0,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wallet_balance CHECK (balance >= 0)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_transactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            wallet_id UUID REFERENCES tenant_wallets(id) ON DELETE SET NULL,
            entry_type VARCHAR(20) NOT NULL,
            amount NUMERIC(15,2) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            reference VARCHAR(120),
            invoice_id UUID REFERENCES billing_invoices(id) ON DELETE SET NULL,
            gateway_reference VARCHAR(120),
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wallet_entry_type CHECK (entry_type IN ('funding','invoice_debit','refund_credit','settlement','adjustment'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_wallet_tx_tenant ON wallet_transactions(tenant_id, created_at DESC)")

    # ── RLS on all tenant-scoped tables ─────────────────────────────────────
    for table in (
        "merchant_subscriptions", "billable_transactions", "billing_fee_ledger",
        "billing_periods", "billing_invoices", "billing_payments",
        "tenant_wallets", "wallet_transactions",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id::text = current_setting('app.business_id', true))
                WITH CHECK (tenant_id::text = current_setting('app.business_id', true))
        """)

    # ── Permissions ─────────────────────────────────────────────────────────
    for perm_name, perm_desc in (
        ("billing:read", "View subscription, fees, invoices, and wallet"),
        ("billing:manage", "Manage own subscription and wallet funding"),
    ):
        conn.execute(
            text(
                "INSERT INTO permissions (id, name, description) "
                "VALUES (:id, :name, :desc) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"id": str(uuid4()), "name": perm_name, "desc": perm_desc},
        )

    for role_name, perm_name in (
        ("owner", "billing:read"),
        ("owner", "billing:manage"),
        ("manager", "billing:read"),
    ):
        conn.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = :role_name AND p.name = :perm_name "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_name": role_name, "perm_name": perm_name},
        )

    # ── Seed default plans with dynamic feature labels ─────────────────────
    op.execute("""
        INSERT INTO subscription_plans (id, name, slug, description, monthly_price, currency, is_active, sort_order)
        VALUES
            (gen_random_uuid(), 'Free', 'free', 'Essential POS and inventory for small businesses.', 0, 'NGN', TRUE, 1),
            (gen_random_uuid(), 'Basic', 'basic', 'Advanced inventory, credit customers, and reporting.', 7500, 'NGN', TRUE, 2),
            (gen_random_uuid(), 'Pro', 'pro', 'Multi-branch, advanced permissions and analytics.', 19500, 'NGN', TRUE, 3)
        ON CONFLICT (slug) DO NOTHING
    """)

    # Default feature labels per plan (admin-editable, shown on landing page).
    features = {
        "free": [
            ("pos", "POS terminal", "1 terminal", True),
            ("products", "Products", "100 products", True),
            ("staff", "Staff accounts", "2 users", True),
            ("inventory", "Basic inventory", "", True),
            ("customers", "Customer management", "", True),
            ("reports", "Basic reports", "", True),
            ("ai", "AI assistant", "Standard", True),
            ("branches", "Multi-branch", "", False),
        ],
        "basic": [
            ("pos", "POS terminals", "3 terminals", True),
            ("products", "Products", "1,000 products", True),
            ("staff", "Staff accounts", "10 users", True),
            ("inventory", "Advanced inventory", "", True),
            ("customers", "Credit customers & receivables", "", True),
            ("reports", "Detailed business reports", "", True),
            ("ai", "AI assistant", "Increased", True),
            ("branches", "Multi-branch", "", False),
        ],
        "pro": [
            ("pos", "POS terminals", "Unlimited", True),
            ("products", "Products", "10,000 products", True),
            ("staff", "Staff accounts", "100 users", True),
            ("inventory", "Advanced inventory", "", True),
            ("customers", "Credit customers & receivables", "", True),
            ("reports", "Advanced analytics & consolidated reporting", "", True),
            ("ai", "AI assistant", "Highest", True),
            ("branches", "Multi-branch management", "", True),
        ],
    }
    for slug, rows in features.items():
        for key, label, value, enabled in rows:
            conn.execute(
                text("""
                    INSERT INTO plan_features (id, plan_id, feature_key, feature_label, feature_value, is_enabled, sort_order)
                    SELECT gen_random_uuid(), p.id, :key, :label, :value, :enabled, :sort
                    FROM subscription_plans p WHERE p.slug = :slug
                    ON CONFLICT (plan_id, feature_key) DO NOTHING
                """),
                {"slug": slug, "key": key, "label": label, "value": value,
                 "enabled": enabled, "sort": rows.index((key, label, value, enabled))},
            )

    # Initial pricing version with the configured fee bands from sub.md.
    conn.execute(
        text("""
            INSERT INTO pricing_versions (id, name, version_number, effective_from, status)
            VALUES (gen_random_uuid(), 'Initial pricing', 1, NOW(), 'active')
            ON CONFLICT (version_number) DO NOTHING
        """)
    )
    bands = {
        "free": [(0, 5000, 25), (6000, 10000, 35), (11000, 20000, 40), (21000, 50000, 50),
                 (51000, 100000, 65), (101000, 300000, 100), (301000, 500000, 200),
                 (501000, 1000000, 300), (1000001, None, 500)],
        "basic": [(0, 5000, 20), (6000, 10000, 30), (11000, 20000, 35), (21000, 50000, 40),
                  (51000, 100000, 50), (101000, 300000, 80), (301000, 500000, 160),
                  (501000, 1000000, 240), (1000001, None, 400)],
        "pro": [(0, 5000, 15), (6000, 10000, 20), (11000, 20000, 25), (21000, 50000, 30),
                (51000, 100000, 40), (101000, 300000, 60), (301000, 500000, 120),
                (501000, 1000000, 180), (1000001, None, 300)],
    }
    for slug, tiers in bands.items():
        for idx, (min_amt, max_amt, fee) in enumerate(tiers):
            label = f"₦{min_amt:,} – ₦{max_amt:,}" if max_amt else f"Above ₦{min_amt:,}"
            conn.execute(
                text("""
                    INSERT INTO transaction_fee_tiers
                        (id, pricing_version_id, plan_id, label, minimum_amount, maximum_amount, fee_amount, currency, is_active)
                    SELECT gen_random_uuid(), v.id, p.id, :label, :min, :max, :fee, 'NGN', TRUE
                    FROM pricing_versions v, subscription_plans p
                    WHERE v.version_number = 1 AND p.slug = :slug
                    ON CONFLICT DO NOTHING
                """),
                {"slug": slug, "label": label, "min": min_amt, "max": max_amt, "fee": fee},
            )

    # ── Create a 30-day subscription + wallet for every existing tenant ────
    conn.execute(text("""
        INSERT INTO merchant_subscriptions
            (id, tenant_id, plan_id, status, billing_interval, current_period_start, current_period_end, next_billing_date, auto_debit_enabled)
        SELECT gen_random_uuid(), t.id, p.id, 'active', 'monthly', NOW(),
               NOW() + INTERVAL '30 days', NOW() + INTERVAL '30 days', TRUE
        FROM tenants t
        JOIN subscription_plans p ON p.slug = 'free'
        WHERE t.status = 'active'
        ON CONFLICT (tenant_id) DO NOTHING
    """))
    conn.execute(text("""
        INSERT INTO tenant_wallets (id, tenant_id, balance, currency)
        SELECT gen_random_uuid(), t.id, 0, 'NGN'
        FROM tenants t
        WHERE t.status = 'active'
        ON CONFLICT (tenant_id) DO NOTHING
    """))

    # Billing periods for active tenants (30-day cycle from now).
    conn.execute(text("""
        INSERT INTO billing_periods (id, tenant_id, period_start, period_end, status)
        SELECT gen_random_uuid(), s.tenant_id, s.current_period_start, s.current_period_end, 'open'
        FROM merchant_subscriptions s
        ON CONFLICT (tenant_id, period_start) DO NOTHING
    """))


def downgrade() -> None:
    for table in (
        "wallet_transactions", "tenant_wallets", "billing_payments",
        "billing_invoices", "billing_periods", "billing_fee_ledger",
        "billable_transactions", "merchant_subscriptions",
        "transaction_fee_tiers", "pricing_versions", "plan_features",
        "subscription_plans",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for perm in ("billing:read", "billing:manage"):
        op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name = '{perm}')")
        op.execute(f"DELETE FROM permissions WHERE name = '{perm}'")
