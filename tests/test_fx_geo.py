"""FX conversion and geo country resolution."""

from decimal import Decimal

import pytest

from app.common.fx import (
    ngn_to_usd,
    public_fx_settings,
    usd_per_ngn_from_payload,
    usd_to_ngn,
)
from app.common.geo import country_from_headers, display_currency


def test_parse_frankfurter_rate():
    assert usd_per_ngn_from_payload({"rate": "0.0007", "date": "2026-11-22"}) == Decimal("0.0007")
    assert usd_per_ngn_from_payload({"rate": "0"}) is None
    assert usd_per_ngn_from_payload({}) is None


def test_ngn_usd_roundtrip():
    rate = Decimal("0.0007")
    usd = ngn_to_usd(Decimal("7500"), rate)
    assert usd == Decimal("5.25")
    assert usd_to_ngn(usd, rate) == Decimal("7500.00")


def test_public_fx_settings_shape():
    out = public_fx_settings({
        "usd_per_ngn": "0.0007",
        "ngn_per_usd": "1428.57",
        "as_of": "2026-11-22",
        "source": "frankfurter",
    })
    assert out["fx_usd_rate"] == "0.0007"
    assert out["fx_ngn_per_usd"] == "1428.57"
    assert out["fx_as_of"] == "2026-11-22"


def test_country_headers():
    class Headers(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    assert country_from_headers(Headers({"cf-ipcountry": "US"})) == "US"
    assert country_from_headers(Headers({})) is None


def test_display_currency():
    assert display_currency("NG") == "NGN"
    assert display_currency("ng") == "NGN"
    assert display_currency("US") == "USD"
    assert display_currency("") == "NGN"
