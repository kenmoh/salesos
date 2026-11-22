"""Naira ↔ US dollar conversion for geo-aware pricing.

Rates come from Frankfurter (keyless daily reference rates), cached for 24h.
Amounts are converted for display and for USD payment links — the app's
internal ledger remains NGN-based.

Frankfurter v2: GET /v2/rate/ngn/usd -> {"date", "base", "quote", "rate"}
where ``rate`` is USD per 1 NGN.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.common.cache import cache

_FX_CACHE_KEY = "sf:cache:fx:NGN_USD"
_FX_URL = "https://api.frankfurter.dev/v2/rate/ngn/usd"
_TTL = 86400
# Fallback used only if both the API and cache and admin setting are missing.
_FALLBACK_USD_PER_NGN = Decimal("0.0007")


def usd_per_ngn_from_payload(payload: dict) -> Decimal | None:
    """Parse a Frankfurter rate response into USD-per-NGN."""
    try:
        rate = Decimal(str(payload["rate"]))
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None
    return rate if rate > 0 else None


async def _fetch_rate() -> dict | None:
    """Fetch the current rate from Frankfurter."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(_FX_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None
    rate = usd_per_ngn_from_payload(data)
    if rate is None:
        return None
    return {
        "rate": str(rate),
        "usd_per_ngn": str(rate),
        "ngn_per_usd": str((Decimal("1") / rate).quantize(Decimal("0.01"))),
        "as_of": data.get("date") or datetime.now(UTC).date().isoformat(),
        "source": "frankfurter",
    }


async def get_rate() -> dict:
    """Return the cached FX snapshot, fetching it when stale.

    Shape: {rate, usd_per_ngn, ngn_per_usd, as_of, source}
    """
    cached = await cache.get(_FX_CACHE_KEY)
    if cached and "usd_per_ngn" in cached:
        try:
            cached["rate_decimal"] = Decimal(str(cached["usd_per_ngn"]))
        except (ValueError, TypeError, InvalidOperation):
            cached["rate_decimal"] = _FALLBACK_USD_PER_NGN
        return cached

    fresh = await _fetch_rate()
    if fresh:
        await cache.set(_FX_CACHE_KEY, fresh, _TTL)
        fresh["rate_decimal"] = Decimal(fresh["usd_per_ngn"])
        return fresh

    # Last-resort fallback. Never break a pricing page because FX is down.
    return {
        "rate": str(_FALLBACK_USD_PER_NGN),
        "usd_per_ngn": str(_FALLBACK_USD_PER_NGN),
        "ngn_per_usd": str((Decimal("1") / _FALLBACK_USD_PER_NGN).quantize(Decimal("0.01"))),
        "as_of": "fallback",
        "source": "fallback",
        "rate_decimal": _FALLBACK_USD_PER_NGN,
    }


def ngn_to_usd(amount: Decimal, usd_per_ngn: Decimal) -> Decimal:
    return (Decimal(str(amount)) * usd_per_ngn).quantize(Decimal("0.01"))


def usd_to_ngn(amount: Decimal, usd_per_ngn: Decimal) -> Decimal:
    if usd_per_ngn <= 0:
        return Decimal("0")
    return (Decimal(str(amount)) / usd_per_ngn).quantize(Decimal("0.01"))


def public_fx_settings(snapshot: dict) -> dict:
    """Public-safe FX fields for /pricing/settings."""
    return {
        "fx_usd_rate": str(snapshot.get("usd_per_ngn", _FALLBACK_USD_PER_NGN)),
        "fx_ngn_per_usd": snapshot.get("ngn_per_usd", "1428.57"),
        "fx_as_of": snapshot.get("as_of", "fallback"),
        "fx_source": snapshot.get("source", "fallback"),
    }
