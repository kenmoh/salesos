"""Currency handling shared by receipts, PDFs, notifications and messages.

Only the *symbol* changes — amounts are never converted between currencies.
"""

from decimal import Decimal, InvalidOperation

DEFAULT_CURRENCY = "NGN"

CURRENCY_SYMBOLS: dict[str, str] = {
    "NGN": "\u20a6",      # ₦
    "GHS": "GH\u20b5",    # GH₵
    "USD": "$",
    "KES": "KSh",
    "ZAR": "R",
    "UGX": "USh",
    "TZS": "TSh",
    "XOF": "CFA",
    "GBP": "\u00a3",      # £
    "EUR": "\u20ac",      # €
}


def normalize_currency(code: str | None) -> str | None:
    """Uppercase a stored currency code; blank becomes None."""
    if not code or not str(code).strip():
        return None
    return str(code).strip().upper()


def currency_symbol(code: str | None) -> str:
    """Resolve a currency code to its display symbol (defaults to ₦)."""
    key = normalize_currency(code) or DEFAULT_CURRENCY
    return CURRENCY_SYMBOLS.get(key, f"{key} ")


def format_money(
    amount: int | float | Decimal | str | None,
    code: str | None = None,
    *,
    decimals: int | None = None,
    grouping: bool = True,
) -> str:
    """Format an amount with the appropriate currency symbol.

    Amounts are not converted — only the symbol changes.
    """
    try:
        value = Decimal(str(amount if amount is not None else 0))
    except (ValueError, TypeError, InvalidOperation):
        value = Decimal("0")

    if decimals is not None:
        quant = Decimal(1).scaleb(-decimals)
        number = f"{value.quantize(quant):,.{decimals}f}" if grouping else f"{value.quantize(quant):.{decimals}f}"
    else:
        number = f"{value:,.0f}" if grouping else f"{value:.0f}"

    return f"{currency_symbol(code)}{number}"


def format_money_ascii(
    amount: int | float | Decimal | str | None,
    code: str | None = None,
    *,
    decimals: int | None = None,
) -> str:
    """ASCII-only formatting for PDFs rendered with latin-1 fonts (e.g. Helvetica)."""
    from decimal import InvalidOperation

    key = normalize_currency(code) or DEFAULT_CURRENCY
    try:
        value = Decimal(str(amount if amount is not None else 0))
    except (ValueError, TypeError, InvalidOperation):
        value = Decimal("0")
    number = f"{value:,.{decimals}f}" if decimals is not None else f"{value:,.0f}"
    return f"{key} {number}"
