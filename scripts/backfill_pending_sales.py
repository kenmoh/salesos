"""Backfill accounting for sales that were created but never booked.

An unpaid sale used to produce no journal at all, so revenue and the receivable
were both absent from the books until a payment arrived. Sales created through
document conversion never get one, because nothing pays a converted sale. The
platform's commission was booked only when a sale reached "completed" and so
escaped those sales entirely.

This books what is owed:

  Dr Accounts Receivable (1100)   the sale total
  Cr Sales Revenue (4000)         the total, less tax
  Cr VAT Payable (2300)            tax, when the sale carried any
  Dr Cost of Goods Sold (5000)     at the store's own cost price
  Cr Inventory (1200)              the same

It also books the platform fee for each sale, once, through
``record_platform_fee``, which refuses a sale that is already charged.

Runs dry by default. Pass --apply to write, and --tenant to limit it. Every
sale already carrying a posted sale journal is skipped, so re-running is safe
and will not double-count.

    python -m scripts.backfill_pending_sales
    python -m scripts.backfill_pending_sales --apply
    python -m scripts.backfill_pending_sales --apply --tenant <uuid>
"""

import argparse
import asyncio
import sys
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text

from app.accounting.models import AccountReceivable, ChartOfAccount, Journal
from app.common.bridge import _post_sale_journal
from app.common.db.session import SessionLocal, set_rls_context
from app.platform.fee_calculator import record_platform_fee
from app.sales.models import Sale, SaleItem
from app.stores.models import StoreProduct


async def _cost_map(session, sale: Sale, items: list[SaleItem]) -> dict[str, float]:
    """Cost per product for this sale's store, matching what checkout uses."""
    product_ids = {str(i.product_id) for i in items if i.product_id}
    if not product_ids or not sale.store_id:
        return {}
    rows = (
        await session.execute(
            select(StoreProduct).where(
                StoreProduct.store_id == sale.store_id,
                StoreProduct.product_id.in_([UUID(p) for p in product_ids]),
            )
        )
    ).scalars().all()
    return {str(sp.product_id): float(sp.cost_price or 0) for sp in rows}


