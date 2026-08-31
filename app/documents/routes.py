from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, PaginatedResponse, ok, paginated
from app.auth.schemas.schema import DocumentCreate, DocumentStatusUpdate
from app.auth.schemas.responses import (
    DocumentConverted,
    DocumentCreated,
    DocumentDetail,
    DocumentListItem,
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


@router.post(
    "",
    status_code=201,
    response_model=DataResponse[DocumentCreated],
    dependencies=[Depends(require_permission("documents:create"))],
)
async def create_document_endpoint(payload: DocumentCreate, ctx: TenantDep):
    return ok(
        await create_document(
            tenant_id=ctx.user.business_id,
            actor_id=ctx.user.user_id,
            doc_type=payload.doc_type,
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
    page: int = 1,
    page_size: int = 50,
):
    result = await list_documents(
        tenant_id=ctx.user.business_id,
        doc_type=doc_type,
        status=status,
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
    return ok(
        await update_document_status(
            tenant_id=ctx.user.business_id,
            document_id=doc_id,
            actor_id=ctx.user.user_id,
            new_status=payload.status,
        )
    )


@router.post(
    "/{doc_id}/convert-to-sale",
    status_code=201,
    response_model=DataResponse[DocumentConverted],
    dependencies=[Depends(require_permission("documents:create"))],
)
async def convert_to_sale_endpoint(doc_id: str, ctx: TenantDep):
    return ok(
        await convert_document_to_sale(
            tenant_id=ctx.user.business_id,
            document_id=doc_id,
            cashier_id=ctx.user.user_id,
            actor_id=ctx.user.user_id,
        )
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

    from app.common.pdf_service import (
        generate_invoice_pdf,
        generate_receipt_pdf,
        generate_quote_pdf,
    )

    doc_type = doc.get("doc_type", "invoice")
    doc_number = doc.get("doc_number", doc_id)
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

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{doc_type}_{doc_number}.pdf"'},
    )
