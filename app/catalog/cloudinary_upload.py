from __future__ import annotations

import logging

import cloudinary
import cloudinary.api
import cloudinary.uploader

from app.common.settings import get_common_settings

logger = logging.getLogger("storeflow.catalog.cloudinary")

_configured = False


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    s = get_common_settings()
    if s.cloudinary_cloud_name and s.cloudinary_api_key and s.cloudinary_api_secret:
        cloudinary.config(
            cloud_name=s.cloudinary_cloud_name,
            api_key=s.cloudinary_api_key,
            api_secret=s.cloudinary_api_secret,
            secure=True,
        )
        _configured = True
    else:
        logger.warning("Cloudinary not configured — QR uploads will be skipped")


def qr_public_id(*, tenant_id: str, product_id: str) -> str:
    return f"storeflow/{tenant_id}/products/{product_id}_qr"


def upload_qr_png(*, tenant_id: str, product_id: str, png_bytes: bytes) -> dict:
    _ensure_configured()
    if not _configured:
        return {"url": "", "public_id": ""}

    public_id = qr_public_id(tenant_id=tenant_id, product_id=product_id)
    result = cloudinary.uploader.upload(
        png_bytes,
        public_id=public_id,
        resource_type="image",
        format="png",
        overwrite=True,
    )
    return {"url": str(result.get("secure_url", "")), "public_id": public_id}


def delete_qr(public_id: str) -> bool:
    _ensure_configured()
    if not _configured:
        return False
    try:
        cloudinary.api.delete_resources([public_id], resource_type="image")
        return True
    except Exception:
        logger.exception("Failed to delete Cloudinary asset %s", public_id)
        return False
