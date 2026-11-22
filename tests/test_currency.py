"""Currency symbols are tenant-configurable; amounts are never converted."""

from decimal import Decimal

from app.common.currency import (
    DEFAULT_CURRENCY,
    currency_symbol,
    format_money,
    format_money_ascii,
    normalize_currency,
)
from app.tenancy.settings import currency_code


def test_defaults_to_naira():
    assert currency_symbol(None) == "\u20a6"
    assert currency_symbol("") == "\u20a6"
    assert currency_code(None) == DEFAULT_CURRENCY


def test_known_currencies():
    assert currency_symbol("ngn") == "\u20a6"
    assert currency_symbol("GHS") == "GH\u20b5"
    assert currency_symbol("USD") == "$"
    assert currency_symbol("KES") == "KSh"


def test_unknown_free_text_code_is_used_as_prefix():
    assert currency_symbol("xyz") == "XYZ "
    assert format_money(1500, "xyz") == "XYZ 1,500"


def test_normalize_currency():
    assert normalize_currency(" ghs ") == "GHS"
    assert normalize_currency("") is None


def test_format_money_keeps_the_number_unchanged():
    assert format_money(3500, "NGN", decimals=2) == "\u20a63,500.00"
    assert format_money(3500, "GHS", decimals=2) == "GH\u20b53,500.00"
    assert format_money(Decimal("1250.5"), "USD", decimals=2) == "$1,250.50"


def test_ascii_formatter_uses_the_code_for_latin1_pdfs():
    assert format_money_ascii(3500, "NGN", decimals=2) == "NGN 3,500.00"
    assert format_money_ascii(3500, "GHS", decimals=2) == "GHS 3,500.00"


def test_tenant_settings_currency():
    assert currency_code('{"currency": "ghs"}') == "GHS"
    assert currency_code("{}") == "NGN"
    assert currency_code("not json") == "NGN"
