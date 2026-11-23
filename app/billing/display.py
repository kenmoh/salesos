"""Display-currency conversion for tenant billing responses.

The internal ledger is NGN-only. USD tenants see NGN amounts converted with
the cached FX rate, alongside the rate/as-of basis for transparency. The
stored amounts themselves are never modified.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.currency import DEFAULT_CURRENCY
from app.common.fx import get_rate, ngn_to_usd
from app.tenancy.settings import currency_code


async def display_context(tenant_id, session: AsyncSession) -> dict:
    """Resolve a tenant's display currency and FX basis."""
    from sqlalchemy import text
    from uuid import UUID as _UUID

    result = await session.execute(
        text("SELECT settings FROM tenants WHERE id = :tid"),
        {"tid": _UUID(str(tenant_id))},
    )
    row = result.mappings().first()
    currency = currency_code(row["settings"] if row else None)
    if currency == DEFAULT_CURRENCY:
        return {"currency": DEFAULT_CURRENCY, "rate": None, "as_of": None}
    snapshot = await get_rate()
    return {
        "currency": currency,
        "rate": snapshot["usd_per_ngn"],
        "as_of": snapshot["as_of"],
    }


def display_amount(amount: Decimal | float | int | None, ctx: dict) -> Decimal | None:
    """Convert an NGN amount for display; None means leave it unconverted."""
    if ctx.get("rate") is None or amount is None:
        return None
    return ngn_to_usd(Decimal(str(amount)), Decimal(str(ctx["rate"])))


def fx_fields(ctx: dict) -> dict:
    return {
        "display_currency": ctx["currency"],
        "display_fx_rate": ctx.get("rate"),
        "display_fx_as_of": ctx.get("as_of"),
    }
