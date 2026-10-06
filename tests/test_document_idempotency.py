"""The invoice status handler must be safe to run twice.

An outbox event can be replayed after a retry or a broker redelivery. Before
the guards, replaying "invoice sent" posted Dr 1100 / Cr 4000 a second time
and double-counted the revenue, and replaying "invoice paid" credited 1100
again. These tests drive the handler twice and assert the ledger moved once.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.common.events.envelope import EventEnvelope


def _envelope(payload: dict) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid4(),
        event_type="document.status_changed",
        tenant_id=uuid4(),
        payload=payload,
    )


@pytest.fixture
async def session_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.common.db.session import set_rls_context
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory, set_rls_context
    finally:
        await engine.dispose()


@pytest.fixture
async def invoices(session_factory):
    """Factory for running a whole event sequence against the real database.

    Every scenario runs inside one transaction that is always rolled back, so
    a replayed event can be observed in the same transaction that created it
    without leaving test invoices behind in the shared database.
    """
    factory, set_rls_context = session_factory

    async def scenario(*payload_updates_list):
        """Run the handler once per payload dict, then report the ledger state."""
        async with factory() as session:
            transaction = await session.begin()
            try:
                tenant = (
                    await session.execute(text("SELECT id FROM tenants LIMIT 1"))
                ).scalar()
                tenant_id = str(tenant)
                await set_rls_context(session, tenant_id, tenant_id, "admin")

                document_id = uuid4()
                doc_number = f"INV-IDEM-{document_id.hex[:8].upper()}"
                await session.execute(
                    text(
                        """
                        INSERT INTO documents (
                            id, tenant_id, doc_number, doc_type, status,
                            customer_name, subtotal, discount, tax, total,
                            created_at, updated_at
                        ) VALUES (
                            :id, :tenant, :number, 'invoice', 'sent',
                            'Idempotency Co', 0, 0, 0, :total, NOW(), NOW()
                        )
                        """
                    ),
                    {"id": document_id, "tenant": tenant, "number": doc_number,
                     "total": Decimal("100000")},
                )

                from app.worker.handlers.documents import handle_document_status_changed

                for updates in payload_updates_list:
                    envelope = _envelope(
                        {
                            "tenant_id": tenant_id,
                            "document_id": str(document_id),
                            "doc_number": doc_number,
                            "doc_type": "invoice",
                            "old_status": "draft",
                            "new_status": "sent",
                            "total": "100000",
                            "customer_name": "Idempotency Co",
                            "customer_id": str(uuid4()),
                        }
                        | updates
                    )
                    await handle_document_status_changed(envelope, session)

                result = await session.execute(
                    text(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM journals
                            WHERE reference_id = :doc
                              AND reference_type = 'invoice_sent') AS sent_journals,
                          (SELECT COUNT(*) FROM journals
                            WHERE reference_id = :doc
                              AND reference_type = 'invoice_paid') AS paid_journals,
                          (SELECT COALESCE(SUM(debit), 0) FROM journal_entries e
                            JOIN chart_of_accounts a ON a.id = e.account_id
                            JOIN journals j ON j.id = e.journal_id
                            WHERE e.account_code = '1100'
                              AND j.reference_type = 'invoice_sent'
                              AND j.reference_id = :doc) AS ar_debits,
                          (SELECT COALESCE(SUM(credit), 0) FROM journal_entries e
                            JOIN chart_of_accounts a ON a.id = e.account_id
                            JOIN journals j ON j.id = e.journal_id
                            WHERE e.account_code = '4000'
                              AND j.reference_type = 'invoice_sent'
                              AND j.reference_id = :doc) AS revenue_credits,
                          (SELECT COALESCE(SUM(credit), 0) FROM journal_entries e
                            JOIN chart_of_accounts a ON a.id = e.account_id
                            JOIN journals j ON j.id = e.journal_id
                            WHERE e.account_code = '1100'
                              AND j.reference_type = 'invoice_paid'
                              AND j.reference_id = :doc) AS ar_credits
                        """
                    ),
                    {"doc": document_id},
                )
                return result.first()
            finally:
                await transaction.rollback()

    return scenario


class TestInvoiceSentIsIdempotent:
    async def test_first_delivery_books_once(self, invoices):
        row = await invoices({})
        assert row.sent_journals == 1
        assert row.ar_debits == Decimal("100000")
        assert row.revenue_credits == Decimal("100000")

    async def test_replay_does_not_book_twice(self, invoices):
        row = await invoices({}, {}, {})
        assert row.sent_journals == 1, "replayed event created a second journal"
        assert row.ar_debits == Decimal("100000"), "receivable was debited twice"
        assert row.revenue_credits == Decimal("100000"), "revenue was credited twice"


class TestInvoicePaidIsIdempotent:
    async def test_paid_settles_the_receivable_once(self, invoices):
        paid = {"new_status": "paid", "old_status": "sent"}
        row = await invoices({}, paid)

        assert row.paid_journals == 1
        assert row.ar_credits == Decimal("100000")

    async def test_replayed_paid_event_credits_ar_once(self, invoices):
        paid = {"new_status": "paid", "old_status": "sent"}
        row = await invoices({}, paid, paid, paid)

        assert row.paid_journals == 1, "replayed paid event created a second journal"
        assert row.ar_credits == Decimal("100000"), "receivable was credited twice"

    async def test_part_paid_invoice_settles_only_what_is_outstanding(self, invoices):
        """A part payment already taken through the payments screen must not
        make the paid event raise: update_ar_payment would reject the full
        total and the event would be retried forever."""
        paid = {"new_status": "paid", "old_status": "sent", "total": "60000"}
        row = await invoices({}, paid)

        assert row.paid_journals == 1
        assert row.ar_credits == Decimal("60000")
