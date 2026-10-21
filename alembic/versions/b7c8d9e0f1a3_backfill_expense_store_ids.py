"""Tag legacy store-less expenses (and their journals) with the tenant's store.

Expenses created before the store_id column existed (and before fn_create_expense
gained p_store_id) carry a NULL store, so they vanish from the expenses list as
soon as a store is picked in the app. Receivables and payables are fully tagged
by the sale flow, which made the expenses list the odd one out.

Fully backfill a NULL-store expense only when its tenant has exactly one store
-- with one store there is no ambiguity about where the expense belongs, while
multi-store tenants keep their NULL rows for manual correction through the
move-to-store tools. The tenant's journals are re-tagged too so per-store cash
flow counts the expense's outflow.
"""

revision = "b7c8d9e0f1a3"
down_revision = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        WITH single_store AS (
            SELECT tenant_id, store_id AS only_store
            FROM (
                SELECT t.id AS tenant_id, st.id AS store_id,
                       COUNT(*) OVER (PARTITION BY t.id) AS store_count
                FROM tenants t
                JOIN stores st ON st.tenant_id = t.id
            ) s
            WHERE s.store_count = 1
        ),
        tagged AS (
            UPDATE expenses e
            SET store_id = ss.only_store
            FROM single_store ss
            WHERE e.tenant_id = ss.tenant_id
              AND e.store_id IS NULL
            RETURNING e.id, e.journal_id, ss.only_store
        ),
        retag_journals AS (
            UPDATE journals j
            SET store_id = t.only_store
            FROM tagged t
            WHERE t.journal_id = j.id
              AND j.store_id IS NULL
            RETURNING 1
        )
        SELECT 1
    """)


def downgrade() -> None:
    pass