"""Two revenue paths earned the tenant money and nothing was booked for either.

A converted document leaves its sale pending forever — nothing pays it — and the
platform fee was only ever charged when a sale reached "completed", so the
commission on that revenue was never taken. A standalone receipt is not a sale at
all and escaped it likewise.

The same gap showed up in the tenant's own books: an unpaid sale produced no
journal, so revenue and the receivable were both absent until payment. Sales are
now booked when they are created, debiting receivables, and the payment settles
them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.accounting.service import plan_sale_journal

TENANT = uuid4()
SALE = uuid4()


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


class _Result:
    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._row


def _rows(session, mapping):
    """Answer every execute() with the same single row."""
    async def execute(*args, **kwargs):
        return _Result()
    session.execute = AsyncMock(side_effect=execute)
    _Result._row = mapping


class TestFeeOnBothPaths:
    async def test_a_converted_sale_is_charged(self):
        """A converted sale is never paid, so nothing would ever complete it and
        the commission would never be taken."""
        from app.common import bridge

        session = _session()
        doc = SimpleNamespace(
            id=uuid4(),
            tenant_id=str(TENANT),
            doc_type="quote",
            status="sent",
            discount=0,
            customer_name=None,
            customer_phone=None,
            store_id=uuid4(),
            linked_sale_id=None,
            total=100.0,
            doc_number="QT-1",
        )

        with (
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.documents.repository.get_document_by_id",
                AsyncMock(return_value=doc),
            ),
            patch(
                "app.documents.repository.get_document_items",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.documents.repository.update_document_status", AsyncMock()
            ),
            patch.object(
                bridge,
                "create_sale_via_service",
                AsyncMock(
                    return_value={
                        "id": str(SALE),
                        "sale_number": "SL-1",
                        "total": 100.0,
                    }
                ),
            ),
            patch(
                "app.platform.fee_calculator.record_platform_fee",
                AsyncMock(return_value={"platform_fee": 5.5}),
            ) as charge,
        ):
            sdb.return_value.session.return_value = session
            await bridge.convert_document_to_sale(
                tenant_id=str(TENANT),
                document_id=str(doc.id),
                cashier_id=str(uuid4()),
            )

        charge.assert_awaited_once()
        assert charge.await_args.kwargs["sale_id"] == SALE
        assert charge.await_args.kwargs["total"] == 100.0

    async def test_a_fee_needs_a_sale_or_a_document(self):
        from app.platform.fee_calculator import record_platform_fee

        session = _session()
        with pytest.raises(ValueError, match="sale or a document"):
            await record_platform_fee(session, tenant_id=TENANT, total=100)

    async def test_a_receipt_is_charged(self):
        """A standalone receipt is revenue the tenant collected."""
        from app.common import bridge

        session = _session()
        doc = SimpleNamespace(
            id=uuid4(),
            tenant_id=TENANT,
            doc_type="receipt",
            status="issued",
            linked_sale_id=None,
            total=500.0,
            doc_number="RCP-1",
        )

        with (
            patch(
                "app.documents.repository.create_document",
                AsyncMock(),
            ),
            patch(
                "app.documents.repository.create_document_items",
                AsyncMock(),
            ),
            patch(
                "app.documents.service.plan_document_creation",
                return_value=(
                    SimpleNamespace(model_dump=lambda mode: {"id": str(doc.id)}),
                    doc,
                    [],
                    [],
                ),
            ),
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.platform.fee_calculator.record_platform_fee",
                AsyncMock(return_value={"platform_fee": 27.5}),
            ) as charge,
        ):
            sdb.return_value.session.return_value = session
            await bridge.create_document(
                tenant_id=str(TENANT),
                actor_id=str(uuid4()),
                doc_type="receipt",
                items=[{"description": "x", "qty": 1, "unit_price": 500}],
            )

        charge.assert_awaited_once()
        assert charge.await_args.kwargs["document_id"] == doc.id

    async def test_a_receipt_for_an_existing_sale_is_not_charged_again(self):
        """It is a receipt *for* a sale that is charged when the sale completes;
        charging again would bill the tenant twice for the same money."""
        from app.common import bridge

        session = _session()
        doc = SimpleNamespace(
            id=uuid4(),
            tenant_id=TENANT,
            doc_type="receipt",
            status="issued",
            linked_sale_id=uuid4(),
            total=500.0,
            doc_number="RCP-2",
        )

        with (
            patch(
                "app.documents.repository.create_document", AsyncMock()
            ),
            patch(
                "app.documents.repository.create_document_items", AsyncMock()
            ),
            patch(
                "app.documents.service.plan_document_creation",
                return_value=(
                    SimpleNamespace(model_dump=lambda mode: {"id": str(doc.id)}),
                    doc,
                    [],
                    [],
                ),
            ),
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.platform.fee_calculator.record_platform_fee",
                AsyncMock(return_value={"platform_fee": 27.5}),
            ) as charge,
        ):
            sdb.return_value.session.return_value = session
            await bridge.create_document(
                tenant_id=str(TENANT),
                actor_id=str(uuid4()),
                doc_type="receipt",
                items=[{"description": "x", "qty": 1, "unit_price": 500}],
                linked_sale_id=str(doc.linked_sale_id),
            )

        charge.assert_not_awaited()

    async def test_a_second_fee_for_the_same_sale_is_refused(self):
        """A converted sale charged on conversion must not be charged again when
        it is later paid in full."""
        from app.platform.fee_calculator import record_platform_fee

        session = _session()
        _rows(session, uuid4())  # an existing ledger row is found

        result = await record_platform_fee(
            session, tenant_id=TENANT, total=100.0, sale_id=SALE
        )

        assert result is None
        session.add.assert_not_called()


class TestUnpaidSalesAreBooked:
    def test_an_unpaid_sale_debits_the_receivable_not_cash(self):
        journal, entries = plan_sale_journal(
            tenant_id=TENANT,
            sale_id=SALE,
            sale_number="SL-2",
            sale_items=[{"product_name": "x", "qty": 1, "unit_price": 100}],
            total=100.0,
            discount=0.0,
            settled=False,
        )
        codes = {e.account_code: e for e in entries}
        assert "1100" in codes, "an unpaid sale must debit receivables"
        assert codes["1100"].debit == 100.0
        # No money has moved, so cash must not be touched.
        assert "1000" not in codes
        assert "1010" not in codes

    def test_a_paid_sale_still_debits_cash(self):
        _journal, entries = plan_sale_journal(
            tenant_id=TENANT,
            sale_id=SALE,
            sale_number="SL-3",
            sale_items=[{"product_name": "x", "qty": 1, "unit_price": 100}],
            total=100.0,
            discount=0.0,
            settled=True,
        )
        codes = {e.account_code for e in entries}
        assert "1000" in codes
        assert "1100" not in codes

    @pytest.mark.parametrize("total,tax", [(100.0, 0.0), (120.0, 20.0)])
    def test_the_entry_still_balances(self, total, tax):
        _journal, entries = plan_sale_journal(
            tenant_id=TENANT,
            sale_id=SALE,
            sale_number="SL-4",
            sale_items=[{"product_name": "x", "qty": 1, "unit_price": total}],
            total=total,
            discount=0.0,
            tax_amount=tax,
            settled=False,
        )
        assert sum(e.debit for e in entries) == pytest.approx(
            sum(e.credit for e in entries)
        )
        assert sum(e.debit for e in entries) == pytest.approx(total)