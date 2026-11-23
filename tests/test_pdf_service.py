"""Tests for document PDF generation.

The PDF bytes are the contract with the client: they must start with the
PDF magic, carry the tenant profile, and survive every document type.
"""

import pytest

from app.common.pdf_service import (
    StoreFlowPDF,
    generate_invoice_pdf,
    generate_quote_pdf,
    generate_receipt_pdf,
)


def _doc(doc_type: str = "invoice", **overrides) -> dict:
    doc = {
        "doc_type": doc_type,
        "doc_number": "INV-20261006-ABCD1234",
        "customer_name": "Test Customer",
        "customer_email": "buyer@example.com",
        "customer_phone": "+234 802 000 1111",
        "customer_address": "5B Bourdillon Road, Ikoyi, Lagos",
        "items": [
            {
                "description": "Samsung Galaxy A14",
                "qty": 2,
                "unit_price": "85000.00",
                "discount_pct": "5.00",
                "tax_rate": "7.50",
            }
        ],
        "subtotal": 161500.0,
        "tax": "12112.50",
        "total": "173612.50",
        "due_date": "2026-11-06T00:00:00+00:00",
        "notes": "Deliver within two weeks.",
        "terms": "Payment within 30 days.",
        "tax_breakdown": [{"name": "VAT", "rate": 7.5, "amount": 12112.5}],
        "show_bank_details": True,
    }
    doc.update(overrides)
    return doc


def _business(**overrides) -> dict:
    business = {
        "name": "Bright Spark Stores Ltd",
        "business_description": "Modern retail chain in Lagos.",
        "business_email": "accounts@brightspark.ng",
        "address": "12 Allen Avenue, Ikeja, Lagos",
        "phone": "+234 801 234 5678",
        "logo_bytes": None,
        "account_number": "0123456789",
        "account_name": "Bright Spark Stores Ltd",
        "bank_name": "Wema Bank",
    }
    business.update(overrides)
    return business


def _rendered_text(pdf: StoreFlowPDF) -> list[str]:
    """Record every string the PDF draws, so tests can assert on content."""
    texts: list[str] = []
    original_cell = pdf.cell
    original_multi_cell = pdf.multi_cell

    def cell(*args, **kwargs):
        if len(args) >= 3:
            texts.append(str(args[2]))
        return original_cell(*args, **kwargs)

    def multi_cell(*args, **kwargs):
        if len(args) >= 3:
            texts.append(str(args[2]))
        return original_multi_cell(*args, **kwargs)

    pdf.cell = cell
    pdf.multi_cell = multi_cell
    return texts


@pytest.mark.parametrize("doc_type", ["invoice", "quote", "receipt", "purchase_order"])
def test_build_document_pdf_returns_pdf_bytes(doc_type):
    from app.documents.routes import _build_document_pdf

    doc_type_out, doc_number, pdf_bytes = _build_document_pdf(_doc(doc_type), _business())

    assert doc_type_out == doc_type
    assert doc_number == "INV-20261006-ABCD1234"
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500


def test_build_document_pdf_without_business_profile():
    from app.documents.routes import _build_document_pdf

    _, _, pdf_bytes = _build_document_pdf(_doc("invoice"), None)

    assert pdf_bytes.startswith(b"%PDF")


def test_build_document_pdf_with_logo():
    from io import BytesIO

    from app.documents.routes import _build_document_pdf

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (40, 40), (10, 80, 200)).save(buffer, format="PNG")

    _, _, pdf_bytes = _build_document_pdf(
        _doc("invoice"), _business(logo_bytes=buffer.getvalue())
    )

    assert pdf_bytes.startswith(b"%PDF")


def test_generate_receipt_and_quote_are_pdfs():
    receipt = generate_receipt_pdf(
        sale_number="SAL-1",
        customer_name="Buyer",
        items=[{"description": "Item", "qty": 1, "unit_price": 1000.0, "discount_pct": 0}],
        subtotal=1000.0,
        total=1000.0,
        business_name="Bright Spark Stores Ltd",
    )
    quote = generate_quote_pdf(
        doc_number="QUO-1",
        customer_name="Buyer",
        items=[{"description": "Item", "qty": 1, "unit_price": 1000.0, "discount_pct": 0}],
        subtotal=1000.0,
        total=1000.0,
        business_name="Bright Spark Stores Ltd",
    )

    assert receipt.startswith(b"%PDF")
    assert quote.startswith(b"%PDF")


def test_format_due_date_renders_date_only():
    from app.documents.routes import _format_due_date

    assert _format_due_date("2026-11-06T00:00:00+00:00") == "06 Nov 2026"
    assert _format_due_date("2026-11-06") == "06 Nov 2026"
    assert _format_due_date(None) == ""


