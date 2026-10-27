"""platform admin 2fa columns

Revision ID: 1af4eac8acb8
Revises: b2d3e4f5a6b7
Create Date: 2026-10-27 02:34:21.563379
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1af4eac8acb8'
down_revision: Union[str, None] = 'b2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_admins",
        sa.Column("totp_secret", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "platform_admins",
        sa.Column(
            "totp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("platform_admins", "totp_enabled")
    op.drop_column("platform_admins", "totp_secret")
