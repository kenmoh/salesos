"""add AR/AP payment listing functions

The ar_payments / ap_payments ledgers had no read path: a part-paid invoice
showed a shrunken balance with no way to see which payments got it there.

Adds fn_list_ar_payments / fn_list_ap_payments, returning one ledger row per
payment newest first.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_list_ar_payments(uuid,uuid)")
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_list_ar_payments(
            p_tenant_id UUID, p_ar_id UUID
        ) RETURNS TABLE (
            id UUID, ar_id UUID, amount NUMERIC, payment_date DATE,
            notes TEXT, recorded_by UUID, created_at TIMESTAMPTZ
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT p.id, p.ar_id, p.amount, p.payment_date, p.notes,
                   p.recorded_by, p.created_at
            FROM ar_payments p
            WHERE p.tenant_id = p_tenant_id AND p.ar_id = p_ar_id
            ORDER BY p.payment_date DESC, p.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("DROP FUNCTION IF EXISTS fn_list_ap_payments(uuid,uuid)")
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_list_ap_payments(
            p_tenant_id UUID, p_ap_id UUID
        ) RETURNS TABLE (
            id UUID, ap_id UUID, amount NUMERIC, payment_date DATE,
            notes TEXT, recorded_by UUID, created_at TIMESTAMPTZ
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT p.id, p.ap_id, p.amount, p.payment_date, p.notes,
                   p.recorded_by, p.created_at
            FROM ap_payments p
            WHERE p.tenant_id = p_tenant_id AND p.ap_id = p_ap_id
            ORDER BY p.payment_date DESC, p.created_at DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_list_ar_payments(uuid,uuid)")
    op.execute("DROP FUNCTION IF EXISTS fn_list_ap_payments(uuid,uuid)")