async def _already_booked(session, sale_id: UUID) -> bool:
    existing = (
        await session.execute(
            select(Journal.id).where(
                Journal.reference_type == "sale",
                Journal.reference_id == sale_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    return existing is not None


async def _ensure_receivable(session, sale: Sale) -> bool:
    """Give the sale a row in the receivables sub-ledger.

    The balance sheet reads receivables from the ledger but the dashboard and
    the reconciliation read the sub-ledger, so a sale booked as a receivable
    without a matching row here shows as no receivable at all and the two
    disagree by the full amount. Matched on the sale number, so running this
    again adds nothing.
    """
    existing = (
        await session.execute(
            select(AccountReceivable.id).where(
                AccountReceivable.tenant_id == sale.tenant_id,
                AccountReceivable.invoice_number == sale.sale_number,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return False

    from datetime import UTC, datetime

    session.add(
        AccountReceivable(
            tenant_id=sale.tenant_id,
            invoice_id=None,
            customer_id=None,
            customer_name=sale.customer_name or sale.sale_number,
            # The sale number is the reference; there is no invoice behind it.
            invoice_number=sale.sale_number,
            amount=sale.total,
            amount_paid=0,
            balance=sale.total,
            due_date=sale.created_at or datetime.now(UTC),
            status="pending",
            store_id=sale.store_id,
        )
    )
    return True


async def _book_one(tid: str, sale_id: UUID, apply: bool) -> str | None:
    """Book a single sale. Returns a description of what happened."""
    async with SessionLocal() as session:
        await set_rls_context(
            session, "00000000-0000-0000-0000-000000000000", tid, "owner"
        )

        sale = (
            await session.execute(
                select(Sale).where(Sale.id == sale_id, Sale.tenant_id == UUID(tid))
            )
        ).scalar_one_or_none()
        if sale is None or sale.status != "pending":
            return None
        if await _already_booked(session, sale.id):
            return None

        items = (
            await session.execute(select(SaleItem).where(SaleItem.sale_id == sale.id))
        ).scalars().all()
        costs = await _cost_map(session, sale, items)

        journal_items = [
            {
                "product_name": i.product_name,
                "qty": float(i.qty),
                "unit_price": float(i.unit_price),
                "cost_price": costs.get(str(i.product_id), 0.0),
            }
            for i in items
        ]

        # Read the row into locals before anything commits. A rollback expires
        # every attribute, and touching one afterwards is a lazy load outside
        # the async context.
        sale_uid = sale.id
        number = sale.sale_number
        total = float(sale.total or 0)
        discount = float(sale.discount or 0)
        tax_amount = float(sale.tax or 0)
        cashier_id = str(sale.cashier_id) if sale.cashier_id else None
        store_id = str(sale.store_id) if sale.store_id else None

        if not apply:
            from app.platform.fee_calculator import calculate_platform_fee

            fee = await calculate_platform_fee(session, total)
            return f"  would book {number} N{total:,.2f} (fee N{fee['platform_fee']:,.2f})"

        await _post_sale_journal(
            tenant_id=tid,
            sale_id=str(sale_uid),
            sale_number=number,
            sale_items=journal_items,
            total=total,
            discount=discount,
            tax_amount=tax_amount,
            cashier_id=cashier_id,
            payment_method="cash",
            store_id=store_id,
            settled=False,
        )

        # _post_sale_journal commits on its own session; this one has to be
        # rolled back first so the fee query sees committed state.
        await session.rollback()
        await set_rls_context(
            session, "00000000-0000-0000-0000-000000000000", tid, "owner"
        )
        fee = await record_platform_fee(
            session,
            tenant_id=UUID(tid),
            total=total,
            sale_id=sale_uid,
            payment_method="cash",
        )
        # The ledger entry is already written; this is the matching row in the
        # receivables sub-ledger the dashboard and reconciliation read.
        await _ensure_receivable(session, sale)
        await session.commit()

        amount = fee["platform_fee"] if fee else 0
        return f"  booked {number} N{total:,.2f} (fee N{amount:,.2f})"


async def reconcile_subledger(tenant_id: str | None, apply: bool) -> int:
    """Give every booked unpaid sale its receivables row.

    Separate from the booking pass because a sale already journalled by an
    earlier run still has no sub-ledger row, and skipping it is what left the
    dashboard reading no receivables while the ledger held them.
    """
    added = 0
    async with SessionLocal() as session:
        rows = (await session.execute(text("SELECT id::text FROM tenants"))).scalars().all()
        tenants = [tenant_id] if tenant_id else list(rows)

    for tid in tenants:
        async with SessionLocal() as session:
            await set_rls_context(
                session, "00000000-0000-0000-0000-000000000000", tid, "owner"
            )
            sales = (
                await session.execute(
                    select(Sale).where(
                        Sale.tenant_id == UUID(tid), Sale.status == "pending"
                    )
                )
            ).scalars().all()
            todo = [
                s.id for s in sales if await _already_booked(session, s.id)
            ]

        for sale_id in todo:
            async with SessionLocal() as session:
                await set_rls_context(
                    session, "00000000-0000-0000-0000-000000000000", tid, "owner"
                )
                sale = (
                    await session.execute(
                        select(Sale).where(
                            Sale.id == sale_id, Sale.tenant_id == UUID(tid)
                        )
                    )
                ).scalar_one_or_none()
                if sale is None:
                    continue
                if apply:
                    if await _ensure_receivable(session, sale):
                        await session.commit()
                        added += 1
                else:
                    existing = (
                        await session.execute(
                            select(AccountReceivable.id).where(
                                AccountReceivable.tenant_id == UUID(tid),
                                AccountReceivable.invoice_number == sale.sale_number,
                            ).limit(1)
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        print(f"  would add receivable for {sale.sale_number}")
                        added += 1

    verb = "added" if apply else "to add"
    print(f"{added} receivables row(s) {verb}.")
    return added


async def process(tenant_id: str | None, apply: bool) -> int:
    booked = 0
    skipped = 0

    async with SessionLocal() as session:
        # Read tenants from the tenancy table, not from sales: sales is under
        # row-level security and there is no tenant context yet at this point,
        # so asking it for the list of tenants returns nothing at all.
        tenant_rows = (await session.execute(text("SELECT id::text FROM tenants"))).scalars().all()
        tenants = [tenant_id] if tenant_id else list(tenant_rows)

    for tid in tenants:
        # Collect ids first, then handle each in its own session: committing one
        # sale expires every other loaded row, and reading them afterwards
        # would be a lazy load outside the async context.
        async with SessionLocal() as session:
            await set_rls_context(
                session, "00000000-0000-0000-0000-000000000000", tid, "owner"
            )
            pending = (
                await session.execute(
                    select(Sale.id).where(
                        Sale.tenant_id == UUID(tid), Sale.status == "pending"
                    )
                )
            ).scalars().all()
            already = [
                sid
                for sid in pending
                if await _already_booked(session, sid)
            ]
            todo = [sid for sid in pending if sid not in already]
            skipped += len(already)

        for sale_id in todo:
            line = await _book_one(tid, sale_id, apply)
            if line is None:
                skipped += 1
                continue
            print(line)
            booked += 1

    await reconcile_subledger(tenant_id, apply)

    mode = "applied" if apply else "DRY RUN — nothing written"
    print(
        f"\n{booked} sale(s) {'booked' if apply else 'to book'}, "
        f"{skipped} skipped. {mode}"
    )
    return booked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the entries")
    parser.add_argument("--tenant", default=None, help="limit to one tenant id")
    args = parser.parse_args()

    if not args.apply:
        print("Dry run. Re-run with --apply to write.\n")

    asyncio.run(process(args.tenant, args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())