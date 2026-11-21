"""Billing event handlers.

Register one fee per completed sale (sale_confirmed) and append reversal
entries when a sale is voided/returned. De-duplication is enforced by the
unique (tenant_id, sale_id) constraint on billable_transactions.
"""

import logging
from decimal import Decimal
from uuid import UUID

from app.billing.fees import (
    charge_document_fee,
    money,
    register_billable_sale,
    reverse_sale_fee,
)
from app.common.events.envelope import EventEnvelope

logger = logging.getLogger("storeflow.billing.handlers")


def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value))
    except Exception:
        return None


async def _fee_base_from_payload(payload: dict) -> Decimal:
    # Fee base is pre-tax, post-discount (subtotal - discount), matching
    # app.platform.fee_calculator.sale_fee_base. Fall back to total - tax.
    subtotal = payload.get("subtotal")
    discount = payload.get("discount") or 0
    total = payload.get("total")
    tax = payload.get("tax") or 0
    if subtotal is not None:
        return money(max(Decimal(0), money(subtotal) - money(discount)))
    return money(max(Decimal(0), money(total) - money(tax)))


async def handle_sale_confirmed(event: EventEnvelope, session) -> None:
    """Register the billable fee when a sale is confirmed/completed."""
    payload = event.payload or {}
    tenant_id = _uuid(payload.get("tenant_id") or event.tenant_id)
    sale_id = _uuid(payload.get("sale_id") or payload.get("id"))
    if not tenant_id or not sale_id:
        return
    fee_base = await _fee_base_from_payload(payload)
    branch_id = _uuid(payload.get("store_id") or payload.get("branch_id"))
    actor = _uuid(payload.get("actor_id") or payload.get("cashier_id"))
    try:
        bt = await register_billable_sale(
            session,
            tenant_id=tenant_id,
            sale_id=sale_id,
            branch_id=branch_id,
            fee_base=fee_base,
            completed_at=event.occurred_at,
            actor_id=actor,
        )
        if bt:
            logger.info("Registered billable fee for sale %s: %s", sale_id, bt.transaction_fee)
    except Exception:
        logger.exception("Failed to register billable fee for sale %s", sale_id)
        raise


async def handle_sale_reversed(event: EventEnvelope, session) -> None:
    """Append a fee reversal entry when a sale is voided or returned."""
    payload = event.payload or {}
    tenant_id = _uuid(payload.get("tenant_id") or event.tenant_id)
    sale_id = _uuid(payload.get("sale_id") or payload.get("id"))
    if not tenant_id or not sale_id:
        return
    actor = _uuid(payload.get("actor_id") or payload.get("voided_by"))
    reason = "Sale voided" if "voided" in (event.event_type or "") else "Sale returned"
    retained = payload.get("refund_amount")
    retained_amount = None
    if retained is not None:
        # retained value = total - refunded
        total = money(payload.get("total") or 0)
        retained_amount = money(max(Decimal(0), total - money(retained)))
    try:
        await reverse_sale_fee(
            session,
            tenant_id=tenant_id,
            sale_id=sale_id,
            actor_id=actor,
            reason=reason,
            retained_amount=retained_amount,
        )
    except Exception:
        logger.exception("Failed to reverse fee for sale %s", sale_id)
        raise


async def handle_document_status_changed(event: EventEnvelope, session) -> None:
    """Charge a plan-based transaction fee when a document leaves draft status.

    Uses the same plan/version/tier fee bands as sales — resolved from the
    document total via ``resolve_fee``. Settlement follows the
    wallet-deduction + debt-accrual pattern. If the tenant cannot cover the
    fee and accruing debt would exceed the cap, the status change is rejected
    (402) — consistent with sale fee blocking.

    Event payload:
        - document_id, doc_type, old_status, new_status, tenant_id,
          actor_id, total (document total as string/number)
    """
    payload = event.payload or {}
    tenant_id = _uuid(payload.get("tenant_id") or event.tenant_id)
    document_id = _uuid(payload.get("document_id"))
    doc_type = payload.get("doc_type")
    new_status = payload.get("new_status")
    old_status = payload.get("old_status")
    actor = _uuid(payload.get("actor_id"))
    document_total = money(payload.get("total") or 0)

    if not tenant_id or not document_id or not doc_type or not new_status or not old_status:
        return

    try:
        charged = await charge_document_fee(
            session,
            tenant_id=tenant_id,
            document_id=document_id,
            doc_type=doc_type,
            new_status=new_status,
            old_status=old_status,
            document_total=document_total,
            actor_id=actor,
        )
        if charged:
            logger.info(
                "Charged document fee for document %s (%s): %s -> %s",
                document_id,
                doc_type,
                old_status,
                new_status,
            )
    except ValueError as exc:
        if str(exc) == "fee_balance_exceeded":
            logger.warning(
                "Document fee blocked for document %s (%s): debt cap reached",
                document_id,
                doc_type,
            )
        raise

