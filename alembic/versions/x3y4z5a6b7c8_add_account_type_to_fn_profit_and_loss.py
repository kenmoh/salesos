"""add account_type to fn_profit_and_loss

The P&L endpoint lumped every row into ``revenue`` because the function's
return type had no ``account_type`` column, so the Python wrapper could not
split revenue from expenses (expenses were hardcoded to []).

Recreates ``fn_profit_and_loss`` with ``account_type`` in the return table.
Postgres cannot change a function's return type with CREATE OR REPLACE, so
the function is dropped first. The aggregation logic is unchanged.

Revision ID: x3y4z5a6b7c8
Revises: w2x3y4z5a6b7
"""

from alembic import op

revision = "x3y4z5a6b7c8"
down_revision = "w2x3y4z5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Separate statements: asyncpg rejects multiple commands per statement.
    op.execute("DROP FUNCTION IF EXISTS fn_profit_and_loss(uuid, text, text);")
    op.execute("""
        CREATE FUNCTION fn_profit_and_loss(
            p_tenant_id UUID, p_from TEXT, p_to TEXT
        ) RETURNS TABLE (
            account_id UUID, account_code VARCHAR, account_name VARCHAR,
            account_type VARCHAR, amount NUMERIC
        ) AS $$
        DECLARE cutoff TIMESTAMPTZ;
        BEGIN
            cutoff := (p_to::DATE + INTERVAL '1 day')::TIMESTAMPTZ;
            RETURN QUERY
            SELECT c.id, c.code, c.name, c.account_type,
                   CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END AS amount
            FROM chart_of_accounts c
            LEFT JOIN journal_entries je ON je.account_id = c.id
                AND je.status = 'posted' AND (je.posted_at IS NULL OR (je.posted_at >= p_from::DATE AND je.posted_at <= cutoff))
            WHERE c.tenant_id = p_tenant_id AND c.account_type IN ('revenue', 'expense')
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END != 0
            ORDER BY c.account_type, c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_profit_and_loss(uuid, text, text, varchar, numeric);")
    op.execute("""
        CREATE FUNCTION fn_profit_and_loss(
            p_tenant_id UUID, p_from TEXT, p_to TEXT
        ) RETURNS TABLE (
            account_id UUID, account_code VARCHAR, account_name VARCHAR, amount NUMERIC
        ) AS $$
        DECLARE cutoff TIMESTAMPTZ;
        BEGIN
            cutoff := (p_to::DATE + INTERVAL '1 day')::TIMESTAMPTZ;
            RETURN QUERY
            SELECT c.id, c.code, c.name,
                   CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END AS amount
            FROM chart_of_accounts c
            LEFT JOIN journal_entries je ON je.account_id = c.id
                AND je.status = 'posted' AND (je.posted_at IS NULL OR (je.posted_at >= p_from::DATE AND je.posted_at <= cutoff))
            WHERE c.tenant_id = p_tenant_id AND c.account_type IN ('revenue', 'expense')
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING CASE WHEN c.account_type = 'revenue' THEN COALESCE(SUM(je.credit), 0) - COALESCE(SUM(je.debit), 0)
                        ELSE COALESCE(SUM(je.debit), 0) - COALESCE(SUM(je.credit), 0) END != 0
            ORDER BY c.account_type, c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)
