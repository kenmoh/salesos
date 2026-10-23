"""Backfill stock history for opening stock recorded before the audit trail fix.

Products created with an opening quantity used to write a ``stock_adjustments``
row with ``reason='initial_stock'`` but no matching ``stock_movements`` row, so
the product history screen showed nothing for that stock. New writes now record
both. This script fills the gap for old rows.

For each adjustment it inserts one movement:

  movement_type  adjustment
  qty_change     the adjustment quantity
  balance_before 0
  balance_after  the adjustment quantity
  reference      adjustment / <adjustment id>
  reason         initial_stock

Adjustments that already have a matching movement are skipped, so re-running is
safe.

Runs dry by default. Pass --apply to write, and --tenant to limit it.

    python -m scripts.backfill_initial_stock_movements
    python -m scripts.backfill_initial_stock_movements --apply
    python -m scripts.backfill_initial_stock_movements --apply --tenant <uuid>
"""

import argparse
import asyncio
import sys
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text

from app.common.db.session import SessionLocal, set_rls_context
from app.inventory.models import StockAdjustment, StockMovement


async def _existing_adjustment_ids(session, tenant_id: UUID) -> set[UUID]:
    rows = (
        await session.execute(
            select(StockMovement.reference_id).where(
                StockMovement.tenant_id == tenant_id,
                StockMovement.reference_type == "adjustment",
                StockMovement.reference_id.is_not(None),
            )
        )
    ).scalars().all()
    return {row for row in rows if row is not None}


async def process(tenant_id: str | None, apply: bool) -> int:
    added = 0
    skipped = 0

    async with SessionLocal() as session:
        tenant_rows = (
            await session.execute(text("SELECT id::text FROM tenants"))
        ).scalars().all()
        tenants = [tenant_id] if tenant_id else list(tenant_rows)

    for tid in tenants:
        tenant_uuid = UUID(tid)
        async with SessionLocal() as session:
            await set_rls_context(
                session, "00000000-0000-0000-0000-000000000000", tid, "owner"
            )
            existing = await _existing_adjustment_ids(session, tenant_uuid)
            adjustments = (
                await session.execute(
                    select(StockAdjustment)
                    .where(
                        StockAdjustment.tenant_id == tenant_uuid,
                        StockAdjustment.reason == "initial_stock",
                    )
                    .order_by(StockAdjustment.created_at)
                )
            ).scalars().all()

            todo = [a for a in adjustments if a.id not in existing]
            skipped += len(adjustments) - len(todo)

            if not apply:
                for adjustment in todo:
                    print(
                        f"  would add opening stock movement for product "
                        f"{adjustment.product_id} ({adjustment.qty_change})"
                    )
                    added += 1
                continue

            for adjustment in todo:
                session.add(
                    StockMovement(
                        tenant_id=tenant_uuid,
                        product_id=adjustment.product_id,
                        store_id=adjustment.store_id,
                        movement_type="adjustment",
                        qty_change=adjustment.qty_change,
                        balance_before=Decimal("0"),
                        balance_after=adjustment.qty_change,
                        reference_type="adjustment",
                        reference_id=adjustment.id,
                        reason="initial_stock",
                        unit_cost=adjustment.unit_cost,
                        notes=adjustment.notes,
                        created_by=adjustment.created_by,
                        created_at=adjustment.created_at,
                    )
                )
                added += 1
            await session.commit()

    mode = "applied" if apply else "DRY RUN -- nothing written"
    verb = "added" if apply else "to add"
    print(f"\n{added} opening stock movement(s) {verb}, {skipped} skipped. {mode}")
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the movements")
    parser.add_argument("--tenant", default=None, help="limit to one tenant id")
    args = parser.parse_args()

    if not args.apply:
        print("Dry run. Re-run with --apply to write.\n")

    asyncio.run(process(args.tenant, args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
