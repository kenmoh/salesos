"""PDF generation service using fpdf2.

Generates invoices, receipts, and quotes as PDF bytes.
"""

from io import BytesIO

from fpdf import FPDF

from app.common.currency import DEFAULT_CURRENCY, format_money_ascii


class StoreFlowPDF(FPDF):
    """Custom PDF class with tenant branding."""

    def __init__(
        self,
        business_name: str = "StoreFlow",
        logo_bytes: bytes | None = None,
        address: str = "",
        phone: str = "",
        currency: str | None = None,
        business_description: str = "",
        business_email: str = "",
        account_number: str = "",
        account_name: str = "",
        bank_name: str = "",
        show_bank_details: bool = False,
    ):
        super().__init__()
        self.business_name = business_name
        self.logo_bytes = logo_bytes
        self.address = address
        self.phone = phone
        self.currency = currency or DEFAULT_CURRENCY
        self.business_description = business_description
        self.business_email = business_email
        self.account_number = account_number
        self.account_name = account_name
        self.bank_name = bank_name
        self.show_bank_details = show_bank_details

    def money(self, amount: float) -> str:
        # Helvetica is latin-1 only; use the currency code rather than a symbol.
        return format_money_ascii(amount, self.currency, decimals=2)

    def header_contact_lines(self) -> list[str]:
        """Company contact lines, each on its own row under the business name."""
        lines = []
        for value in (self.address, self.phone, self.business_email):
            text = (value or "").strip()
            if text:
                lines.append(text)
        return lines

    def bank_detail_rows(self) -> list[tuple[str, str]]:
        """Settlement bank rows to print at the far right of the header.

        The document decides whether they are shown at all, and a business with
        no bank details stored gets no block rather than a set of empty labels.
        """
        if not self.show_bank_details:
            return []
        candidates = (
            ("Account Number:", self.account_number),
            ("Account Name:", self.account_name),
            ("Bank Name:", self.bank_name),
        )
        return [
            (label, text)
            for label, value in candidates
            if (text := (value or "").strip())
        ]

    def _bank_block(self, x: float, width: float, top: float) -> None:
        label_width = 30.0
        value_width = width - label_width
        for label, value in self.bank_detail_rows():
            self.set_xy(x, top)
            self.set_font("Helvetica", "B", 8.5)
            self.set_text_color(90, 90, 90)
            self.cell(label_width, 4.5, label, align="R")
            self.set_font("Helvetica", "", 9)
            self.set_text_color(0, 0, 0)
            self.multi_cell(value_width, 4.5, value, align="R")
            top = self.get_y()

    def header(self):
        top = 8.0
        right_width = 70.0
        has_bank = bool(self.bank_detail_rows())

        logo_bottom = top
        if self.logo_bytes:
            try:
                from PIL import Image

                with Image.open(BytesIO(self.logo_bytes)) as image:
                    ratio = image.height / image.width
                self.image(BytesIO(self.logo_bytes), x=self.l_margin, y=top, w=20)
                logo_bottom = top + 20 * ratio + 3
            except Exception:
                self.logo_bytes = None

        left_x = self.l_margin + (24.0 if self.logo_bytes else 0.0)
        right_x = self.w - self.r_margin - right_width
        if has_bank:
            left_width = right_x - 6.0 - left_x
        else:
            left_width = self.w - self.r_margin - left_x

        self.set_xy(left_x, top)
        self.set_font("Helvetica", "B", 16 if self.logo_bytes else 18)
        self.multi_cell(left_width, 9, self.business_name)

        description = (self.business_description or "").strip()
        if description:
            self.set_x(left_x)
            self.set_font("Helvetica", "", 9)
            self.set_text_color(90, 90, 90)
            self.multi_cell(left_width, 4.5, description)
            self.set_text_color(0, 0, 0)

        for line in self.header_contact_lines():
            self.set_x(left_x)
            self.set_font("Helvetica", "", 9)
            self.set_text_color(90, 90, 90)
            self.multi_cell(left_width, 4.5, line)
            self.set_text_color(0, 0, 0)

        left_bottom = max(self.get_y(), logo_bottom)

        if has_bank:
            self._bank_block(right_x, right_width, top)

        bottom = max(left_bottom, self.get_y())
        self.set_y(bottom + 1.5)
        self.set_draw_color(215, 215, 215)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_draw_color(0, 0, 0)
        self.ln(4)

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
        self.multi_cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    def customer_rows(
        self,
        customer_name: str,
        customer_phone: str = "",
        customer_email: str = "",
        customer_address: str = "",
    ):
        """Customer block: name first, then whichever contact details exist."""
        self.info_row("Customer:", customer_name)
        for label, value in (
            ("Phone:", customer_phone),
            ("Email:", customer_email),
            ("Address:", customer_address),
        ):
            text = (value or "").strip()
            if text:
                self.info_row(label, text)

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
            unit_price = float(item.get("unit_price", 0)) + float(item.get("extra", 0))
            discount_pct = float(item.get("discount_pct", 0))
            line_total = qty * unit_price * (1 - discount_pct / 100)

            self.cell(col_widths[0], 6, str(idx), border=1, align="C")
            self.cell(col_widths[1], 6, str(item.get("description", ""))[:35], border=1)
            self.cell(col_widths[2], 6, f"{qty:.1f}", border=1, align="C")
            self.cell(col_widths[3], 6, self.money(unit_price), border=1, align="R")
            self.cell(col_widths[4], 6, f"{discount_pct:.0f}%", border=1, align="C")
            self.cell(col_widths[5], 6, self.money(line_total), border=1, align="R")
            self.ln()

    def totals_section(self, subtotal: float, tax: float = 0, total: float = 0, tax_breakdown: list[dict] | None = None):
        self.ln(3)
        self.set_font("Helvetica", "B", 10)
        x_start = self.w - self.r_margin - 75
        self.set_x(x_start)
        self.cell(35, 7, "Subtotal:")
        self.cell(40, 7, self.money(subtotal), align="R")
        self.ln(7)

        if tax_breakdown:
            for t in tax_breakdown:
                self.set_x(x_start)
                self.cell(35, 7, f"{t['name']} ({t['rate']}%):")
                self.cell(40, 7, self.money(t["amount"]), align="R")
                self.ln(7)
        elif tax > 0:
            self.set_x(x_start)
            self.cell(35, 7, "Tax:")
            self.cell(40, 7, self.money(tax), align="R")
            self.ln(7)

        self.set_font("Helvetica", "B", 12)
        self.set_x(x_start)
        self.cell(35, 8, "Total:")
        self.cell(40, 8, self.money(total), align="R")
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
    currency: str | None = None,
    business_description: str = "",
    business_email: str = "",
    account_number: str = "",
    account_name: str = "",
    bank_name: str = "",
    show_bank_details: bool = False,
    customer_phone: str = "",
    customer_email: str = "",
    customer_address: str = "",
) -> bytes:
    pdf = StoreFlowPDF(
        business_name,
        logo_bytes=logo_bytes,
        address=address,
        phone=phone,
        currency=currency,
        business_description=business_description,
        business_email=business_email,
        account_number=account_number,
        account_name=account_name,
        bank_name=bank_name,
        show_bank_details=show_bank_details,
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title(title, doc_number)
    pdf.customer_rows(
        customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email,
        customer_address=customer_address,
    )
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
    currency: str | None = None,
    business_description: str = "",
    business_email: str = "",
    account_number: str = "",
    account_name: str = "",
    bank_name: str = "",
    show_bank_details: bool = False,
    customer_phone: str = "",
    customer_email: str = "",
    customer_address: str = "",
) -> bytes:
    pdf = StoreFlowPDF(
        business_name,
        logo_bytes=logo_bytes,
        address=address,
        phone=phone,
        currency=currency,
        business_description=business_description,
        business_email=business_email,
        account_number=account_number,
        account_name=account_name,
        bank_name=bank_name,
        show_bank_details=show_bank_details,
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title("Receipt", sale_number)
    pdf.customer_rows(
        customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email,
        customer_address=customer_address,
    )
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
    currency: str | None = None,
    business_description: str = "",
    business_email: str = "",
    account_number: str = "",
    account_name: str = "",
    bank_name: str = "",
    show_bank_details: bool = False,
    customer_phone: str = "",
    customer_email: str = "",
    customer_address: str = "",
) -> bytes:
    pdf = StoreFlowPDF(
        business_name,
        logo_bytes=logo_bytes,
        address=address,
        phone=phone,
        currency=currency,
        business_description=business_description,
        business_email=business_email,
        account_number=account_number,
        account_name=account_name,
        bank_name=bank_name,
        show_bank_details=show_bank_details,
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title("Quote", doc_number)
    pdf.customer_rows(
        customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email,
        customer_address=customer_address,
    )
    pdf.ln(3)

    pdf.items_table(items)
    pdf.totals_section(subtotal, tax, total, tax_breakdown=tax_breakdown)
    pdf.notes_section(note=notes, terms=terms)

    return pdf.output()
