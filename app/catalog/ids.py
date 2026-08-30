import secrets


def new_product_public_id() -> str:
    """Create a short, URL-safe product identifier for QR codes.

    Internal UUIDs stay private. Public IDs are stable enough to print on QR labels
    and safe to expose in customer/staff scan URLs.
    """

    return f"prd_{secrets.token_urlsafe(8).replace('-', '').replace('_', '')[:11]}"
