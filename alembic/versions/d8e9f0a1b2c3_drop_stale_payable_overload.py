"""drop the superseded fn_create_accounts_payable overload

c7d8e9f0a1b2 added p_vendor_id to the function but its DROP listed the
ten-argument signature, so the previous nine-argument version survived as a
second overload. Postgres resolves our named-argument call to the ten-argument
one, but leaving two functions that differ only by an optional parameter is how
the next caller ends up posting a payable without the vendor link.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""

from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "fn_create_accounts_payable(uuid,varchar,varchar,numeric,text,text,uuid,varchar,uuid)"
    )


def downgrade() -> None:
    # The nine-argument body is superseded and is not recreated: recreate the
    # ten-argument function instead of reintroducing the ambiguity.
    raise NotImplementedError(
        "reverting would reintroduce the ambiguous overload; redeploy instead"
    )