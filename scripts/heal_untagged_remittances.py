"""Attribute untagged tax remittance journals to a store.

Remittances posted before remitting became store-bound (the All Stores
workflow) left ``journals.store_id`` NULL. Store-filtered owed balances join
on journals.store_id, so whichever store the tax was collected in kept
showing the amount as still owed even after the merchant paid it — the
remittance was invisible to every store view.

For the tenant given, every ``tax_remittance`` journal with no store gets the
store named by --store. Run the dry pass first; it prints exactly the rows it
would tag. Safe to re-run: untagged rows only shrink.

    python -m scripts.heal_untagged_remittances --tenant <uuid> --store <uuid>
    python -m scripts.heal_untagged_remittances --tenant <uuid> --store <uuid> --apply
"""

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select

from app.accounting.models import Journal
from app.common.db.session import SessionLocal, set_rls_context


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the tags")
    parser.add_argument("--tenant", type=UUID, required=True, help="tenant to heal")
    parser.add_argument("--store", type=UUID, required=True, help="store to attribute to")
    args = parser.parse_args()

    async with SessionLocal() as session:
        # journals is FORCE ROW SECURITY: the pooler role sees nothing here
        # without a business context stamped on the session first.
        await set_rls_context(
            session, "00000000-0000-0000-0000-000000000000", str(args.tenant), "owner"
        )
        untagged = list(
            (
                await session.execute(
                    select(Journal)
                    .where(
                        Journal.tenant_id == args.tenant,
                        Journal.reference_type == "tax_remittance",
                        Journal.store_id.is_(None),
                    )
                    .order_by(Journal.posted_at)
                )
            ).scalars().all()
        )
        print(f"untagged tax_remittance journals: {len(untagged)}")

        if not untagged:
            print("nothing to do")
            return 0

        for j in untagged:
            print(f"  {j.id} {j.posted_at} {j.description}")

        if not args.apply:
            print(f"would tag these to store {args.store}; pass --apply to write")
            return 0

        for j in untagged:
            j.store_id = args.store
        await session.commit()
        print(f"tagged {len(untagged)} journal(s) to store {args.store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