def test_the_pdf_services_stay_inside_what_the_core_fonts_can_encode():
    """The Naira sign crashes fpdf2 rather than printing.

    fpdf2's built-in fonts are latin-1 only, so a Naira sign (U+20A6) raises
    FPDFUnicodeEncodingException instead of rendering. Amounts are written as
    NGN for that reason; a sign sneaked back in would 500 the download.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "\u20a6" in text and "pdf" in path.name:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


class TestHeaderLayout:
    def test_company_contacts_are_one_per_line(self):
        pdf = StoreFlowPDF(
            business_name="Bright Spark Stores Ltd",
            address="12 Allen Avenue, Ikeja, Lagos",
            phone="+234 801 234 5678",
            business_email="accounts@brightspark.ng",
        )

        assert pdf.header_contact_lines() == [
            "12 Allen Avenue, Ikeja, Lagos",
            "+234 801 234 5678",
            "accounts@brightspark.ng",
        ]

    def test_missing_company_contacts_are_left_out(self):
        pdf = StoreFlowPDF(business_name="Acme", address="", phone="  ")

        assert pdf.header_contact_lines() == []

    def test_bank_details_are_printed_when_the_document_asks_for_them(self):
        pdf = StoreFlowPDF(
            business_name="Acme",
            account_number="0123456789",
            account_name="Acme Ventures Ltd",
            bank_name="Wema Bank",
            show_bank_details=True,
        )
        texts = _rendered_text(pdf)
        pdf.add_page()

        assert pdf.bank_detail_rows() == [
            ("Account Number:", "0123456789"),
            ("Account Name:", "Acme Ventures Ltd"),
            ("Bank Name:", "Wema Bank"),
        ]
        assert any("Account Number:" in text for text in texts)
        assert any("0123456789" in text for text in texts)
        assert any("Bank Name:" in text for text in texts)

    def test_bank_details_stay_hidden_when_the_document_says_no(self):
        pdf = StoreFlowPDF(
            business_name="Acme",
            account_number="0123456789",
            account_name="Acme Ventures Ltd",
            bank_name="Wema Bank",
            show_bank_details=False,
        )
        texts = _rendered_text(pdf)
        pdf.add_page()

        assert pdf.bank_detail_rows() == []
        assert not any("Account Number:" in text for text in texts)
        assert not any("Bank Name:" in text for text in texts)

    def test_bank_details_stay_hidden_when_the_business_has_none(self):
        pdf = StoreFlowPDF(business_name="Acme", show_bank_details=True)
        texts = _rendered_text(pdf)
        pdf.add_page()

        assert pdf.bank_detail_rows() == []
        assert not any("Account Number:" in text for text in texts)

    def test_business_description_is_printed_under_the_business_name(self):
        pdf = StoreFlowPDF(
            business_name="Acme",
            business_description="Modern retail chain in Lagos.",
            address="12 Allen Avenue, Ikeja, Lagos",
            phone="+234 801 234 5678",
            business_email="accounts@brightspark.ng",
        )
        texts = _rendered_text(pdf)
        pdf.add_page()

        assert any("Modern retail chain in Lagos." in text for text in texts)


class TestCustomerBlock:
    def test_customer_contact_details_are_printed_when_available(self):
        pdf = StoreFlowPDF(business_name="Acme")
        texts = _rendered_text(pdf)
        pdf.add_page()
        pdf.customer_rows(
            "Ada Lovelace",
            customer_phone="+234 802 000 1111",
            customer_email="ada@example.com",
            customer_address="5B Bourdillon Road, Ikoyi, Lagos",
        )

        assert any("Ada Lovelace" in text for text in texts)
        assert any("+234 802 000 1111" in text for text in texts)
        assert any("ada@example.com" in text for text in texts)
        assert any("5B Bourdillon Road, Ikoyi, Lagos" in text for text in texts)

    def test_customer_contact_details_are_skipped_when_missing(self):
        pdf = StoreFlowPDF(business_name="Acme")
        texts = _rendered_text(pdf)
        pdf.add_page()
        pdf.customer_rows("Walk-in Customer")

        assert any("Walk-in Customer" in text for text in texts)
        assert not any("Phone:" in text for text in texts)
        assert not any("Email:" in text for text in texts)
        assert not any("Address:" in text for text in texts)


class TestBankFlagReachesThePdf:
    def test_the_documents_flag_is_passed_to_the_generator(self, monkeypatch):
        from app.common import pdf_service
        from app.documents.routes import _build_document_pdf

        captured: dict = {}

        def fake_invoice(**kwargs):
            captured.update(kwargs)
            return b"%PDF-1.4 fake"

        monkeypatch.setattr(pdf_service, "generate_invoice_pdf", fake_invoice)
        _build_document_pdf(_doc("invoice", show_bank_details=True), _business())

        assert captured["show_bank_details"] is True
        assert captured["account_number"] == "0123456789"
        assert captured["account_name"] == "Bright Spark Stores Ltd"
        assert captured["bank_name"] == "Wema Bank"

    def test_a_document_that_did_not_ask_for_bank_details_keeps_them_out(self, monkeypatch):
        from app.common import pdf_service
        from app.documents.routes import _build_document_pdf

        captured: dict = {}

        def fake_invoice(**kwargs):
            captured.update(kwargs)
            return b"%PDF-1.4 fake"

        monkeypatch.setattr(pdf_service, "generate_invoice_pdf", fake_invoice)
        _build_document_pdf(_doc("invoice", show_bank_details=False), _business())

        assert captured["show_bank_details"] is False

    def test_customer_contact_details_reach_the_generator(self, monkeypatch):
        from app.common import pdf_service
        from app.documents.routes import _build_document_pdf

        captured: dict = {}

        def fake_invoice(**kwargs):
            captured.update(kwargs)
            return b"%PDF-1.4 fake"

        monkeypatch.setattr(pdf_service, "generate_invoice_pdf", fake_invoice)
        _build_document_pdf(_doc("invoice"), _business())

        assert captured["customer_phone"] == "+234 802 000 1111"
        assert captured["customer_email"] == "buyer@example.com"
        assert captured["customer_address"] == "5B Bourdillon Road, Ikoyi, Lagos"
