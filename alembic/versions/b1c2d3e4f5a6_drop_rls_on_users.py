"""Drop row level security from the auth-identity tables.

The tenant_isolation policy on users makes credential login impossible
under a non-bypass role: login looks a user up by email on an anonymous
request, before any tenant is known, so app.business_id is unset and the
USING clause filters out every row. Registration has the same problem in
reverse. auth_audit_logs is written on those same anonymous paths (failed
logins, unknown emails, TOTP attempts), so the same policy blocks the audit
trail itself. User rows are already scoped by tenant_id in application
queries, and the other 38 tenant tables keep their RLS policies, so this
removes just these two from the policy set.

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e1f2
"""

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


POLICY_SQL = """
CREATE POLICY tenant_isolation ON {table}
    USING (tenant_id::text = current_setting('app.business_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.business_id', true));
"""


def upgrade() -> None:
    for table in ("users", "auth_audit_logs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in ("users", "auth_audit_logs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(POLICY_SQL.format(table=table))

