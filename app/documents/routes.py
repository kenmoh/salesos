from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from uuid import UUID

from app.core.dependencies import DbTenantDep, TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from app.auth.schemas.schema import DocumentCreate, DocumentStatusUpdate
from app.auth.schemas.responses import (
    DocumentConverted,
    DocumentCreated,
    DocumentDetail,
    DocumentListItem,
    DocumentPdf,
    DocumentStatusUpdated,
)
from app.common.bridge import (
    create_document,
    convert_document_to_sale,
    get_document_by_id,
    list_documents,
    update_document_status,
)

router = APIRouter(prefix="/documents", tags=["Documents"])


async def _resolve_document_store(ctx, requested: UUID | None) -> str | None:
    """Decide which store a new document belongs to.

    A document drives accounting: it becomes an AR record, a payable or a
    journal, all of which are attributed to a store. So the store is not a
    free-text choice, and it is not the client's to pick.

    An owner picks, because they are responsible for the whole business. Anyone
    else is bound to the store on their own account — a cashier must not be able
    to book a sale into a store they do not work in, and relying on the client
    to hide the picker would leave that one request away from happening.

    A non-owner naming a different store is refused rather than quietly
    corrected: silently writing to one store what the caller asked to file
    against another is worse than telling them no.
    """
    from app.identity.repository import get_user_by_id

    user = await get_user_by_id(ctx.session, UUID(ctx.user.user_id))
    own_store_id = str(user.store_id) if user and user.store_id else None

    if await ctx.user.min_role(session=ctx.session, role="owner"):
        # The owner may also file a business-wide document, which has no store.
        return str(requested) if requested else None

    if requested and own_store_id and str(requested) != own_store_id:
        raise HTTPException(
            status_code=403,
            detail="You can only create documents for your own store",
        )
    return own_store_id


@router.post(
    "",
    status_code=201,
    response_model=DataResponse[DocumentCreated],
    dependencies=[Depends(require_permission("documents:create"))],
)
async def create_document_endpoint(payload: DocumentCreate, ctx: DbTenantDep):
    store_id = await _resolve_document_store(ctx, payload.store_id)
    try:
        return await _create_document(payload, ctx, store_id)
    except ValueError as exc:
        if str(exc) == "fee_balance_exceeded":
            # The tenant's unpaid platform fees have reached the limit. Same
            # answer as opening a cart, and it says why rather than surfacing as
            # a server fault.
            raise HTTPException(
                status_code=402,
                detail=(
                    "Fee balance exceeded. Please clear outstanding fees before "
                    "creating a document."
                ),
            )
        raise


async def _create_document(payload: DocumentCreate, ctx, store_id: str | None):
    return ok(
        await create_document(
            tenant_id=ctx.user.business_id,
            actor_id=ctx.user.user_id,
            doc_type=payload.doc_type,
            customer_id=str(payload.customer_id) if payload.customer_id else None,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
            customer_phone=payload.customer_phone,
            customer_address=payload.customer_addr,
            due_date=payload.due_date,
            notes=payload.notes,
            terms=payload.terms,
            items=[
                {
                    "product_id": str(i.product_id) if i.product_id else None,
                    "description": i.description,
                    "qty": float(i.qty),
                    "unit_price": float(i.unit_price),
                    "discount_pct": float(i.discount_pct),
                    "tax_rate": float(i.tax_rate) if i.tax_rate else None,
                }
                for i in payload.items
            ],
            linked_sale_id=str(payload.sale_id) if payload.sale_id else None,
            store_id=store_id,
        )
    )


