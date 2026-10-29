"""PDF generation service using fpdf2.

Generates invoices, receipts, and quotes as PDF bytes.
"""

from io import BytesIO

from fpdf import FPDF


class StoreFlowPDF(FPDF):
    """Custom PDF class with tenant branding."""

    def __init__(
        self,
        business_name: str = "StoreFlow",
        logo_bytes: bytes | None = None,
        address: str = "",
        phone: str = "",
    ):
        super().__init__()
        self.business_name = business_name
        self.logo_bytes = logo_bytes
        self.address = address
        self.phone = phone

    @staticmethod
    def _naira(amount: float) -> str:
        return f"NGN {amount:,.2f}"

    def header(self):
        contact_bits = [bit for bit in (self.address.strip(), self.phone.strip()) if bit]
        contact_line = "  |  ".join(contact_bits)
        logo_bottom = 0.0
        if self.logo_bytes:
            try:
                from PIL import Image

                with Image.open(BytesIO(self.logo_bytes)) as image:
                    ratio = image.height / image.width
                self.image(BytesIO(self.logo_bytes), x=self.l_margin, y=8, w=20)
                logo_bottom = 8 + 20 * ratio + 4
            except Exception:
                self.logo_bytes = None

        name_font = 16 if self.logo_bytes else 18
        if self.logo_bytes:
            self.set_xy(self.l_margin + 24, 8)
        else:
            self.set_x(self.l_margin)
        self.set_font("Helvetica", "B", name_font)
        self.cell(0, 9, self.business_name)
        self.ln(9)
        if contact_line:
            self.set_x(self.l_margin + (24 if self.logo_bytes else 0))
            self.set_font("Helvetica", "", 9)
            self.set_text_color(110, 110, 110)
            self.multi_cell(0, 4.5, contact_line)
            self.set_text_color(0, 0, 0)
        self.set_y(max(self.get_y(), logo_bottom))
        self.ln(3)

    def footer(self):
        self.set_y(-18)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 5, "This is a digitally generated document and is valid without a signature.", align="C")
        self.set_y(-12)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="C")

    def document_title(self, title: str, doc_number: str):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, f"{title}: {doc_number}")
        self.ln(10)
        self.ln(2)

    def info_row(self, label: str, value: str):
        self.set_font("Helvetica", "B", 10)
        self.cell(40, 6, label)
        self.set_font("Helvetica", "", 10)
        self.cell(0, 6, value)
        self.ln(6)

    def items_table(self, items: list[dict]):
        self.ln(3)
        self.set_font("Helvetica", "B", 10)
        col_widths = [10, 70, 20, 30, 30, 30]
        headers = ["#", "Description", "Qty", "Unit Price", "Discount", "Total"]
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, h, border=1, align="C")
        self.ln()

        self.set_font("Helvetica", "", 9)
        for idx, item in enumerate(items, 1):
            qty = float(item.get("qty", 0))
            unit_price = float(item.get("unit_price", 0))
            discount_pct = float(item.get("discount_pct", 0))
            line_total = qty * unit_price * (1 - discount_pct / 100)

            self.cell(col_widths[0], 6, str(idx), border=1, align="C")
            self.cell(col_widths[1], 6, str(item.get("description", ""))[:35], border=1)
            self.cell(col_widths[2], 6, f"{qty:.1f}", border=1, align="C")
            self.cell(col_widths[3], 6, f"NGN{unit_price:,.2f}", border=1, align="R")
            self.cell(col_widths[4], 6, f"{discount_pct:.0f}%", border=1, align="C")
            self.cell(col_widths[5], 6, f"NGN{line_total:,.2f}", border=1, align="R")
            self.ln()

    def totals_section(self, subtotal: float, tax: float = 0, total: float = 0, tax_breakdown: list[dict] | None = None):
        self.ln(3)
        self.set_font("Helvetica", "B", 10)
        x_start = self.w - self.r_margin - 75
        self.set_x(x_start)
        self.cell(35, 7, "Subtotal:")
        self.cell(40, 7, f"NGN{subtotal:,.2f}", align="R")
        self.ln(7)

        if tax_breakdown:
            for t in tax_breakdown:
                self.set_x(x_start)
                self.cell(35, 7, f"{t['name']} ({t['rate']}%):")
                self.cell(40, 7, f"NGN{t['amount']:,.2f}", align="R")
                self.ln(7)
        elif tax > 0:
            self.set_x(x_start)
            self.cell(35, 7, "Tax:")
            self.cell(40, 7, f"NGN{tax:,.2f}", align="R")
            self.ln(7)

        self.set_font("Helvetica", "B", 12)
        self.set_x(x_start)
        self.cell(35, 8, "Total:")
        self.cell(40, 8, f"NGN{total:,.2f}", align="R")
        self.ln(8)

    def notes_section(self, note: str = "", terms: str = ""):
        if note:
            self.ln(5)
            self.set_font("Helvetica", "B", 10)
            self.cell(0, 6, "Note:")
            self.ln(6)
            self.set_font("Helvetica", "", 9)
            self.multi_cell(0, 5, note)

        if terms:
            self.ln(3)
            self.set_font("Helvetica", "B", 10)
            self.cell(0, 6, "Terms:")
            self.ln(6)
            self.set_font("Helvetica", "", 9)
            self.multi_cell(0, 5, terms)


