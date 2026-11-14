"""Scope the inbox to per-consumer processing.

``inbox_events`` was keyed by ``event_id`` alone, but the same event is
delivered to several consumers (sales.sale_confirmed -> sales, accounting,
notifications; payment.succeeded -> four consumers). Concurrent deliveries
raced on the single key: one consumer committed the row, every other hit a
unique violation and crashed the whole consumer runner.

Keys the table by (event_id, service_name) and backfills one marker per
consumer whose queue binds a matching routing key, so already-acked events
cannot be double-processed on a redelivery.

Revision ID: b2c3d4e5f6a7
Revises: a1a2a3a4a5a6
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa

# Mirrors app.common.messagebus.setup.SERVICE_QUEUES — kept literal so the
# migration stays valid even if the topology dict changes later.
SERVICE_QUEUES = {
    "tenancy": ["tenancy.#"],
    "identity": ["identity.#"],
    "catalog": ["catalog.#"],
    "cart": ["cart.#"],
    "sales": ["sales.#", "cart.checked_out", "payment.succeeded"],
    "inventory": [
        "inventory.#",
        "sales.sale_created",
        "sales.sale_voided",
        "catalog.product_created",
    ],
    "payments": ["payment.#", "sales.sale_created"],
    "terminals": ["terminal.#", "tenancy.tier_changed"],
    "accounting": ["accounting.#", "sales.sale_confirmed", "payment.succeeded"],
    "notifications": [
        "notification.#",
        "tenancy.tenant_created",
        "identity.user_created",
        "inventory.low_stock_detected",
        "sales.sale_confirmed",
        "payment.succeeded",
    ],
    "reporting": ["reporting.#", "sales.#", "payment.#", "tenancy.tier_changed"],
}


def _predicate(keys: list[str]) -> str:
    """SQL lambda that matches an event_type against the bound routing keys."""
    conds = []
    for key in keys:
        if key.endswith("#"):
            conds.append(f"e.event_type LIKE '{key[:-2]}%'")
        else:
            conds.append(f"e.event_type = '{key}'")
    return "(" + " OR ".join(conds) + ")"


revision = "b2c3d4e5f6a7"
down_revision = "a1a2a3a4a5a6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inbox_events_new",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("service_name", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=160), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "service_name"),
    )
    for service_name, keys in SERVICE_QUEUES.items():
        op.execute(
            f"""
            INSERT INTO inbox_events_new (event_id, event_type, processed_at, service_name)
            SELECT e.event_id, e.event_type, e.processed_at, '{service_name}'
            FROM inbox_events e
            WHERE {_predicate(keys)}
            ON CONFLICT (event_id, service_name) DO NOTHING
            """
        )
    op.drop_table("inbox_events")
    op.rename_table("inbox_events_new", "inbox_events")

    # Tax-inclusive selling price: products may opt into having their tax
    # deducted from the selling price at sale time instead of added on top.
    op.add_column(
        "products",
        sa.Column("auto_calc_tax", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("products", "auto_calc_tax")
    op.create_table(
        "inbox_events_old",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=160), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.execute(
        """
        INSERT INTO inbox_events_old (event_id, event_type, processed_at)
        SELECT event_id, event_type, processed_at
        FROM inbox_events
        WHERE service_name != 'legacy'
        ON CONFLICT (event_id) DO NOTHING
        """
    )
    op.drop_table("inbox_events")
    op.rename_table("inbox_events_old", "inbox_events")
