"""Add foreign keys to plan_features and transaction_fee_tiers.

plan_id on plan_features and transaction_fee_tiers must reference
subscription_plans.id so SQLAlchemy relationships can join.

Revision ID: g8h9i0j1k2l3
Revises: f7g8h9i0j1k2
Create Date: 2026-11-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "g8h9i0j1k2l3"
down_revision: Union[str, None] = "f7g8h9i0j1k2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            ALTER TABLE plan_features
            ADD CONSTRAINT fk_plan_features_plan_id
            FOREIGN KEY (plan_id) REFERENCES subscription_plans(id) ON DELETE CASCADE
            """
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE transaction_fee_tiers
            ADD CONSTRAINT fk_fee_tiers_plan_id
            FOREIGN KEY (plan_id) REFERENCES subscription_plans(id) ON DELETE CASCADE
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            ALTER TABLE plan_features DROP CONSTRAINT IF EXISTS fk_plan_features_plan_id
            """
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE transaction_fee_tiers
            DROP CONSTRAINT IF EXISTS fk_fee_tiers_plan_id
            """
        )
    )
