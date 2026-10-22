"""Grant taxes permissions to roles that can already see accounting.

Every role that can read (or write) the books should be able to read (or
manage) taxes: the taxes screens hang off the same ledger, so a role holding
``accounting:read`` without ``taxes:read`` gets an accounting tab whose tax
widgets it cannot open. Roles created before the taxes permissions were added
to the role templates stopped exactly there.

For each such role this script grants

  1. ``taxes:read``   when it holds ``accounting:read``,
  2. ``taxes:manage`` when it holds ``accounting:write``.

Runs dry by default (prints what it would touch). Pass --apply to write.
Safe to re-run: a grant is only made when the role does not already have it.

    python -m scripts.heal_taxes_permissions
    python -m scripts.heal_taxes_permissions --apply
"""

import argparse
import asyncio

from sqlalchemy import select, text

from app.identity.models import Permission, Role, RolePermission
from app.common.db.session import SessionLocal, set_rls_context

GRANTS = [("accounting:read", "taxes:read"), ("accounting:write", "taxes:manage")]


async def grant_in_tenant(session, perm_ids: dict[str, str]) -> list[tuple]:
    """Missing taxes grants for one tenant's roles; call inside an RLS context."""
    missing = []
    for src_name, tax_name in GRANTS:
        src_id, tax_id = perm_ids.get(src_name), perm_ids.get(tax_name)
        if not src_id or not tax_id:
            print(f"permission row missing: {src_name} / {tax_name}; nothing to do")
            continue

        roles_holding = (
            await session.execute(
                select(Role).join(
                    RolePermission, RolePermission.role_id == Role.id
                ).where(RolePermission.permission_id == src_id)
            )
        ).scalars().unique().all()

        for role in roles_holding:
            existing = (
                await session.execute(
                    select(RolePermission.permission_id).where(
                        RolePermission.role_id == role.id,
                        RolePermission.permission_id == tax_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            missing.append((role, src_name, tax_name))
    return missing


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the grants")
    args = parser.parse_args()

    permission_grant_rows = 0

    async with SessionLocal() as session:
        # The tenants catalog is not RLS-protected; roles are (FORCE ROW
        # SECURITY), so roles are only readable once an owner context is
        # stamped for the tenant being healed.
        perm_ids = dict(
            (await session.execute(select(Permission.name, Permission.id))).fetchall()
        )

        tenant_ids = [
            row[0]
            for row in (await session.execute(text("select id from tenants"))).fetchall()
        ]

        missing = []
        for tenant_id in sorted(tenant_ids):
            await set_rls_context(
                session, "00000000-0000-0000-0000-000000000000", str(tenant_id), "owner"
            )
            missing.extend(await grant_in_tenant(session, perm_ids))

        for role, src_name, tax_name in missing:
            print(f"[{role.tenant_id}] {role.name}: {src_name} without {tax_name}")

        if not missing:
            print("nothing to do")
            return 0
        if not args.apply:
            print(f"would grant {len(missing)} permission(s); pass --apply to write")
            return 0

        for role, _, tax_name in missing:
            tax_id = perm_ids[tax_name]
            session.add(RolePermission(role_id=role.id, permission_id=tax_id))
        await session.commit()
        print(f"granted {len(missing)} permission(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))



if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
