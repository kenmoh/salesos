import base64
import io

import qrcode


def build_product_qr_url(*, base_url: str, store_id: str, product_id: str) -> str:
    """Build the full API URL that returns product data when scanned."""
    base = base_url.rstrip("/")
    return f"{base}/v1/products/{store_id}/{product_id}"


def generate_qr_png(url: str, *, box_size: int = 10, border: int = 4) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_qr_base64(url: str, *, box_size: int = 10, border: int = 4) -> str:
    png_bytes = generate_qr_png(url, box_size=box_size, border=border)
    return base64.b64encode(png_bytes).decode("ascii")


def generate_qr_svg(url: str, *, box_size: int = 10) -> str:
    import qrcode.image.svg

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgImage)
    buf = io.BytesIO()
    img.save(buf, kind="PNG")
    return buf.getvalue().decode("utf-8")
