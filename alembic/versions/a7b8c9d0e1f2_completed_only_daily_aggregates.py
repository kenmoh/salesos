"""analytics stop counting voided sales as sales

mv_daily_sales, mv_store_sales and mv_cashier_performance were built with
COUNT(*)/SUM(...) over rows whose status is completed OR voided, so revenue,
sales count, discounts, tax and average order value all included voided
sales. The voided_count/voided_amount columns on the same views exist
precisely because voided sales were meant to be reported apart, and every
dashboard card and report screen number sits on top of these three views.

This recreates the three views counting completed sales only. A day that is
all voided still produces a row, with its totals at zero and the voided
figures unchanged, so the shape callers read does not move. mv_store_sales
also gains total_discounts and total_tax, carried the same way mv_daily_sales
already carries them, because a single store has to be reportable with the
columns the tenant view has.

Revision ID: a7b8c9d0e1f2
Revises: c4d5e6f70819
"""

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "c4d5e6f70819"
branch_labels = None
depends_on = None


DAILY_SALES = """
    CREATE MATERIALIZED VIEW mv_daily_sales AS
    SELECT
        s.tenant_id,
        DATE(s.created_at) AS date,
        COUNT(*) FILTER (WHERE s.status = 'completed') AS total_sales,
        COALESCE(SUM(s.total) FILTER (WHERE s.status = 'completed'), 0) AS total_revenue,
        COALESCE(SUM(s.discount) FILTER (WHERE s.status = 'completed'), 0) AS total_discounts,
        COALESCE(SUM(s.tax) FILTER (WHERE s.status = 'completed'), 0) AS total_tax,
        AVG(s.total) FILTER (WHERE s.status = 'completed') AS avg_order_value,
        COUNT(*) FILTER (WHERE s.status = 'voided') AS voided_count,
        COALESCE(SUM(s.total) FILTER (WHERE s.status = 'voided'), 0) AS voided_amount
    FROM sales s
    WHERE s.status IN ('completed', 'voided')
    GROUP BY s.tenant_id, DATE(s.created_at);
"""

STORE_SALES = """
    CREATE MATERIALIZED VIEW mv_store_sales AS
    SELECT
        s.tenant_id,
        s.store_id,
        st.name AS store_name,
        DATE(s.created_at) AS date,
        COUNT(*) FILTER (WHERE s.status = 'completed') AS total_sales,
        COALESCE(SUM(s.total) FILTER (WHERE s.status = 'completed'), 0) AS total_revenue,
        COALESCE(SUM(s.discount) FILTER (WHERE s.status = 'completed'), 0) AS total_discounts,
        COALESCE(SUM(s.tax) FILTER (WHERE s.status = 'completed'), 0) AS total_tax,
        COALESCE(SUM(s.total) FILTER (WHERE s.status = 'voided'), 0) AS voided_amount,
        COUNT(*) FILTER (WHERE s.status = 'voided') AS voided_count,
        AVG(s.total) FILTER (WHERE s.status = 'completed') AS avg_order_value,
        COALESCE(
            SUM(s.total)
            FILTER (WHERE s.status = 'completed' AND s.payment_methods->>'method' = 'cash'),
            0
        ) AS cash_amount,
        COALESCE(
            SUM(s.total)
            FILTER (WHERE s.status = 'completed' AND s.payment_methods->>'method' = 'card'),
            0
        ) AS card_amount,
        COALESCE(
            SUM(s.total)
            FILTER (WHERE s.status = 'completed' AND s.payment_methods->>'method' = 'transfer'),
            0
        ) AS transfer_amount
    FROM sales s
    JOIN stores st ON st.id = s.store_id
    WHERE s.status IN ('completed', 'voided')
    GROUP BY s.tenant_id, s.store_id, st.name, DATE(s.created_at);
"""

CASHIER_PERFORMANCE = """
    CREATE MATERIALIZED VIEW mv_cashier_performance AS
    SELECT
        s.tenant_id,
        s.cashier_id AS user_id,
        DATE(s.created_at) AS date,
        COUNT(*) FILTER (WHERE s.status = 'completed') AS sales_count,
        COALESCE(SUM(s.total) FILTER (WHERE s.status = 'completed'), 0) AS total_revenue,
        AVG(s.total) FILTER (WHERE s.status = 'completed') AS avg_transaction,
        COUNT(*) FILTER (WHERE s.status = 'voided') AS void_count
    FROM sales s
    WHERE s.status IN ('completed', 'voided') AND s.cashier_id IS NOT NULL
    GROUP BY s.tenant_id, s.cashier_id, DATE(s.created_at);
"""

# The definitions this migration replaces, verbatim from
# c1d2e3f4a5b6_add_rpc_functions_and_materialized_views, for downgrade.
DAILY_SALES_OLD = """
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
"""

STORE_SALES_OLD = """
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
"""

CASHIER_PERFORMANCE_OLD = """
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
"""


def upgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_daily_sales;")
    op.execute(DAILY_SALES)
    op.execute("CREATE UNIQUE INDEX idx_mv_daily_sales ON mv_daily_sales(tenant_id, date);")

    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_store_sales;")
    op.execute(STORE_SALES)
    op.execute("CREATE UNIQUE INDEX idx_mv_store_sales ON mv_store_sales(tenant_id, store_id, date);")

    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_cashier_performance;")
    op.execute(CASHIER_PERFORMANCE)
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_cashier_performance "
        "ON mv_cashier_performance(tenant_id, user_id, date);"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_daily_sales;")
    op.execute(DAILY_SALES_OLD)
    op.execute("CREATE UNIQUE INDEX idx_mv_daily_sales ON mv_daily_sales(tenant_id, date);")

    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_store_sales;")
    op.execute(STORE_SALES_OLD)
    op.execute("CREATE UNIQUE INDEX idx_mv_store_sales ON mv_store_sales(tenant_id, store_id, date);")

    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_cashier_performance;")
    op.execute(CASHIER_PERFORMANCE_OLD)
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_cashier_performance "
        "ON mv_cashier_performance(tenant_id, user_id, date);"
    )
