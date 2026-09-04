"""seed_discounts_coupons_permissions

Revision ID: i9j0k1l2m3n4
Revises: h7i8j9k0l1m2
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa


revision = "i9j0k1l2m3n4"
down_revision = "h7i8j9k0l1m2"
branch_labels = None
depends_on = None


PERMISSIONS = [
    ("discounts:create", "Create discounts and promotions"),
    ("discounts:read", "View discounts and promotions"),
    ("discounts:update", "Update discounts and promotions"),
    ("discounts:delete", "Delete discounts and promotions"),
    ("coupons:create", "Create coupons"),
    ("coupons:read", "View coupons"),
    ("coupons:update", "Update coupons"),
    ("coupons:delete", "Delete coupons"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for name, description in PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, name, description) "
                "VALUES (gen_random_uuid(), :name, :description) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description"
            ),
            {"name": name, "description": description},
        )


def downgrade() -> None:
    names = [p[0] for p in PERMISSIONS]
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM permissions WHERE name IN :names"),
        {"names": tuple(names)},
    )
