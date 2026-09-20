"""fix get_store_history missing fields

Revision ID: p5q6r7s8t9u0
Revises: o3p4q5r6s7t8
Create Date: 2026-09-20
"""

from alembic import op

revision = "p5q6r7s8t9u0"
down_revision = "o3p4q5r6s7t8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION get_store_history(
            p_tenant_id UUID,
            p_store_id UUID DEFAULT NULL,
            p_product_id UUID DEFAULT NULL,
            p_page INT DEFAULT 1,
            p_page_size INT DEFAULT 50
        ) RETURNS JSONB AS $$
        DECLARE
            result JSONB;
            data_arr JSONB;
            total_count INT;
            offset_val INT;
        BEGIN
            offset_val := (p_page - 1) * p_page_size;

            SELECT COUNT(*)
            INTO total_count
            FROM stock_movements sm
            WHERE sm.tenant_id = p_tenant_id
              AND (p_store_id IS NULL OR sm.store_id = p_store_id)
              AND (p_product_id IS NULL OR sm.product_id = p_product_id);

            SELECT jsonb_agg(row_to_json(t))
            INTO data_arr
            FROM (
                SELECT sm.id, sm.product_id, p.name AS product_name,
                       p.sku AS product_sku,
                       sm.store_id, st.name AS store_name,
                       sm.movement_type, sm.qty_change,
                       sm.balance_before, sm.balance_after,
                       sm.reference_type, sm.reference_id,
                       sm.reason, sm.unit_cost, sm.notes,
                       sm.created_by, sm.created_at
                FROM stock_movements sm
                JOIN products p ON p.id = sm.product_id
                JOIN stores st ON st.id = sm.store_id
                WHERE sm.tenant_id = p_tenant_id
                  AND (p_store_id IS NULL OR sm.store_id = p_store_id)
                  AND (p_product_id IS NULL OR sm.product_id = p_product_id)
                ORDER BY sm.created_at DESC
                LIMIT p_page_size OFFSET offset_val
            ) t;

            result := jsonb_build_object(
                'data', COALESCE(data_arr, '[]'::jsonb),
                'total', total_count,
                'page', p_page,
                'page_size', p_page_size
            );

            RETURN result;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION get_store_history(
            p_tenant_id UUID,
            p_store_id UUID DEFAULT NULL,
            p_product_id UUID DEFAULT NULL,
            p_page INT DEFAULT 1,
            p_page_size INT DEFAULT 50
        ) RETURNS JSONB AS $$
        DECLARE
            result JSONB;
            data_arr JSONB;
            total_count INT;
            offset_val INT;
        BEGIN
            offset_val := (p_page - 1) * p_page_size;

            SELECT COUNT(*)
            INTO total_count
            FROM stock_movements sm
            WHERE sm.tenant_id = p_tenant_id
              AND (p_store_id IS NULL OR sm.store_id = p_store_id)
              AND (p_product_id IS NULL OR sm.product_id = p_product_id);

            SELECT jsonb_agg(row_to_json(t))
            INTO data_arr
            FROM (
                SELECT sm.id, sm.product_id, p.name AS product_name,
                       sm.store_id, st.name AS store_name,
                       sm.movement_type, sm.qty_change,
                       sm.balance_before, sm.balance_after,
                       sm.reference_type, sm.reference_id,
                       sm.reason, sm.notes, sm.created_at
                FROM stock_movements sm
                JOIN products p ON p.id = sm.product_id
                JOIN stores st ON st.id = sm.store_id
                WHERE sm.tenant_id = p_tenant_id
                  AND (p_store_id IS NULL OR sm.store_id = p_store_id)
                  AND (p_product_id IS NULL OR sm.product_id = p_product_id)
                ORDER BY sm.created_at DESC
                LIMIT p_page_size OFFSET offset_val
            ) t;

            result := jsonb_build_object(
                'data', COALESCE(data_arr, '[]'::jsonb),
                'total', total_count,
                'page', p_page,
                'page_size', p_page_size
            );

            RETURN result;
        END;
        $$ LANGUAGE plpgsql;
    """)
