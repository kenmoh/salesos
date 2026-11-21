"""Seed plan_features for public pricing plans.

The live database has active subscription plans (Free, Basic, Pro) but the
plan_features table was never populated, so the landing page "Compare plans"
table renders with no rows. This migration seeds the default features for
each plan, matching the seed data in d5e6f7g8h9i0.

Revision ID: j0k1l2m3n4o5p6
Revises: i0j1k2l3m4n5
Create Date: 2026-11-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "j0k1l2m3n4o5p6"
down_revision: Union[str, None] = "i0j1k2l3m4n5"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_FEATURES: tuple[tuple[str, str, str, str | None, bool, int], ...] = (
    # (slug, feature_key, feature_label, feature_value, is_enabled, sort_order)
    ("free", "pos", "POS terminal", "1 terminal", True, 0),
    ("free", "products", "Products", "100 products", True, 1),
    ("free", "staff", "Staff accounts", "2 users", True, 2),
    ("free", "inventory", "Basic inventory", "", True, 3),
    ("free", "customers", "Customer management", "", True, 4),
    ("free", "reports", "Basic reports", "", True, 5),
    ("free", "ai", "AI assistant", "Standard", True, 6),
    ("free", "branches", "Multi-branch", "", False, 7),
    ("basic", "pos", "POS terminals", "3 terminals", True, 0),
    ("basic", "products", "Products", "1,000 products", True, 1),
    ("basic", "staff", "Staff accounts", "10 users", True, 2),
    ("basic", "inventory", "Advanced inventory", "", True, 3),
    ("basic", "customers", "Credit customers & receivables", "", True, 4),
    ("basic", "reports", "Detailed business reports", "", True, 5),
    ("basic", "ai", "AI assistant", "Increased", True, 6),
    ("basic", "branches", "Multi-branch", "", False, 7),
    ("pro", "pos", "POS terminals", "Unlimited", True, 0),
    ("pro", "products", "Products", "10,000 products", True, 1),
    ("pro", "staff", "Staff accounts", "100 users", True, 2),
    ("pro", "inventory", "Advanced inventory", "", True, 3),
    ("pro", "customers", "Credit customers & receivables", "", True, 4),
    ("pro", "reports", "Advanced analytics & consolidated reporting", "", True, 5),
    ("pro", "ai", "AI assistant", "Highest", True, 6),
    ("pro", "branches", "Multi-branch management", "", True, 7),
)


def _values_clause() -> str:
    rows = ", ".join(
        f"('{slug}', '{key}', '{label}', {value!r}, {enabled}, {sort})"
        for slug, key, label, value, enabled, sort in _FEATURES
    )
    return rows


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            f"""
            INSERT INTO plan_features (id, plan_id, feature_key, feature_label, feature_value, is_enabled, sort_order)
            SELECT gen_random_uuid(), p.id, v.feature_key, v.feature_label, v.feature_value, v.is_enabled, v.sort_order
            FROM (VALUES {_values_clause()})
                 AS v(slug, feature_key, feature_label, feature_value, is_enabled, sort_order)
            JOIN subscription_plans p ON p.slug = v.slug
            ON CONFLICT (plan_id, feature_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            DELETE FROM plan_features pf
            USING subscription_plans p
            WHERE pf.plan_id = p.id
              AND p.slug IN ('free', 'basic', 'pro')
            """
        )
    )