def generate_invoice_pdf(
    doc_number: str,
    customer_name: str,
    items: list[dict],
    subtotal: float,
    tax: float = 0,
    total: float = 0,
    due_date: str = "",
    notes: str = "",
    terms: str = "",
    business_name: str = "StoreFlow",
    logo_bytes: bytes | None = None,
    address: str = "",
    phone: str = "",
    title: str = "Invoice",
    tax_breakdown: list[dict] | None = None,
) -> bytes:
    pdf = StoreFlowPDF(business_name, logo_bytes=logo_bytes, address=address, phone=phone)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title(title, doc_number)
    pdf.info_row("Customer:", customer_name)
    if due_date:
        pdf.info_row("Due Date:", due_date)
    pdf.ln(3)

    pdf.items_table(items)
    pdf.totals_section(subtotal, tax, total, tax_breakdown=tax_breakdown)
    pdf.notes_section(note=notes, terms=terms)

    return pdf.output()


def generate_receipt_pdf(
    sale_number: str,
    customer_name: str,
    items: list[dict],
    subtotal: float,
    payment_method: str = "Cash",
    total: float = 0,
    notes: str = "",
    business_name: str = "StoreFlow",
    logo_bytes: bytes | None = None,
    address: str = "",
    phone: str = "",
    tax: float = 0,
    tax_breakdown: list[dict] | None = None,
) -> bytes:
    pdf = StoreFlowPDF(business_name, logo_bytes=logo_bytes, address=address, phone=phone)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title("Receipt", sale_number)
    pdf.info_row("Customer:", customer_name)
    pdf.info_row("Payment:", payment_method)
    pdf.ln(3)

    pdf.items_table(items)
    pdf.totals_section(subtotal, tax, total, tax_breakdown=tax_breakdown)
    pdf.notes_section(note=notes)

    return pdf.output()


def generate_quote_pdf(
    doc_number: str,
    customer_name: str,
    items: list[dict],
    subtotal: float,
    tax: float = 0,
    total: float = 0,
    notes: str = "",
    terms: str = "",
    business_name: str = "StoreFlow",
    logo_bytes: bytes | None = None,
    address: str = "",
    phone: str = "",
    tax_breakdown: list[dict] | None = None,
) -> bytes:
    pdf = StoreFlowPDF(business_name, logo_bytes=logo_bytes, address=address, phone=phone)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title("Quote", doc_number)
    pdf.info_row("Customer:", customer_name)
    pdf.ln(3)

    pdf.items_table(items)
    pdf.totals_section(subtotal, tax, total, tax_breakdown=tax_breakdown)
    pdf.notes_section(note=notes, terms=terms)

    return pdf.output()
