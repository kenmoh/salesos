"""accounting integrity controls

Revision ID: f623d73e3125
Revises: c9f1a2b3d4e5
Create Date: 2026-11-25 09:04:35.322161
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f623d73e3125'
down_revision: Union[str, None] = 'c9f1a2b3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand before constrain. Historical rows keep a usable date during the
    # backfill, then the column becomes required.
    op.add_column("journals", sa.Column("accounting_date", sa.Date(), nullable=True))
    op.execute("""
        UPDATE journals
        SET accounting_date = COALESCE(
            accounting_date,
            (posted_at AT TIME ZONE 'UTC')::date,
            (created_at AT TIME ZONE 'UTC')::date
        )
        WHERE accounting_date IS NULL
    """)
    op.execute("""
        DO $$
        DECLARE
            v_count integer;
        BEGIN
            SELECT COUNT(*) INTO v_count
            FROM journals
            WHERE accounting_date IS NULL;
            IF v_count > 0 THEN
                RAISE EXCEPTION 'Accounting migration stopped: % journals have no accounting date', v_count;
            END IF;
        END $$;
    """)
    op.alter_column("journals", "accounting_date", existing_type=sa.Date(), nullable=False)

    op.add_column(
        "chart_of_accounts",
        sa.Column("cash_flow_class", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "chart_of_accounts",
        sa.Column(
            "is_cash_equivalent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute("""
        UPDATE chart_of_accounts
        SET is_cash_equivalent = true,
            cash_flow_class = 'excluded'
        WHERE code IN ('1000', '1010')
    """)
    op.create_check_constraint(
        "ck_chart_cash_flow_class",
        "chart_of_accounts",
        "cash_flow_class IS NULL OR cash_flow_class IN ('operating','investing','financing','excluded')",
    )
    op.create_check_constraint(
        "ck_chart_cash_equivalent_class",
        "chart_of_accounts",
        "NOT is_cash_equivalent OR cash_flow_class = 'excluded'",
    )

    # Payment preflight. Existing payments must map to exactly one journal.
    op.execute("""
        DO $$
        DECLARE
            v_ar record;
            v_ap record;
        BEGIN
            FOR v_ar IN
                SELECT p.id, p.tenant_id, p.ar_id, p.amount, p.payment_date,
                       ar.store_id,
                       (SELECT COUNT(*)
                        FROM journals j
                        WHERE j.tenant_id = p.tenant_id
                          AND j.reference_type = 'ar_payment'
                          AND (
                              j.reference_id = p.id
                              OR j.reference_id = p.ar_id
                          )
                          AND EXISTS (
                              SELECT 1
                              FROM journal_entries e
                              WHERE e.journal_id = j.id
                                AND e.tenant_id = p.tenant_id
                                AND (e.debit = p.amount OR e.credit = p.amount)
                          )) AS candidate_count
                FROM ar_payments p
                JOIN accounts_receivable ar ON ar.id = p.ar_id AND ar.tenant_id = p.tenant_id
            LOOP
                IF v_ar.candidate_count = 0 THEN
                    RAISE EXCEPTION 'Accounting migration stopped: unmatched AR payment % (tenant %, receivable %)',
                        v_ar.id, v_ar.tenant_id, v_ar.ar_id;
                END IF;
                IF v_ar.candidate_count > 1 THEN
                    RAISE EXCEPTION 'Accounting migration stopped: ambiguous AR payment % (tenant %, % candidates)',
                        v_ar.id, v_ar.tenant_id, v_ar.candidate_count;
                END IF;
            END LOOP;
            FOR v_ap IN
                SELECT p.id, p.tenant_id, p.ap_id, p.amount, p.payment_date,
                       ap.store_id,
                       (SELECT COUNT(*)
                        FROM journals j
                        WHERE j.tenant_id = p.tenant_id
                          AND j.reference_type = 'ap_payment'
                          AND (
                              j.reference_id = p.id
                              OR j.reference_id = p.ap_id
                          )
                          AND EXISTS (
                              SELECT 1
                              FROM journal_entries e
                              WHERE e.journal_id = j.id
                                AND e.tenant_id = p.tenant_id
                                AND (e.debit = p.amount OR e.credit = p.amount)
                          )) AS candidate_count
                FROM ap_payments p
                JOIN accounts_payable ap ON ap.id = p.ap_id AND ap.tenant_id = p.tenant_id
            LOOP
                IF v_ap.candidate_count = 0 THEN
                    RAISE EXCEPTION 'Accounting migration stopped: unmatched AP payment % (tenant %, payable %)',
                        v_ap.id, v_ap.tenant_id, v_ap.ap_id;
                END IF;
                IF v_ap.candidate_count > 1 THEN
                    RAISE EXCEPTION 'Accounting migration stopped: ambiguous AP payment % (tenant %, % candidates)',
                        v_ap.id, v_ap.tenant_id, v_ap.candidate_count;
                END IF;
            END LOOP;
        END $$;
    """)
    op.execute("""
        UPDATE journals j
        SET reference_id = p.id,
            store_id = COALESCE(ar.store_id, j.store_id),
            accounting_date = p.payment_date
        FROM ar_payments p
        JOIN accounts_receivable ar ON ar.id = p.ar_id AND ar.tenant_id = p.tenant_id
        WHERE j.tenant_id = p.tenant_id
          AND j.reference_type = 'ar_payment'
          AND j.reference_id = p.ar_id
          AND EXISTS (
              SELECT 1
              FROM journal_entries e
              WHERE e.journal_id = j.id
                AND e.tenant_id = p.tenant_id
                AND (e.debit = p.amount OR e.credit = p.amount)
          )
    """)
    op.execute("""
        UPDATE journals j
        SET reference_id = p.id,
            store_id = COALESCE(ap.store_id, j.store_id),
            accounting_date = p.payment_date
        FROM ap_payments p
        JOIN accounts_payable ap ON ap.id = p.ap_id AND ap.tenant_id = p.tenant_id
        WHERE j.tenant_id = p.tenant_id
          AND j.reference_type = 'ap_payment'
          AND j.reference_id = p.ap_id
          AND EXISTS (
              SELECT 1
              FROM journal_entries e
              WHERE e.journal_id = j.id
                AND e.tenant_id = p.tenant_id
                AND (e.debit = p.amount OR e.credit = p.amount)
          )
    """)

    for table, payment_type in (("ar_payments", "ar"), ("ap_payments", "ap")):
        op.add_column(table, sa.Column("idempotency_key", sa.String(length=255), nullable=True))
        op.add_column(table, sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("store_id", sa.UUID(), nullable=True))
        op.add_column(table, sa.Column("journal_id", sa.UUID(), nullable=True))
        op.execute(f"""
            UPDATE {table} p
            SET journal_id = j.id,
                store_id = j.store_id
            FROM journals j
            WHERE j.tenant_id = p.tenant_id
              AND j.reference_type = '{payment_type}_payment'
              AND j.reference_id = p.id
        """)
        op.create_index(
            f"ix_{table}_journal_id",
            table,
            ["journal_id"],
            unique=True,
        )
        op.create_index(
            f"uq_{table}_idempotency_scope",
            table,
            ["tenant_id", "idempotency_key", sa.text("COALESCE(store_id::text, '')")],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )
        op.create_check_constraint(
            f"ck_{table}_positive_amount",
            table,
            "amount > 0",
        )

    op.create_check_constraint(
        "ck_ar_positive_balance",
        "accounts_receivable",
        "amount > 0 AND amount_paid >= 0 AND balance >= 0 AND amount = amount_paid + balance",
    )
    op.create_check_constraint(
        "ck_ap_positive_balance",
        "accounts_payable",
        "amount > 0 AND amount_paid >= 0 AND balance >= 0 AND amount = amount_paid + balance",
    )
    op.create_check_constraint(
        "ck_journal_entry_amount",
        "journal_entries",
        "debit >= 0 AND credit >= 0 AND ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_journal_entry_amount", "journal_entries", type_="check")
    op.drop_constraint("ck_ap_positive_balance", "accounts_payable", type_="check")
    op.drop_constraint("ck_ar_positive_balance", "accounts_receivable", type_="check")
    for table in ("ar_payments", "ap_payments"):
        op.drop_constraint(f"ck_{table}_positive_amount", table, type_="check")
        op.drop_index(f"uq_{table}_idempotency_scope", table_name=table)
        op.drop_index(f"ix_{table}_journal_id", table_name=table)
        op.drop_column(table, "journal_id")
        op.drop_column(table, "store_id")
        op.drop_column(table, "request_fingerprint")
        op.drop_column(table, "idempotency_key")
    op.drop_constraint("ck_chart_cash_equivalent_class", "chart_of_accounts", type_="check")
    op.drop_constraint("ck_chart_cash_flow_class", "chart_of_accounts", type_="check")
    op.drop_column("chart_of_accounts", "is_cash_equivalent")
    op.drop_column("chart_of_accounts", "cash_flow_class")
    op.drop_column("journals", "accounting_date")
