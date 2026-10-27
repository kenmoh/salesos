"""Create the first platform admin (or verify an existing one).

Run from the repo root with the project venv:

    ./.venv/Scripts/python.exe scripts/create_platform_admin.py \
        --email admin@storeflow.ng --name "Ops Admin" [--role admin] [--totp]

The password is read interactively (getpass) unless --password is passed
(for scripted environments). With --totp the script also prints a
provisioning URI to enroll the new admin in an authenticator app after
their first login.
"""

import argparse
import asyncio
import getpass
import sys
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, dest="full_name")
    parser.add_argument("--role", default="admin", help="admin | super_admin | support | readonly")
    parser.add_argument("--password", default=None)
    parser.add_argument("--totp", action="store_true", help="generate a TOTP secret as well")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    from sqlalchemy import select

    from app.common.bridge import _get_shared_engine
    from app.core.security import generate_totp_secret, get_totp_uri, hash_password
    from app.platform.models import PlatformAdmin

    password = args.password or getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters", file=sys.stderr)
        return 1
    if args.role not in {"super_admin", "admin", "support", "readonly"}:
        print(f"Unknown role: {args.role}", file=sys.stderr)
        return 1

    engine = _get_shared_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            existing = await session.scalar(
                select(PlatformAdmin).where(PlatformAdmin.email == args.email)
            )
            if existing:
                print(f"Admin already exists: {args.email} (role={existing.role})")
                return 1

            admin = PlatformAdmin(
                id=uuid4(),
                email=args.email,
                password_hash=hash_password(password),
                full_name=args.full_name,
                role=args.role,
                totp_secret=generate_totp_secret() if args.totp else None,
                totp_enabled=False,
            )
            session.add(admin)
            await session.flush()

            print(f"Created platform admin {admin.email} (role={admin.role})")
            if args.totp and admin.totp_secret:
                print("TOTP provisioning URI (scan on first login setup):")
                print(get_totp_uri(admin.totp_secret, admin.email))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
