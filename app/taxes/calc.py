"""Tax arithmetic shared by sales, documents and the cart quote.

Tax identity lives in ``taxes``: a name, a rate, and the liability account the
money is owed to. There is no type enumeration and no cart-wide scope, because
neither survives a tenant inventing a tax we have never heard of. Scope is
expressed purely by assignment -- a product carries the taxes that apply to it
-- and the cart only ever aggregates what those lines produced.

Two rules everything here follows:

* Tax is computed on what was actually charged, so discounts come off the base
  first. A cart-level discount is spread across lines by gross share.
* A line's tax is a snapshot (name, rate, amount, account) written beside it and
  again on the header, grouped by name. Historical documents must not change
  when a tax is later renamed or repriced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Liability account for a tax with no better home. Created alongside
#: ``VAT_PAYABLE`` so a tenant never owes tax we cannot record.
DEFAULT_TAX_ACCOUNT = "2400"

#: Liability account for tax that is, by name, value added tax.
VAT_PAYABLE = "2300"

#: Names we treat as VAT when deciding the default account at creation time.
#: Only ever consulted while creating a tax, never while posting: the account
#: is stored on the tax and read back.
_VAT_NAMES = frozenset({"vat", "valueaddedtax", "vatpayable"})


def default_account_for(name: str) -> str:
    """Pick the liability account for a newly created tax.

    The name is the only identifier a tax has, so it is the only thing to go
    on -- and this is a default the creator can override, not a rule the ledger
    re-derives later.
    """
    # Letters only, so "VAT (7.5%)" and "VAT" resolve the same way while
    # "Vatable services" does not.
    normalized = "".join(ch for ch in (name or "").lower() if ch.isalpha())
    return VAT_PAYABLE if normalized in _VAT_NAMES else DEFAULT_TAX_ACCOUNT


def account_for(tax: dict) -> str:
    """Read the liability account off a tax dict, falling back to the default."""
    code = tax.get("account_code")
    return str(code) if code else DEFAULT_TAX_ACCOUNT


@dataclass
class TaxLine:
    """One cart/document line as the calculator sees it."""

    qty: float
    unit_price: float
    discount_pct: float = 0.0
    taxes: list[dict] = field(default_factory=list)
    #: When true, unit_price is treated as tax-inclusive: tax is deducted
    #: from the price so the customer still pays exactly unit_price * qty.
    tax_inclusive: bool = False


@dataclass
class TaxComputation:
    """Totals, per-line snapshots, and the header breakdown."""

    subtotal: float
    discount: float
    taxable: float
    total_tax: float
    breakdown: list[dict]
    line_taxes: list[list[dict]]
    line_tax_totals: list[float]
    line_rates: list[float]
    #: What each line actually charges, after its own discount and its share of
    #: the cart discount, before tax. Summing these gives ``taxable``.
    line_bases: list[float]
    #: What each line actually charges the customer for, after tax deduction
    #: on inclusive lines. Summing these gives the customer-facing total.
    line_totals: list[float]


def _round(value: float) -> float:
    return round(value + 0.0, 2)


def group_tax_lines(entries) -> list[dict]:
    """Group already-priced tax entries for display.

    Entries are snapshots: ``(name, rate, amount, account_code)``. Two entries
    belong to the same group when those identity fields match, and the amounts
    add up. Used by ``compute_taxes`` and by anything reading snapshots back
    out of storage, so a stored document groups the same way a fresh cart does.
    """
    grouped: dict[tuple[str, float, str], dict] = {}
    for tax in entries:
        rate = _round(float(tax.get("rate") or 0))
        key = (str(tax.get("name") or "Tax"), rate, str(tax.get("account_code") or ""))
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "id": tax.get("id"),
                "name": str(tax.get("name") or "Tax"),
                "rate": rate,
                "amount": _round(float(tax.get("amount") or 0)),
                "account_code": str(tax.get("account_code") or ""),
            }
        else:
            entry["amount"] = _round(entry["amount"] + float(tax.get("amount") or 0))
    return list(grouped.values())


def compute_taxes(
    lines: list[TaxLine],
    cart_discount: float = 0.0,
) -> TaxComputation:
    """Compute tax for a set of lines.

    Args:
        lines: The lines to tax, each carrying its assigned taxes.
        cart_discount: Discount applied to the whole cart, in money. Spread
            across lines by gross share so every line pays tax on what the
            customer actually pays for it.

    Returns:
        Totals plus snapshots: ``line_taxes[i]`` is line i's tax entries,
        ``breakdown`` is the same entries grouped by tax for display.
    """
    cart_discount = max(float(cart_discount), 0.0)
    grosses = [float(l.qty) * float(l.unit_price) for l in lines]
    subtotal = sum(grosses)

    line_discounts = [
        gross * (max(float(line.discount_pct), 0.0) / 100)
        for gross, line in zip(grosses, lines)
    ]

    # Spread the cart discount by gross share. Lines already carrying their own
    # discount still share it -- the discount is against the cart, not the line
    # that happened to be cheapest.
    shares: list[float] = []
    remaining = cart_discount
    for index, gross in enumerate(grosses):
        if index == len(grosses) - 1:
            share = remaining
        elif subtotal > 0:
            share = cart_discount * (gross / subtotal)
        else:
            share = 0.0
        shares.append(min(share, remaining))
        remaining = round(remaining - shares[-1], 10)

    line_taxes: list[list[dict]] = []
    line_tax_totals: list[float] = []
    line_rates: list[float] = []
    line_bases: list[float] = []
    #: What each line actually charges the customer for, after any tax
    #: deduction on inclusive lines. Exclusive lines charge base + tax.
    line_totals: list[float] = []

    for line, gross, line_discount, share in zip(lines, grosses, line_discounts, shares):
        gross_after_discount = max(gross - line_discount - share, 0.0)

        applied: list[dict] = []
        seen: set[str] = set()
        total_rate = sum(float(t.get("rate") or 0) for t in (line.taxes or []))
        denominator = 100 + total_rate if line.tax_inclusive else 100
        for tax in line.taxes or []:
            # The same tax assigned twice to one product must not charge twice.
            key = str(tax.get("id") or tax.get("name") or "")
            if key and key in seen:
                continue
            seen.add(key)

            rate = float(tax.get("rate") or 0)
            # Inclusive pricing splits the tax across the price-inclusive base:
            # each tax is rate / (100 + sum of all rates) of the price.
            amount = _round(gross_after_discount * rate / denominator)
            applied.append(
                {
                    "id": tax.get("id"),
                    "name": str(tax.get("name") or "Tax"),
                    "rate": _round(rate),
                    "amount": _round(amount),
                    "account_code": account_for(tax),
                }
            )

        line_tax_total = _round(sum(t["amount"] for t in applied))
        line_rate_total = _round(sum(t["rate"] for t in applied))
        line_taxes.append(applied)
        line_tax_totals.append(line_tax_total)
        line_rates.append(line_rate_total)

        if line.tax_inclusive:
            # Customer pays exactly gross_after_discount; tax comes out of it.
            line_bases.append(max(gross_after_discount - line_tax_total, 0.0))
            line_totals.append(gross_after_discount)
        else:
            # Tax added on top of the price.
            line_bases.append(gross_after_discount)
            line_totals.append(gross_after_discount + line_tax_total)

    breakdown = group_tax_lines(tax for applied in line_taxes for tax in applied)
    line_discount_total = sum(line_discounts)

    return TaxComputation(
        subtotal=_round(subtotal),
        discount=_round(line_discount_total + cart_discount),
        taxable=_round(sum(line_bases)),
        total_tax=_round(sum(line_tax_totals)),
        breakdown=breakdown,
        line_taxes=line_taxes,
        line_tax_totals=list(line_tax_totals),
        line_rates=list(line_rates),
        line_bases=[_round(b) for b in line_bases],
        line_totals=[_round(t) for t in line_totals],
    )
