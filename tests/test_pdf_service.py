"""Tests for document PDF generation.

The PDF bytes are the contract with the client: they must start with the
PDF magic, carry the tenant profile, and survive every document type.
"""

import pytest

from app.common.pdf_service import (
    generate_invoice_pdf,
    generate_quote_pdf,
    generate_receipt_pdf,
)


def _doc(doc_type: str = "invoice") -> dict:
    return {
        "doc_type": doc_type,
        "doc_number": "INV-20261006-ABCD1234",
        "customer_name": "Test Customer",
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
    }


def _business(**overrides) -> dict:
    business = {
        "name": "Bright Spark Stores Ltd",
        "address": "12 Allen Avenue, Ikeja, Lagos",
        "phone": "+234 801 234 5678",
        "logo_bytes": None,
    }
    business.update(overrides)
    return business


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
