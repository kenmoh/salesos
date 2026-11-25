"""make AR and AP payment posting idempotent"""

from typing import Sequence, Union

from alembic import op


revision: str = "d73a1f0c4b8e"
down_revision: Union[str, None] = "53be011fb0e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_record_ar_payment(
            p_tenant_id UUID,
            p_ar_id UUID,
            p_amount NUMERIC,
            p_payment_date TEXT,
            p_notes TEXT,
            p_recorded_by UUID,
            p_idempotency_key TEXT,
            p_request_fingerprint TEXT,
            p_store_id UUID
        ) RETURNS TABLE (
            out_id UUID,
            out_tenant_id UUID,
            customer_id UUID,
            customer_name VARCHAR,
            invoice_number VARCHAR,
            amount NUMERIC,
            amount_paid NUMERIC,
            balance NUMERIC,
            due_date TIMESTAMPTZ,
            status VARCHAR,
            out_store_id UUID,
            out_payment_id UUID,
            out_journal_id UUID,
            out_replayed BOOLEAN,
            out_error TEXT
        ) AS $$
        DECLARE
            v_row accounts_receivable%ROWTYPE;
            v_existing ar_payments%ROWTYPE;
            v_payment_id UUID := gen_random_uuid();
            v_date DATE;
            v_new_paid NUMERIC;
            v_new_balance NUMERIC;
            v_new_status VARCHAR;
            v_error TEXT;
        BEGIN
            SELECT * INTO v_row
            FROM accounts_receivable
            WHERE id = p_ar_id AND tenant_id = p_tenant_id
            FOR UPDATE;

            IF NOT FOUND THEN
                RETURN QUERY SELECT
                    p_ar_id, p_tenant_id, NULL::UUID, NULL::VARCHAR, NULL::VARCHAR,
                    NULL::NUMERIC, NULL::NUMERIC, NULL::NUMERIC, NULL::TIMESTAMPTZ,
                    NULL::VARCHAR, NULL::UUID, NULL::UUID, NULL::UUID, false,
                    'Receivable not found'::TEXT;
                RETURN;
            END IF;

            IF p_idempotency_key IS NULL
               OR length(btrim(p_idempotency_key)) = 0
               OR length(p_idempotency_key) > 255
               OR p_request_fingerprint IS NULL
               OR p_request_fingerprint !~ '^[0-9a-f]{64}$' THEN
                v_error := 'Idempotency-Key or request fingerprint is invalid';
            ELSIF p_store_id IS DISTINCT FROM v_row.store_id THEN
                v_error := 'Receivable store changed while recording payment';
            END IF;

            IF v_error IS NOT NULL THEN
                RETURN QUERY SELECT
                    v_row.id, v_row.tenant_id, v_row.customer_id, v_row.customer_name,
                    v_row.invoice_number, v_row.amount, v_row.amount_paid, v_row.balance,
                    v_row.due_date, v_row.status, v_row.store_id, NULL::UUID,
                    NULL::UUID, false, v_error;
                RETURN;
            END IF;

            SELECT * INTO v_existing
            FROM ar_payments p
            WHERE p.tenant_id = p_tenant_id
              AND p.idempotency_key = p_idempotency_key
              AND p.store_id IS NOT DISTINCT FROM v_row.store_id
            LIMIT 1;

            IF FOUND THEN
                IF v_existing.ar_id <> p_ar_id
                   OR v_existing.request_fingerprint IS DISTINCT FROM p_request_fingerprint THEN
                    v_error := 'Idempotency-Key has already been used with a different request';
                ELSE
                    RETURN QUERY SELECT
                        v_row.id, v_row.tenant_id, v_row.customer_id, v_row.customer_name,
                        v_row.invoice_number, v_row.amount, v_row.amount_paid, v_row.balance,
                        v_row.due_date, v_row.status, v_existing.store_id,
                        v_existing.id, v_existing.journal_id, true, NULL::TEXT;
                    RETURN;
                END IF;

                RETURN QUERY SELECT
                    v_row.id, v_row.tenant_id, v_row.customer_id, v_row.customer_name,
                    v_row.invoice_number, v_row.amount, v_row.amount_paid, v_row.balance,
                    v_row.due_date, v_row.status, v_existing.store_id,
                    v_existing.id, v_existing.journal_id, false, v_error;
                RETURN;
            END IF;

            IF p_amount IS NULL OR p_amount <= 0 THEN
                v_error := 'Payment amount must be greater than 0';
            ELSIF p_amount > v_row.balance THEN
                v_error := 'Payment amount exceeds the remaining balance of '
                           || v_row.balance::TEXT;
            END IF;

            IF v_error IS NOT NULL THEN
                RETURN QUERY SELECT
                    v_row.id, v_row.tenant_id, v_row.customer_id, v_row.customer_name,
                    v_row.invoice_number, v_row.amount, v_row.amount_paid, v_row.balance,
                    v_row.due_date, v_row.status, v_row.store_id, NULL::UUID,
                    NULL::UUID, false, v_error;
                RETURN;
            END IF;

            v_date := p_payment_date::DATE;
            v_new_paid := v_row.amount_paid + p_amount;
            v_new_balance := v_row.balance - p_amount;
            v_new_status := CASE WHEN v_new_balance <= 0 THEN 'paid' ELSE 'partial' END;

            UPDATE accounts_receivable
            SET amount_paid = v_new_paid,
                balance = v_new_balance,
                status = v_new_status
            WHERE id = p_ar_id AND tenant_id = p_tenant_id;

            INSERT INTO ar_payments (
                id, tenant_id, ar_id, amount, payment_date, notes, recorded_by,
                idempotency_key, request_fingerprint, store_id
            ) VALUES (
                v_payment_id, p_tenant_id, p_ar_id, p_amount, v_date, p_notes,
                p_recorded_by, p_idempotency_key, p_request_fingerprint, v_row.store_id
            );

            RETURN QUERY SELECT
                v_row.id, v_row.tenant_id, v_row.customer_id, v_row.customer_name,
                v_row.invoice_number, v_row.amount, v_new_paid, v_new_balance,
                v_row.due_date, v_new_status, v_row.store_id, v_payment_id,
                NULL::UUID, false, NULL::TEXT;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_record_ap_payment(
            p_tenant_id UUID,
            p_ap_id UUID,
            p_amount NUMERIC,
            p_payment_date TEXT,
            p_notes TEXT,
            p_recorded_by UUID,
            p_idempotency_key TEXT,
            p_request_fingerprint TEXT,
            p_store_id UUID
        ) RETURNS TABLE (
            out_id UUID,
            out_tenant_id UUID,
            bill_number VARCHAR,
            vendor_name VARCHAR,
            description TEXT,
            amount NUMERIC,
            amount_paid NUMERIC,
            balance NUMERIC,
            due_date TIMESTAMPTZ,
            status VARCHAR,
            out_store_id UUID,
            out_payment_id UUID,
            out_journal_id UUID,
            out_replayed BOOLEAN,
            out_error TEXT
        ) AS $$
        DECLARE
            v_row accounts_payable%ROWTYPE;
            v_existing ap_payments%ROWTYPE;
            v_payment_id UUID := gen_random_uuid();
            v_date DATE;
            v_new_paid NUMERIC;
            v_new_balance NUMERIC;
            v_new_status VARCHAR;
            v_error TEXT;
        BEGIN
            SELECT * INTO v_row
            FROM accounts_payable
            WHERE id = p_ap_id AND tenant_id = p_tenant_id
            FOR UPDATE;

            IF NOT FOUND THEN
                RETURN QUERY SELECT
                    p_ap_id, p_tenant_id, NULL::VARCHAR, NULL::VARCHAR, NULL::TEXT,
                    NULL::NUMERIC, NULL::NUMERIC, NULL::NUMERIC, NULL::TIMESTAMPTZ,
                    NULL::VARCHAR, NULL::UUID, NULL::UUID, NULL::UUID, false,
                    'Payable not found'::TEXT;
                RETURN;
            END IF;

            IF p_idempotency_key IS NULL
               OR length(btrim(p_idempotency_key)) = 0
               OR length(p_idempotency_key) > 255
               OR p_request_fingerprint IS NULL
               OR p_request_fingerprint !~ '^[0-9a-f]{64}$' THEN
                v_error := 'Idempotency-Key or request fingerprint is invalid';
            ELSIF p_store_id IS DISTINCT FROM v_row.store_id THEN
                v_error := 'Payable store changed while recording payment';
            END IF;

            IF v_error IS NOT NULL THEN
                RETURN QUERY SELECT
                    v_row.id, v_row.tenant_id, v_row.bill_number, v_row.vendor_name,
                    v_row.description, v_row.amount, v_row.amount_paid, v_row.balance,
                    v_row.due_date, v_row.status, v_row.store_id, NULL::UUID,
                    NULL::UUID, false, v_error;
                RETURN;
            END IF;

            SELECT * INTO v_existing
            FROM ap_payments p
            WHERE p.tenant_id = p_tenant_id
              AND p.idempotency_key = p_idempotency_key
              AND p.store_id IS NOT DISTINCT FROM v_row.store_id
            LIMIT 1;

            IF FOUND THEN
                IF v_existing.ap_id <> p_ap_id
                   OR v_existing.request_fingerprint IS DISTINCT FROM p_request_fingerprint THEN
                    v_error := 'Idempotency-Key has already been used with a different request';
                ELSE
                    RETURN QUERY SELECT
                        v_row.id, v_row.tenant_id, v_row.bill_number, v_row.vendor_name,
                        v_row.description, v_row.amount, v_row.amount_paid, v_row.balance,
                        v_row.due_date, v_row.status, v_existing.store_id,
                        v_existing.id, v_existing.journal_id, true, NULL::TEXT;
                    RETURN;
                END IF;

                RETURN QUERY SELECT
                    v_row.id, v_row.tenant_id, v_row.bill_number, v_row.vendor_name,
                    v_row.description, v_row.amount, v_row.amount_paid, v_row.balance,
                    v_row.due_date, v_row.status, v_existing.store_id,
                    v_existing.id, v_existing.journal_id, false, v_error;
                RETURN;
            END IF;

            IF p_amount IS NULL OR p_amount <= 0 THEN
                v_error := 'Payment amount must be greater than 0';
            ELSIF p_amount > v_row.balance THEN
                v_error := 'Payment amount exceeds the remaining balance of '
                           || v_row.balance::TEXT;
            END IF;

            IF v_error IS NOT NULL THEN
                RETURN QUERY SELECT
                    v_row.id, v_row.tenant_id, v_row.bill_number, v_row.vendor_name,
                    v_row.description, v_row.amount, v_row.amount_paid, v_row.balance,
                    v_row.due_date, v_row.status, v_row.store_id, NULL::UUID,
                    NULL::UUID, false, v_error;
                RETURN;
            END IF;

            v_date := p_payment_date::DATE;
            v_new_paid := v_row.amount_paid + p_amount;
            v_new_balance := v_row.balance - p_amount;
            v_new_status := CASE WHEN v_new_balance <= 0 THEN 'paid' ELSE 'partial' END;

            UPDATE accounts_payable
            SET amount_paid = v_new_paid,
                balance = v_new_balance,
                status = v_new_status
            WHERE id = p_ap_id AND tenant_id = p_tenant_id;

            INSERT INTO ap_payments (
                id, tenant_id, ap_id, amount, payment_date, notes, recorded_by,
                idempotency_key, request_fingerprint, store_id
            ) VALUES (
                v_payment_id, p_tenant_id, p_ap_id, p_amount, v_date, p_notes,
                p_recorded_by, p_idempotency_key, p_request_fingerprint, v_row.store_id
            );

            RETURN QUERY SELECT
                v_row.id, v_row.tenant_id, v_row.bill_number, v_row.vendor_name,
                v_row.description, v_row.amount, v_new_paid, v_new_balance,
                v_row.due_date, v_new_status, v_row.store_id, v_payment_id,
                NULL::UUID, false, NULL::TEXT;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS fn_record_ar_payment(
            UUID, UUID, NUMERIC, TEXT, TEXT, UUID, TEXT, TEXT, UUID
        )
    """)
    op.execute("""
        DROP FUNCTION IF EXISTS fn_record_ap_payment(
            UUID, UUID, NUMERIC, TEXT, TEXT, UUID, TEXT, TEXT, UUID
        )
    """)
