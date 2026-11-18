"""add cashier tips ledger and settlements

A positive per-line ``extra`` is money handed to the cashier, but it lived
only on the sale line: there was no way to see what a cashier had earned, and
no way to record that it had been paid out. This adds:

1. ``cashier_tips`` -- one row per sale item with a positive ``extra``, tied to
   the cashier and the sale timestamp.
2. ``cashier_tip_settlements`` -- an immutable paid batch for one cashier over
   an inclusive date range.
3. ``tips:read`` / ``tips:settle`` permissions, granted to existing roles.
4. A backfill of tips from completed sales already in the database.

Revision ID: t1i2p3s4x5y6
Revises: y8f9e0d1c2b3
"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "t1i2p3s4x5y6"
down_revision = "y8f9e0d1c2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Settlement batches (referenced by tips, so created first) ─────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS cashier_tip_settlements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            cashier_id UUID NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            amount NUMERIC(15, 2) NOT NULL CHECK (amount > 0),
            tip_count INTEGER NOT NULL CHECK (tip_count > 0),
            status VARCHAR(20) NOT NULL DEFAULT 'paid',
            note TEXT,
            paid_by UUID NOT NULL,
            paid_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (period_end >= period_start)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cashier_tip_settlements_tenant_cashier "
        "ON cashier_tip_settlements(tenant_id, cashier_id, paid_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cashier_tip_settlements_tenant_paid "
        "ON cashier_tip_settlements(tenant_id, paid_at DESC)"
    )

    # ── Individual tips ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS cashier_tips (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            sale_id UUID NOT NULL,
            sale_item_id UUID NOT NULL,
            cashier_id UUID NOT NULL,
            store_id UUID NOT NULL,
            extra NUMERIC(15, 2) NOT NULL,
            qty NUMERIC(10, 2) NOT NULL,
            amount NUMERIC(15, 2) NOT NULL CHECK (amount > 0),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            earned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            settlement_id UUID,
            settled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_cashier_tips_sale_item UNIQUE (sale_item_id),
            CONSTRAINT fk_cashier_tips_settlement
                FOREIGN KEY (settlement_id)
                REFERENCES cashier_tip_settlements(id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cashier_tips_tenant_cashier_status "
        "ON cashier_tips(tenant_id, cashier_id, status, earned_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cashier_tips_tenant_sale "
        "ON cashier_tips(tenant_id, sale_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cashier_tips_tenant_earned "
        "ON cashier_tips(tenant_id, earned_at DESC)"
    )

    # Same tenant isolation as every other tenant-scoped table.
    for table in ("cashier_tips", "cashier_tip_settlements"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id::text = current_setting('app.business_id', true))
                WITH CHECK (tenant_id::text = current_setting('app.business_id', true))
        """)

    # ── Permissions ───────────────────────────────────────────────────────
    for perm_name, perm_desc in (
        ("tips:read", "View cashier tips and settlement history"),
        ("tips:settle", "Settle cashier tips for a date range"),
    ):
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, name, description) "
                "VALUES (:id, :name, :desc) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"id": str(uuid4()), "name": perm_name, "desc": perm_desc},
        )

    # Owners and managers settle; cashiers can only read (their own).
    role_grants = (
        ("owner", "tips:read"),
        ("owner", "tips:settle"),
        ("manager", "tips:read"),
        ("manager", "tips:settle"),
        ("cashier", "tips:read"),
    )
    for role_name, perm_name in role_grants:
        conn.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = :role_name AND p.name = :perm_name "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_name": role_name, "perm_name": perm_name},
        )

    # ── Backfill completed sales already in the database ──────────────────
    op.execute("""
        INSERT INTO cashier_tips (
            tenant_id, sale_id, sale_item_id, cashier_id, store_id,
            extra, qty, amount, status, earned_at, created_at
        )
        SELECT
            s.tenant_id,
            s.id,
            si.id,
            s.cashier_id,
            s.store_id,
            si.extra,
            si.qty,
            ROUND(si.extra * si.qty, 2),
            'pending',
            s.created_at,
            NOW()
        FROM sales s
        JOIN sale_items si ON si.sale_id = s.id
        WHERE s.status = 'completed'
          AND s.cashier_id IS NOT NULL
          AND si.extra > 0
          AND si.qty > 0
          AND ROUND(si.extra * si.qty, 2) > 0
        ON CONFLICT (sale_item_id) DO NOTHING
    """)


def downgrade() -> None:
    conn = op.get_bind()

    op.execute("DROP TABLE IF EXISTS cashier_tips")
    op.execute("DROP TABLE IF EXISTS cashier_tip_settlements")

    for perm_name in ("tips:read", "tips:settle"):
        perm_row = conn.execute(
            sa.text("SELECT id FROM permissions WHERE name = :name"),
            {"name": perm_name},
        ).fetchone()
        if perm_row:
            conn.execute(
                sa.text("DELETE FROM role_permissions WHERE permission_id = :id"),
                {"id": str(perm_row[0])},
            )
            conn.execute(
                sa.text("DELETE FROM permissions WHERE name = :name"),
                {"name": perm_name},
            )
