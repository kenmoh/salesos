"""seed taxes permissions

Revision ID: v1w2x3y4z5a6
Revises: p5q6r7s8t9u0
Create Date: 2026-09-21
"""
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = "v1w2x3y4z5a6"
down_revision = "q6r7s8t9u0v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Insert missing tax permissions into permissions table
    for perm_name in ("taxes:read", "taxes:manage"):
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, name) "
                "VALUES (:id, :name) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": str(uuid4()), "name": perm_name},
        )

    # Link taxes:read and taxes:manage to all owner roles
    rows = conn.execute(
        sa.text("SELECT id FROM roles WHERE name = 'owner'")
    ).fetchall()

    for row in rows:
        role_id = row[0]
        for perm_name in ("taxes:read", "taxes:manage"):
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
    for perm_name in ("taxes:read", "taxes:manage"):
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
