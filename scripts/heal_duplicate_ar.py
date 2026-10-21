"""Remove duplicate receivables created by the heal_sale_receivables migration.

The ``z7a8b9c0d1e2`` migration re-created AR rows for sales whose original
sub-ledger row linked by ``invoice_number`` and left ``invoice_id`` NULL, so
its ``NOT EXISTS (ar.invoice_id = ...)`` guard missed them. Every one of those
sales ended up with two receivable rows, and when a payment was recorded the
route posted one payment journal per row -- doubling the cash and the 1100
control account.

For each duplicate row this script

  1. posts a posted reversal journal against every ``ar_payment`` journal that
     references the duplicate (the same treatment ``void_sale`` gives a sale
     journal, so cash-flow, P&L and the trial balance all see it),
  2. deletes the duplicate ``ar_payments`` ledger rows (they describe money
     received for a receivable that never existed),
  3. deletes the duplicate ``accounts_receivable`` row itself.

Runs dry by default (prints what it would touch). Pass --apply to write, and
--tenant to limit it. Safe to re-run: a duplicate is only processed when no
reversal journal already references its payment journal.

    python -m scripts.heal_duplicate_ar
    python -m scripts.heal_duplicate_ar --apply
    python -m scripts.heal_duplicate_ar --apply --tenant <uuid>
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import aliased

from app.accounting.models import (
    AccountReceivable,
    ArPayment,
    Journal,
    JournalEntry,
)
from app.common.db.session import SessionLocal, set_rls_context


async def find_duplicates(session, tenant_id: UUID | None):
    newer = aliased(AccountReceivable)
    older = aliased(AccountReceivable)
    stmt = select(newer).join(
        older,
        (older.tenant_id == newer.tenant_id)
        & (older.invoice_number == newer.invoice_number)
        & (older.amount == newer.amount)
        & (older.created_at < newer.created_at),
    )
    if tenant_id:
        stmt = stmt.where(newer.tenant_id == tenant_id)
    return list((await session.execute(stmt)).scalars().unique().all())


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the fix")
    parser.add_argument("--tenant", type=UUID, required=True, help="tenant to heal")
    args = parser.parse_args()

    async with SessionLocal() as session:
        # The pooler role runs under RLS, so every query -- dry or apply --
        # needs the business context stamped on the session first.
        await set_rls_context(
            session,
            "00000000-0000-0000-0000-000000000000",
            str(args.tenant),
            "owner",
        )
        dupes = await find_duplicates(session, args.tenant)
        print(f"duplicate receivable rows found: {len(dupes)}")

        net_cash = Decimal(0)
        for dupe in dupes:
            journals = list(
                (
                    await session.execute(
                        select(Journal).where(
                            Journal.reference_id == dupe.id,
                            Journal.reference_type == "ar_payment",
                            Journal.status == "posted",
                        )
                    )
                ).scalars().all()
            )
            already = await session.execute(
                select(Journal.id).where(
                    Journal.reference_id.in_([j.id for j in journals] or [dupe.id]),
                    Journal.reference_type == "ar_payment_reversal",
                ).limit(1)
            )
            if already.scalar_one_or_none():
                print(f"  {dupe.invoice_number}: already reversed, skipping")
                continue

            pay_rows = await session.execute(
                select(ArPayment.id).where(ArPayment.ar_id == dupe.id)
            )
            print(
                f"  {dupe.invoice_number} row={dupe.id} amount={dupe.amount}: "
                f"{len(journals)} payment journal(s), {len(pay_rows.all())} payment rows"
            )
            for journal in journals:
                entries = list(
                    (
                        await session.execute(
                            select(JournalEntry).where(
                                JournalEntry.journal_id == journal.id
                            )
                        )
                    ).scalars().all()
                )
                for entry in entries:
                    if entry.account_code == "1100":
                        net_cash += Decimal(str(entry.debit or 0)) - Decimal(
                            str(entry.credit or 0)
                        )

        print(f"  receivable (1100) credit to reverse: {net_cash}")
        if not args.apply:
            print("dry run, nothing written (pass --apply to fix)")
            return 0

        now = datetime.now(UTC)
        for dupe in dupes:
            journals = list(
                (
                    await session.execute(
                        select(Journal).where(
                            Journal.reference_id == dupe.id,
                            Journal.reference_type == "ar_payment",
                            Journal.status == "posted",
                        )
                    )
                ).scalars().all()
            )
            already = await session.execute(
                select(Journal.id).where(
                    Journal.reference_id.in_([j.id for j in journals] or [dupe.id]),
                    Journal.reference_type == "ar_payment_reversal",
                ).limit(1)
            )
            if already.scalar_one_or_none():
                continue

            for journal in journals:
                entries = list(
                    (
                        await session.execute(
                            select(JournalEntry).where(
                                JournalEntry.journal_id == journal.id
                            )
                        )
                    ).scalars().all()
                )
                reversal_id = uuid4()
                reversal = Journal(
                    id=reversal_id,
                    tenant_id=dupe.tenant_id,
                    journal_number=(
                        f"JRN-{now.strftime('%Y%m%d')}-{str(reversal_id)[:8].upper()}"
                    ),
                    description=(
                        f"Reversal of {journal.journal_number} — "
                        f"duplicate receivable for {dupe.invoice_number}"
                    ),
                    reference_id=journal.id,
                    reference_type="ar_payment_reversal",
                    status="posted",
                    posted_at=now,
                    store_id=dupe.store_id,
                )
                session.add(reversal)
                for entry in entries:
                    session.add(
                        JournalEntry(
                            id=uuid4(),
                            journal_id=reversal_id,
                            tenant_id=dupe.tenant_id,
                            account_id=entry.account_id,
                            account_code=entry.account_code,
                            debit=float(entry.credit or 0),
                            credit=float(entry.debit or 0),
                            description=f"{entry.description or ''} (reversal)".strip(),
                            type=entry.type,
                            status="posted",
                            posted_at=now,
                            # Net convention: debit-credit for asset/expense
                            # entries, credit-debit for the rest.
                            amount=(
                                float(entry.debit or 0) - float(entry.credit or 0)
                                if entry.type in ("asset", "expense")
                                else float(entry.credit or 0) - float(entry.debit or 0)
                            ),
                        )
                    )

            await session.execute(delete(ArPayment).where(ArPayment.ar_id == dupe.id))
            await session.execute(delete(AccountReceivable).where(AccountReceivable.id == dupe.id))
            print(f"  {dupe.invoice_number}: reversed and row deleted")

        await session.commit()
        print("done")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
