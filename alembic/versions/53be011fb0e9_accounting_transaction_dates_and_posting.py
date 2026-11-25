"""accounting transaction dates and posting

Revision ID: 53be011fb0e9
Revises: f623d73e3125
Create Date: 2026-11-25 09:40:06.242802
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53be011fb0e9'
down_revision: Union[str, None] = 'f623d73e3125'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_trial_balance(uuid,text,uuid)")
    op.execute("DROP FUNCTION IF EXISTS fn_profit_and_loss(uuid,text,text,uuid)")

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_post_journal(
            p_tenant_id UUID,
            p_uid UUID,
            p_desc TEXT,
            p_entries JSONB,
            p_ref_id UUID DEFAULT NULL,
            p_ref_type VARCHAR DEFAULT NULL,
            p_store_id UUID DEFAULT NULL,
            p_accounting_date DATE DEFAULT NULL
        ) RETURNS UUID AS $$
        DECLARE
            j_id UUID := gen_random_uuid();
            j_number VARCHAR;
            entry JSONB;
            total_debit NUMERIC := 0;
            total_credit NUMERIC := 0;
            v_accounting_date DATE := COALESCE(p_accounting_date, CURRENT_DATE);
        BEGIN
            j_number := 'JRN-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' ||
                        UPPER(SUBSTR(j_id::TEXT, 1, 8));

            IF jsonb_array_length(COALESCE(p_entries, '[]'::jsonb)) < 2 THEN
                RAISE EXCEPTION 'Posted journals require at least two journal entry lines';
            END IF;

            FOR entry IN SELECT * FROM jsonb_array_elements(p_entries)
            LOOP
                total_debit := total_debit + COALESCE((entry->>'debit')::NUMERIC, 0);
                total_credit := total_credit + COALESCE((entry->>'credit')::NUMERIC, 0);

                IF COALESCE((entry->>'account_id')::TEXT, '') = '' THEN
                    RAISE EXCEPTION 'Journal entry has no account_id';
                END IF;
                IF COALESCE((entry->>'debit')::NUMERIC, 0) < 0
                   OR COALESCE((entry->>'credit')::NUMERIC, 0) < 0
                   OR NOT (
                       ((entry->>'debit')::NUMERIC > 0 AND COALESCE((entry->>'credit')::NUMERIC, 0) = 0)
                       OR
                       ((entry->>'credit')::NUMERIC > 0 AND COALESCE((entry->>'debit')::NUMERIC, 0) = 0)
                   ) THEN
                    RAISE EXCEPTION 'Each journal line must have one positive debit or credit side';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM chart_of_accounts a
                    WHERE a.id = (entry->>'account_id')::UUID
                      AND a.tenant_id = p_tenant_id
                      AND a.status = 'active'
                ) THEN
                    RAISE EXCEPTION 'Account % does not belong to tenant %',
                        entry->>'account_id', p_tenant_id;
                END IF;
            END LOOP;

            IF ABS(total_debit - total_credit) > 0.01 THEN
                RAISE EXCEPTION 'Debits (%) must equal credits (%)', total_debit, total_credit;
            END IF;

            INSERT INTO journals (
                id, tenant_id, journal_number, description, reference_id,
                reference_type, status, posted_by, posted_at, store_id,
                accounting_date, created_at
            )
            VALUES (
                j_id, p_tenant_id, j_number, p_desc, p_ref_id, p_ref_type,
                'posted', p_uid, NOW(), p_store_id, v_accounting_date, NOW()
            );

            FOR entry IN SELECT * FROM jsonb_array_elements(p_entries)
            LOOP
                INSERT INTO journal_entries (
                    id, journal_id, tenant_id, account_id, account_code,
                    debit, credit, description, type, status, posted_at, amount
                )
                SELECT
                    gen_random_uuid(), j_id, p_tenant_id, a.id, a.code,
                    COALESCE((entry->>'debit')::NUMERIC, 0),
                    COALESCE((entry->>'credit')::NUMERIC, 0),
                    entry->>'description',
                    a.account_type,
                    'posted', NOW(),
                    COALESCE((entry->>'debit')::NUMERIC, 0)
                        + COALESCE((entry->>'credit')::NUMERIC, 0)
                FROM chart_of_accounts a
                WHERE a.id = (entry->>'account_id')::UUID
                  AND a.tenant_id = p_tenant_id;
            END LOOP;

            RETURN j_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_trial_balance(
            p_tenant_id UUID,
            p_at TEXT DEFAULT NULL,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            account_id UUID,
            account_code VARCHAR,
            account_name VARCHAR,
            account_type VARCHAR,
            debit NUMERIC,
            credit NUMERIC
        ) AS $$
        DECLARE
            v_cutoff DATE := COALESCE(p_at::DATE, CURRENT_DATE);
        BEGIN
            RETURN QUERY
            SELECT c.id, c.code, c.name, c.account_type,
                   GREATEST(
                       COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.debit ELSE 0 END), 0)
                       - COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.credit ELSE 0 END), 0),
                       0
                   ) AS debit,
                   GREATEST(
                       COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.credit ELSE 0 END), 0)
                       - COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.debit ELSE 0 END), 0),
                       0
                   ) AS credit
            FROM chart_of_accounts c
            LEFT JOIN journal_entries je
              ON je.account_id = c.id
             AND je.status = 'posted'
             AND je.tenant_id = p_tenant_id
            LEFT JOIN journals j
              ON j.id = je.journal_id
             AND j.status = 'posted'
             AND j.accounting_date <= v_cutoff
             AND (p_store_id IS NULL OR j.store_id = p_store_id)
            WHERE c.tenant_id = p_tenant_id
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING
                COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.debit ELSE 0 END), 0)
                <> COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.credit ELSE 0 END), 0)
            ORDER BY c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_profit_and_loss(
            p_tenant_id UUID,
            p_from TEXT,
            p_to TEXT,
            p_store_id UUID DEFAULT NULL
        ) RETURNS TABLE (
            account_id UUID,
            account_code VARCHAR,
            account_name VARCHAR,
            amount NUMERIC
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT c.id, c.code, c.name,
                   CASE
                       WHEN c.account_type = 'revenue'
                       THEN COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.credit ELSE 0 END), 0)
                            - COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.debit ELSE 0 END), 0)
                       ELSE COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.debit ELSE 0 END), 0)
                            - COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.credit ELSE 0 END), 0)
                   END AS amount
            FROM chart_of_accounts c
            LEFT JOIN journal_entries je
              ON je.account_id = c.id
             AND je.status = 'posted'
             AND je.tenant_id = p_tenant_id
            LEFT JOIN journals j
              ON j.id = je.journal_id
             AND j.status = 'posted'
             AND j.accounting_date BETWEEN p_from::DATE AND p_to::DATE
             AND (p_store_id IS NULL OR j.store_id = p_store_id)
            WHERE c.tenant_id = p_tenant_id
              AND c.account_type IN ('revenue', 'expense')
            GROUP BY c.id, c.code, c.name, c.account_type
            HAVING CASE
                       WHEN c.account_type = 'revenue'
                       THEN COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.credit ELSE 0 END), 0)
                            - COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.debit ELSE 0 END), 0)
                       ELSE COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.debit ELSE 0 END), 0)
                            - COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN je.credit ELSE 0 END), 0)
                   END <> 0
            ORDER BY c.account_type, c.code;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_post_journal(
            p_tenant_id UUID, p_uid UUID, p_desc TEXT,
            p_entries JSONB, p_ref_id UUID DEFAULT NULL,
            p_ref_type VARCHAR DEFAULT NULL, p_store_id UUID DEFAULT NULL
        ) RETURNS UUID AS $$
        BEGIN
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)