@router.get(
    "",
    response_model=PaginatedResponse[DocumentListItem],
    dependencies=[Depends(require_permission("documents:read"))],
)
async def list_documents_endpoint(
    ctx: TenantDep,
    doc_type: str | None = None,
    status: str | None = None,
    customer_id: str | None = Query(None, description="Filter to one customer"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if customer_id:
        try:
            UUID(customer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="customer_id must be a valid UUID")
    result = await list_documents(
        tenant_id=ctx.user.business_id,
        doc_type=doc_type,
        status=status,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )
    return paginated(
        result["items"], total=result["total"], page=result["page"], page_size=result["page_size"]
    )


@router.get(
    "/{doc_id}",
    response_model=DataResponse[DocumentDetail],
    dependencies=[Depends(require_permission("documents:read"))],
)
async def get_document_endpoint(doc_id: str, ctx: TenantDep):
    doc = await get_document_by_id(tenant_id=ctx.user.business_id, document_id=doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return ok(doc)


@router.patch(
    "/{doc_id}/status",
    response_model=DataResponse[DocumentStatusUpdated],
    dependencies=[Depends(require_permission("documents:update"))],
)
async def update_document_status_endpoint(
    doc_id: str, payload: DocumentStatusUpdate, ctx: TenantDep
):
    try:
        return ok(
            await update_document_status(
                tenant_id=ctx.user.business_id,
                document_id=doc_id,
                actor_id=ctx.user.user_id,
                new_status=payload.status,
            )
        )
    except ValueError as exc:
        message = str(exc)
        if message == "document_not_found":
            raise HTTPException(status_code=404, detail="Document not found")
        # An invalid transition is the caller's mistake. It used to surface as a
        # 500, which told the user nothing and looked like a server fault.
        raise HTTPException(status_code=400, detail=message)


@router.post(
    "/{doc_id}/convert-to-sale",
    status_code=201,
    response_model=DataResponse[DocumentConverted],
    dependencies=[Depends(require_permission("documents:create"))],
)
async def convert_to_sale_endpoint(doc_id: str, ctx: TenantDep):
    try:
        return ok(
            await convert_document_to_sale(
                tenant_id=ctx.user.business_id,
                document_id=doc_id,
                cashier_id=ctx.user.user_id,
                actor_id=ctx.user.user_id,
            )
        )
    except ValueError as exc:
        message = str(exc)
        if message == "document_not_found":
            raise HTTPException(status_code=404, detail="Document not found")
        # A rejected conversion is the caller's situation to resolve, not a
        # server fault, so it answers 400 and says what was wrong.
        raise HTTPException(status_code=400, detail=message)


def _ensure_pdf_bytes(data) -> bytes:
    """Coerce fpdf2 output to bytes.

    Depending on the fpdf2 version, ``FPDF.output()`` returns ``str``
    (one char per byte) or ``bytes``/``bytearray``. Latin-1 maps bytes
    0-255 one-to-one, so it restores the exact PDF bytes from str.
    """
    if isinstance(data, str):
        return data.encode("latin-1")
    return bytes(data)


def _build_document_pdf(doc: dict) -> tuple[str, str, bytes]:
    """Generate PDF bytes for a document dict.

    Returns (doc_type, doc_number, pdf_bytes).
    """
    from app.common.pdf_service import (
        generate_invoice_pdf,
        generate_receipt_pdf,
        generate_quote_pdf,
    )

    doc_type = doc.get("doc_type", "invoice")
    doc_number = doc.get("doc_number", "")
    customer_name = doc.get("customer_name", "")
    items = doc.get("items", [])
    subtotal = float(doc.get("subtotal", 0))
    tax = float(doc.get("tax_total", 0))
    total = float(doc.get("grand_total", 0))
    due_date = doc.get("due_date", "")
    notes = doc.get("notes", "")
    terms = doc.get("terms", "")

    if doc_type == "receipt":
        pdf_bytes = generate_receipt_pdf(
            sale_number=doc_number,
            customer_name=customer_name,
            items=items,
            subtotal=subtotal,
            total=total,
        )
    elif doc_type == "quote":
        pdf_bytes = generate_quote_pdf(
            doc_number=doc_number,
            customer_name=customer_name,
            items=items,
            subtotal=subtotal,
            tax=tax,
            total=total,
            notes=notes,
            terms=terms,
        )
    else:
        pdf_bytes = generate_invoice_pdf(
            doc_number=doc_number,
            customer_name=customer_name,
            items=items,
            subtotal=subtotal,
            tax=tax,
            total=total,
            due_date=due_date,
            notes=notes,
            terms=terms,
        )
    return doc_type, doc_number, _ensure_pdf_bytes(pdf_bytes)


@router.get(
    "/{doc_id}/pdf",
    response_model=DataResponse[DocumentPdf],
    dependencies=[Depends(require_permission("documents:read"))],
)
async def document_pdf(doc_id: str, ctx: TenantDep):
    """Return the document PDF as base64 JSON (binary-safe for any proxy)."""
    import base64

    doc = await get_document_by_id(tenant_id=ctx.user.business_id, document_id=doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_type, doc_number, pdf_bytes = _build_document_pdf(doc)
    return ok(
        {
            "filename": f"{doc_type}_{doc_number}.pdf",
            "mime_type": "application/pdf",
            "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        }
    )


@router.get(
    "/{doc_id}/download",
    response_class=Response,
    dependencies=[Depends(require_permission("documents:read"))],
)
async def download_document(doc_id: str, ctx: TenantDep):
    doc = await get_document_by_id(tenant_id=ctx.user.business_id, document_id=doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_type, doc_number, pdf_bytes = _build_document_pdf(doc)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{doc_type}_{doc_number}.pdf"'},
    )
