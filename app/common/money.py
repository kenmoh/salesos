from decimal import Decimal, ROUND_HALF_UP

KOBO = Decimal("100")
NAIRA_QUANT = Decimal("0.01")


def quantize_naira(amount: Decimal) -> Decimal:
    return amount.quantize(NAIRA_QUANT, rounding=ROUND_HALF_UP)


def naira_to_kobo(amount: Decimal) -> int:
    return int(quantize_naira(amount) * KOBO)


def kobo_to_naira(kobo: int) -> Decimal:
    return Decimal(str(kobo)) / KOBO


def format_naira(amount: Decimal, currency: str | None = None) -> str:
    """Format an amount with a currency symbol (defaults to NGN)."""
    from app.common.currency import format_money

    return format_money(quantize_naira(amount), currency, decimals=2)
