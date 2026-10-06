"""lift the owner role to the top rank

Owner was seeded with rank 1 while every owner-only gate compares against
ROLE_RANKS["owner"] == 80. TenantData.min_role therefore evaluated 1 >= 80 and
refused the owner, so owner-only paths (including the supervisor-approval
bypass on store corrections) sent the owner through the PIN flow meant for
everyone below them.

The rank is also a ceiling: anything at or above 80 satisfies an owner-only
gate. Bring existing owner rows up to the canonical rank and leave any tenant
that already ranks its owner higher alone, so a deliberate override survives.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""

from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None

OWNER_RANK = 80


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE roles
           SET rank = {OWNER_RANK}
         WHERE name = 'owner'
           AND rank IS DISTINCT FROM {OWNER_RANK}
           AND rank < {OWNER_RANK}
        """
    )


def downgrade() -> None:
    # The previous rank (1) is the bug this migration fixes. Guessing which
    # tenants were seeded that way versus deliberately set is not possible, so
    # downgrading would either re-break them or change ranks nobody asked to
    # change.
    raise NotImplementedError(
        "rank 1 is the value this migration corrects; roll forward instead"
    )