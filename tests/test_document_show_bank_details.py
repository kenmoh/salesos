"""A document decides whether its PDF prints settlement bank details.

The business stores the bank details; each document says whether they belong
on its own PDF, so an invoice can carry them while a receipt stays bare.
"""

from decimal import Decimal
from uuid import uuid4

from app.documents.schemas import DocumentCreateCommand, DocumentItemLine
from app.documents.service import plan_document_creation


def _command(**overrides) -> DocumentCreateCommand:
    payload = dict(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        doc_type="invoice",
        items=[
            DocumentItemLine(
                description="Widget", qty=Decimal("1"), unit_price=Decimal("1000")
            )
        ],
    )
    payload.update(overrides)
    return DocumentCreateCommand(**payload)


class TestShowBankDetails:
    def test_bank_details_are_hidden_by_default(self):
        result, doc, _items, _outbox = plan_document_creation(_command())

        assert doc.show_bank_details is False
        assert result.show_bank_details is False

    def test_a_document_can_ask_for_its_bank_details(self):
        result, doc, _items, _outbox = plan_document_creation(
            _command(show_bank_details=True)
        )

        assert doc.show_bank_details is True
        assert result.show_bank_details is True
