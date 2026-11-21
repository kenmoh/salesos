"""Add last-known device columns to users and platform_admins.

Signing in records the device the client used and when it was seen. The
values live on the account row itself and are overwritten on every sign-in,
so tracking a device never adds a row to the table.

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-11-26 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "i0j1k2l3m4n5"
down_revision: Union[str, None] = "h9i0j1k2l3m4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("last_device_type", "VARCHAR(20)"),
    ("last_browser", "VARCHAR(50)"),
    ("last_seen_at", "TIMESTAMPTZ"),
    ("last_user_agent", "VARCHAR(500)"),
    ("last_ip_address", "VARCHAR(45)"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for table in ("users", "platform_admins"):
        for column, column_type in _COLUMNS:
            conn.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}"
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    for table in ("users", "platform_admins"):
        for column, _ in _COLUMNS:
            conn.execute(
                text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
            )
