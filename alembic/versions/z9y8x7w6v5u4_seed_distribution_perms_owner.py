"""seed distribution permissions to owner roles

Revision ID: z9y8x7w6v5u4
Revises: e6f7a8b9c0d1
Create Date: 2026-09-21
"""
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = "z9y8x7w6v5u4"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Ensure distribution permissions exist
    for perm_name, perm_desc in (
        ("inventory:distribution_create", "Create warehouse distributions"),
        ("inventory:distribution_receive", "Receive warehouse distributions"),
        ("inventory:distribution_read", "View distributions"),
    ):
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, name, description) "
                "VALUES (:id, :name, :desc) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"id": str(uuid4()), "name": perm_name, "desc": perm_desc},
        )

    # Link distribution permissions to all owner roles
    rows = conn.execute(sa.text("SELECT id FROM roles WHERE name = 'owner'")).fetchall()
    for row in rows:
        role_id = row[0]
        for perm_name in (
            "inventory:distribution_create",
            "inventory:distribution_receive",
            "inventory:distribution_read",
        ):
            perm_row = conn.execute(
                sa.text("SELECT id FROM permissions WHERE name = :name"),
                {"name": perm_name},
            ).fetchone()
            if perm_row:
                conn.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, permission_id) "
                        "VALUES (:role_id, :perm_id) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"role_id": str(role_id), "perm_id": str(perm_row[0])},
                )


def downgrade() -> None:
    conn = op.get_bind()
    for perm_name in (
        "inventory:distribution_create",
        "inventory:distribution_receive",
        "inventory:distribution_read",
    ):
        perm_row = conn.execute(
            sa.text("SELECT id FROM permissions WHERE name = :name"),
            {"name": perm_name},
        ).fetchone()
        if perm_row:
            conn.execute(
                sa.text("DELETE FROM role_permissions WHERE permission_id = :id"),
                {"id": str(perm_row[0])},
            )
            conn.execute(
                sa.text("DELETE FROM permissions WHERE name = :name"),
                {"name": perm_name},
            )
