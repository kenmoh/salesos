"""PDF generation service using fpdf2.

Generates invoices, receipts, and quotes as PDF bytes.
"""

from fpdf import FPDF


class StoreFlowPDF(FPDF):
    """Custom PDF class with StoreFlow branding."""

    def __init__(self, business_name: str = "StoreFlow"):
        super().__init__()
        self.business_name = business_name

    def header(self):
        self.set_font("Helvetica", "B", 18)
        self.cell(0, 10, self.business_name, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(128, 128, 128)
        self.cell(0, 5, "Powered by StoreFlow", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def document_title(self, title: str, doc_number: str):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, f"{title}: {doc_number}", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def info_row(self, label: str, value: str):
        self.set_font("Helvetica", "B", 10)
        self.cell(40, 6, label)
        self.set_font("Helvetica", "", 10)
        self.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

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
            self.cell(col_widths[3], 6, f"₦{unit_price:,.2f}", border=1, align="R")
            self.cell(col_widths[4], 6, f"{discount_pct:.0f}%", border=1, align="C")
            self.cell(col_widths[5], 6, f"₦{line_total:,.2f}", border=1, align="R")
            self.ln()

    def totals_section(self, subtotal: float, tax: float = 0, total: float = 0):
        self.ln(3)
        self.set_font("Helvetica", "B", 10)
        x_start = self.w - 70
        self.set_x(x_start)
        self.cell(35, 7, "Subtotal:")
        self.cell(35, 7, f"₦{subtotal:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

        if tax > 0:
            self.set_x(x_start)
            self.cell(35, 7, "Tax:")
            self.cell(35, 7, f"₦{tax:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "B", 12)
        self.set_x(x_start)
        self.cell(35, 8, "Total:")
        self.cell(35, 8, f"₦{total:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    def notes_section(self, notes: str = "", terms: str = ""):
        if notes:
            self.ln(5)
            self.set_font("Helvetica", "B", 10)
            self.cell(0, 6, "Notes:", new_x="LMARGIN", new_y="NEXT")
            self.set_font("Helvetica", "", 9)
            self.multi_cell(0, 5, notes)

        if terms:
            self.ln(3)
            self.set_font("Helvetica", "B", 10)
            self.cell(0, 6, "Terms:", new_x="LMARGIN", new_y="NEXT")
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
) -> bytes:
    pdf = StoreFlowPDF(business_name)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title("Invoice", doc_number)
    pdf.info_row("Customer:", customer_name)
    if due_date:
        pdf.info_row("Due Date:", due_date)
    pdf.ln(3)

    pdf.items_table(items)
    pdf.totals_section(subtotal, tax, total)
    pdf.notes_section(notes, terms)

    return pdf.output()


def generate_receipt_pdf(
    sale_number: str,
    customer_name: str,
    items: list[dict],
    subtotal: float,
    payment_method: str = "Cash",
    total: float = 0,
    business_name: str = "StoreFlow",
) -> bytes:
    pdf = StoreFlowPDF(business_name)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title("Receipt", sale_number)
    pdf.info_row("Customer:", customer_name)
    pdf.info_row("Payment:", payment_method)
    pdf.ln(3)

    pdf.items_table(items)
    pdf.totals_section(subtotal, 0, total)

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
) -> bytes:
    pdf = StoreFlowPDF(business_name)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.document_title("Quote", doc_number)
    pdf.info_row("Customer:", customer_name)
    pdf.ln(3)

    pdf.items_table(items)
    pdf.totals_section(subtotal, tax, total)
    pdf.notes_section(notes, terms)

    return pdf.output()
