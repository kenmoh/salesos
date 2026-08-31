"""add_rpc_functions_and_materialized_views

Revision ID: c1d2e3f4a5b6
Revises: b88ef4f330c2
Create Date: 2026-08-31

"""
from alembic import op


revision = "c1d2e3f4a5b6"
down_revision = "b88ef4f330c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── RPC Functions ─────────────────────────────────────────────────────

    op.execute("""
        CREATE OR REPLACE FUNCTION get_store_details(
            p_tenant_id UUID,
            p_store_id UUID,
            p_from_date DATE DEFAULT NULL,
            p_to_date DATE DEFAULT NULL
        ) RETURNS JSONB AS $$
        DECLARE
            result JSONB;
            store_rec RECORD;
            total_products INT;
            total_stock NUMERIC;
            low_stock_count INT;
            total_sales INT;
            total_revenue NUMERIC;
        BEGIN
            SELECT s.id, s.name, s.address, s.is_warehouse, s.status
            INTO store_rec
            FROM stores s
            WHERE s.id = p_store_id AND s.tenant_id = p_tenant_id;

            IF store_rec IS NULL THEN
                RETURN '{"error": "Store not found"}'::jsonb;
            END IF;

            SELECT COUNT(DISTINCT sb.product_id), COALESCE(SUM(sb.qty), 0)
            INTO total_products, total_stock
            FROM stock_balances sb
            WHERE sb.store_id = p_store_id AND sb.tenant_id = p_tenant_id;

            SELECT COUNT(*)
            INTO low_stock_count
            FROM stock_balances sb
            JOIN products p ON p.id = sb.product_id
            WHERE sb.store_id = p_store_id
              AND sb.tenant_id = p_tenant_id
              AND (sb.qty - sb.reserved_qty) <= p.reorder_point;

            SELECT COUNT(*), COALESCE(SUM(s.total), 0)
            INTO total_sales, total_revenue
            FROM sales s
            WHERE s.store_id = p_store_id
              AND s.tenant_id = p_tenant_id
              AND s.status = 'completed'
              AND (p_from_date IS NULL OR s.created_at::date >= p_from_date)
              AND (p_to_date IS NULL OR s.created_at::date <= p_to_date);

            result := jsonb_build_object(
                'store_id', store_rec.id,
                'name', store_rec.name,
                'address', store_rec.address,
                'is_warehouse', store_rec.is_warehouse,
                'status', store_rec.status,
                'total_products', total_products,
                'total_stock', total_stock,
                'low_stock_count', low_stock_count,
                'total_sales', total_sales,
                'total_revenue', total_revenue
            );

            RETURN result;
        END;
        $$ LANGUAGE plpgsql;
    """)

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

    op.execute("""
        CREATE OR REPLACE FUNCTION get_store_product_detail(
            p_tenant_id UUID,
            p_store_id UUID,
            p_product_id UUID
        ) RETURNS JSONB AS $$
        DECLARE
            result JSONB;
            prod_rec RECORD;
            stock_rec RECORD;
            history_arr JSONB;
        BEGIN
            SELECT sp.id, sp.name, sp.sku, sp.selling_price, sp.cost_price,
                   sp.image_url, sp.status
            INTO prod_rec
            FROM store_products sp
            WHERE sp.tenant_id = p_tenant_id
              AND sp.store_id = p_store_id
              AND sp.product_id = p_product_id;

            IF prod_rec IS NULL THEN
                RETURN '{"error": "Product not found in this store"}'::jsonb;
            END IF;

            SELECT sb.qty, sb.reserved_qty, sb.min_stock_level,
                   (sb.qty - sb.reserved_qty) AS available
            INTO stock_rec
            FROM stock_balances sb
            WHERE sb.tenant_id = p_tenant_id
              AND sb.store_id = p_store_id
              AND sb.product_id = p_product_id;

            SELECT jsonb_agg(row_to_json(t))
            INTO history_arr
            FROM (
                SELECT sm.id, sm.movement_type, sm.qty_change,
                       sm.balance_before, sm.balance_after,
                       sm.reason, sm.created_at
                FROM stock_movements sm
                WHERE sm.tenant_id = p_tenant_id
                  AND sm.store_id = p_store_id
                  AND sm.product_id = p_product_id
                ORDER BY sm.created_at DESC
                LIMIT 10
            ) t;

            result := jsonb_build_object(
                'product', row_to_json(prod_rec),
                'stock', CASE WHEN stock_rec IS NOT NULL THEN row_to_json(stock_rec) ELSE NULL END,
                'recent_history', COALESCE(history_arr, '[]'::jsonb)
            );

            RETURN result;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ─── Materialized Views ───────────────────────────────────────────────

    op.execute("""
        CREATE MATERIALIZED VIEW mv_daily_sales AS
        SELECT
            s.tenant_id,
            DATE(s.created_at) AS date,
            COUNT(*) AS total_sales,
            COALESCE(SUM(s.total), 0) AS total_revenue,
            COALESCE(SUM(s.discount), 0) AS total_discounts,
            COALESCE(SUM(s.tax), 0) AS total_tax,
            AVG(s.total) AS avg_order_value,
            COUNT(*) FILTER (WHERE s.status = 'voided') AS voided_count,
            COALESCE(SUM(s.total) FILTER (WHERE s.status = 'voided'), 0) AS voided_amount
        FROM sales s
        WHERE s.status IN ('completed', 'voided')
        GROUP BY s.tenant_id, DATE(s.created_at);
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_daily_sales ON mv_daily_sales(tenant_id, date);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_product_rankings AS
        SELECT
            s.tenant_id,
            si.product_id,
            si.product_name,
            p.sku,
            DATE(s.created_at) AS date,
            SUM(si.qty) AS units_sold,
            SUM(si.line_total) AS revenue,
            AVG(si.unit_price) AS avg_selling_price,
            p.cost_price
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE s.status = 'completed'
        GROUP BY s.tenant_id, si.product_id, si.product_name, p.sku, p.cost_price, DATE(s.created_at);
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_product_rankings ON mv_product_rankings(tenant_id, product_id, date);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_payment_methods AS
        SELECT
            s.tenant_id,
            DATE(s.created_at) AS date,
            s.payment_methods->>'method' AS method,
            COUNT(*) AS payment_count,
            COALESCE(SUM(s.total), 0) AS total_amount
        FROM sales s
        WHERE s.status = 'completed' AND s.payment_methods IS NOT NULL
        GROUP BY s.tenant_id, DATE(s.created_at), s.payment_methods->>'method';
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_payment_methods ON mv_payment_methods(tenant_id, date, method);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_cashier_performance AS
        SELECT
            s.tenant_id,
            s.cashier_id AS user_id,
            DATE(s.created_at) AS date,
            COUNT(*) AS sales_count,
            COALESCE(SUM(s.total), 0) AS total_revenue,
            AVG(s.total) AS avg_transaction,
            COUNT(*) FILTER (WHERE s.status = 'voided') AS void_count
        FROM sales s
        WHERE s.status IN ('completed', 'voided') AND s.cashier_id IS NOT NULL
        GROUP BY s.tenant_id, s.cashier_id, DATE(s.created_at);
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_cashier_performance ON mv_cashier_performance(tenant_id, user_id, date);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_customer_summary AS
        SELECT
            s.tenant_id,
            COALESCE(s.customer_name, 'Walk-in') AS customer_key,
            MIN(s.created_at) AS first_purchase,
            MAX(s.created_at) AS last_purchase,
            COUNT(*) AS total_purchases,
            COALESCE(SUM(s.total), 0) AS total_revenue,
            AVG(s.total) AS avg_order_value
        FROM sales s
        WHERE s.status = 'completed'
        GROUP BY s.tenant_id, COALESCE(s.customer_name, 'Walk-in');
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_customer_summary ON mv_customer_summary(tenant_id, customer_key);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_inventory_status AS
        SELECT
            sb.tenant_id,
            sb.product_id,
            p.name AS product_name,
            p.sku,
            sb.store_id,
            sb.qty AS current_qty,
            p.reorder_point,
            CASE
                WHEN (sb.qty - sb.reserved_qty) <= 0 THEN 'out_of_stock'
                WHEN (sb.qty - sb.reserved_qty) <= p.reorder_point THEN 'low_stock'
                ELSE 'in_stock'
            END AS status
        FROM stock_balances sb
        JOIN products p ON p.id = sb.product_id;
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_inventory_status ON mv_inventory_status(tenant_id, product_id, store_id);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_store_sales AS
        SELECT
            s.tenant_id,
            s.store_id,
            st.name AS store_name,
            DATE(s.created_at) AS date,
            COUNT(*) AS total_sales,
            COALESCE(SUM(s.total), 0) AS total_revenue,
            COALESCE(SUM(s.total) FILTER (WHERE s.status = 'voided'), 0) AS voided_amount,
            COUNT(*) FILTER (WHERE s.status = 'voided') AS voided_count,
            AVG(s.total) AS avg_order_value,
            COALESCE(SUM(s.total) FILTER (WHERE s.payment_methods->>'method' = 'cash'), 0) AS cash_amount,
            COALESCE(SUM(s.total) FILTER (WHERE s.payment_methods->>'method' = 'card'), 0) AS card_amount,
            COALESCE(SUM(s.total) FILTER (WHERE s.payment_methods->>'method' = 'transfer'), 0) AS transfer_amount
        FROM sales s
        JOIN stores st ON st.id = s.store_id
        WHERE s.status IN ('completed', 'voided')
        GROUP BY s.tenant_id, s.store_id, st.name, DATE(s.created_at);
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_store_sales ON mv_store_sales(tenant_id, store_id, date);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_store_product_rankings AS
        SELECT
            s.tenant_id,
            s.store_id,
            si.product_id,
            si.product_name,
            p.sku,
            DATE(s.created_at) AS date,
            SUM(si.qty) AS units_sold,
            SUM(si.line_total) AS revenue,
            AVG(si.unit_price) AS avg_selling_price
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE s.status = 'completed'
        GROUP BY s.tenant_id, s.store_id, si.product_id, si.product_name, p.sku, DATE(s.created_at);
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_store_product_rankings ON mv_store_product_rankings(tenant_id, store_id, product_id, date);")

    op.execute("""
        CREATE MATERIALIZED VIEW mv_store_inventory AS
        SELECT
            sb.tenant_id,
            sb.store_id,
            st.name AS store_name,
            sb.product_id,
            p.name AS product_name,
            p.sku,
            sb.qty AS current_qty,
            sb.reserved_qty,
            (sb.qty - sb.reserved_qty) AS available_qty,
            p.reorder_point,
            sb.unit_cost,
            CASE
                WHEN (sb.qty - sb.reserved_qty) <= 0 THEN 'out_of_stock'
                WHEN (sb.qty - sb.reserved_qty) <= p.reorder_point THEN 'low_stock'
                ELSE 'in_stock'
            END AS status
        FROM stock_balances sb
        JOIN products p ON p.id = sb.product_id
        JOIN stores st ON st.id = sb.store_id;
    """)
    op.execute("CREATE UNIQUE INDEX idx_mv_store_inventory ON mv_store_inventory(tenant_id, store_id, product_id);")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_store_inventory;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_store_product_rankings;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_store_sales;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_inventory_status;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_customer_summary;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_cashier_performance;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_payment_methods;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_product_rankings;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_daily_sales;")
    op.execute("DROP FUNCTION IF EXISTS get_store_product_detail(UUID, UUID, UUID);")
    op.execute("DROP FUNCTION IF EXISTS get_store_history(UUID, UUID, UUID, INT, INT);")
    op.execute("DROP FUNCTION IF EXISTS get_store_details(UUID, UUID, DATE, DATE);")
