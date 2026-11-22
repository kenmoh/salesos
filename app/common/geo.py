"""Resolve a visitor's country from their IP for currency display.

Checks proxy-provided country headers first, then falls back to a keyless
IP geolocation API. Results are cached 24h by IP. Fail-closed to Nigeria.
"""

import httpx

from app.common.cache import cache

_GEO_CACHE_PREFIX = "sf:cache:geo:"
_TTL = 86400
_DEFAULT_COUNTRY = "NG"


def country_from_headers(headers) -> str | None:
    """Read a country code supplied by a trusted proxy, if present."""
    for header in ("cf-ipcountry", "x-country-code", "x-geo-country"):
        code = headers.get(header)
        if code and len(code.strip()) == 2:
            return code.strip().upper()
    return None


async def _lookup_country(ip: str) -> str | None:
    """Ask a keyless geo API for the IP's ISO country code."""
    if not ip or ip in ("0.0.0.0", "127.0.0.1", "::1"):
        return None
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{ip}",
                params={"fields": "countryCode"},
            )
            if resp.status_code != 200:
                return None
            code = resp.json().get("countryCode")
            return code.upper() if isinstance(code, str) and len(code) == 2 else None
    except Exception:
        return None


async def country_from_ip(ip: str, headers=None) -> str:
    """Resolve an ISO country code (defaults to NG on any failure)."""
    if headers is not None:
        header_country = country_from_headers(headers)
        if header_country:
            return header_country
    if not ip:
        return _DEFAULT_COUNTRY

    cache_key = f"{_GEO_CACHE_PREFIX}{ip}"
    cached = await cache.get(cache_key)
    if isinstance(cached, str) and len(cached) == 2:
        return cached

    country = await _lookup_country(ip)
    if not country:
        return _DEFAULT_COUNTRY
    await cache.set(cache_key, country, _TTL)
    return country


def display_currency(country: str) -> str:
    """Nigeria displays NGN; everyone else displays USD."""
    return "NGN" if (country or _DEFAULT_COUNTRY).upper() == "NG" else "USD"
