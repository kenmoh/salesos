"""Flutterwave payment gateway integration service.

This module provides functions for interacting with the Flutterwave payment API,
including payment link creation, transaction verification, bank transfers,
subaccount management, and webhook signature verification.

All functions use a shared async HTTP client with the Flutterwave API credentials
configured from application settings.

Typical usage:
    from app.common import flutterwave_service

    # Create a payment link for a customer
    result = await flutterwave_service.create_payment_link(
        amount=Decimal("5000"),
        tx_ref="SF-ABC123",
        customer_email="customer@example.com",
    )
    payment_url = result["link"]
"""

import hashlib
import hmac
from decimal import Decimal

import httpx

from app.core.config import settings

PLATFORM_FEE_PCT = Decimal("1.5")

_shared_client: httpx.AsyncClient | None = None


class FlutterwaveError(Exception):
    """Custom exception for Flutterwave API errors.

    Attributes:
        message: Human-readable error description.
        code: Error code for programmatic handling (default: "flutterwave_error").
    """

    def __init__(self, message: str, code: str = "flutterwave_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _client() -> httpx.AsyncClient:
    """Create or return the shared async HTTP client for Flutterwave API calls.

    Returns:
        A configured httpx.AsyncClient instance with Flutterwave credentials.
    """
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(
            base_url=settings.flutterwave_base_url,
            headers={
                "Authorization": f"Bearer {settings.flutterwave_secret_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
    return _shared_client


def _flw_amount(naira: Decimal) -> int:
    """Convert a Decimal amount to integer kobo (Flutterwave expects kobo).

    Args:
        naira: Amount in Nigerian Naira.

    Returns:
        Amount in kobo (integer).
    """
    return int(naira)


async def create_payment_link(
    *,
    amount: Decimal,
    tx_ref: str,
    customer_email: str,
    customer_name: str | None = None,
    callback_url: str | None = None,
    payment_options: str | None = None,
    title: str = "StoreFlow Payment",
    meta: dict | None = None,
    subaccounts: list[dict] | None = None,
) -> dict:
    """Create a hosted payment page on Flutterwave.

    Generates a unique payment link that customers can use to complete
    their payment via card, bank transfer, or other supported methods.

    Args:
        amount: Payment amount in Nigerian Naira.
        tx_ref: Unique transaction reference for tracking.
        customer_email: Email address of the paying customer.
        customer_name: Optional full name of the customer.
        callback_url: URL to redirect after payment (default: StoreFlow callback).
        payment_options: Comma-separated payment methods (e.g., "card,banktransfer").
        title: Display title for the payment page.
        meta: Additional metadata to attach to the transaction.
        subaccounts: List of subaccount configurations for payment splitting.
            Each dict should contain:
            - "id": Subaccount code (e.g., "SUB_xxx")
            - "transaction_charge_type": "percentage" | "flat" | "flat_subaccount"
            - "transaction_charge": Charge amount (decimal for %, integer for flat)

    Returns:
        Dict with keys:
            - "link": URL to the hosted payment page
            - "tx_ref": The transaction reference used

    Raises:
        FlutterwaveError: If the API request fails or returns an error.
    """
    payload = {
        "tx_ref": tx_ref,
        "amount": _flw_amount(amount),
        "currency": "NGN",
        "redirect_url": callback_url or "https://app.storeflow.ng/payment/callback",
        "customer": {
            "email": customer_email,
            **({"name": customer_name} if customer_name else {}),
        },
        "customizations": {"title": title},
        "configuration": {"session_duration": 30},
    }
    if payment_options:
        payload["payment_options"] = payment_options
    if meta:
        payload["meta"] = meta
    if subaccounts:
        payload["subaccounts"] = subaccounts

    async with _client() as client:
        resp = await client.post("/payments", json=payload)
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Payment link creation failed: {data.get('message')}")

    return {"link": data["data"]["link"], "tx_ref": tx_ref}


async def verify_transaction(*, tx_ref: str) -> dict:
    """Verify a transaction by its reference identifier.

    Checks the status of a payment with Flutterwave to confirm
    whether the transaction was successful.

    Args:
        tx_ref: The unique transaction reference to verify.

    Returns:
        Dict containing full transaction details from Flutterwave,
        including amount, status, customer information, and timestamps.

    Raises:
        FlutterwaveError: If verification fails or transaction was not successful.
    """
    async with _client() as client:
        resp = await client.get(f"/transactions/verify_by_reference?tx_ref={tx_ref}")
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Transaction verification failed: {data.get('message')}")

    tx_data = data["data"]
    if tx_data.get("status") != "successful":
        raise FlutterwaveError(f"Transaction not successful: {tx_data.get('status')}")

    return tx_data


async def initiate_transfer(
    *,
    amount: Decimal,
    bank_code: str,
    account_number: str,
    narration: str,
    reference: str,
    currency: str = "NGN",
    beneficiary_name: str | None = None,
    callback_url: str | None = None,
) -> dict:
    """Initiate a bank transfer to a beneficiary account.

    Sends money from the platform's Flutterwave balance to a specified
    bank account. Used for settlements, refunds, and payouts.

    Args:
        amount: Transfer amount in Nigerian Naira.
        bank_code: CBN bank code (e.g., "044" for Access Bank).
        account_number: 10-digit beneficiary bank account number.
        narration: Transfer description/narration.
        reference: Unique reference for tracking this transfer.
        currency: Currency code (default: "NGN").
        beneficiary_name: Optional name of the beneficiary for verification.
        callback_url: Optional URL for transfer status callback.

    Returns:
        Dict containing:
            - "transfer_id": Flutterwave transfer identifier
            - "reference": The transfer reference
            - "status": Current transfer status
            - "amount": Transfer amount
            - "fee": Flutterwave processing fee
            - "bank_name": Beneficiary bank name
            - "account_number": Beneficiary account number
            - "full_name": Account holder name

    Raises:
        FlutterwaveError: If the transfer initiation fails.
    """
    payload = {
        "account_bank": bank_code,
        "account_number": account_number,
        "amount": _flw_amount(amount),
        "currency": currency,
        "narration": narration,
        "reference": reference,
    }
    if beneficiary_name:
        payload["beneficiary_name"] = beneficiary_name
    if callback_url:
        payload["callback_url"] = callback_url

    async with _client() as client:
        resp = await client.post("/transfers", json=payload)
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Transfer initiation failed: {data.get('message')}")

    tx = data["data"]
    return {
        "transfer_id": tx["id"],
        "reference": tx["reference"],
        "status": tx["status"],
        "amount": tx["amount"],
        "fee": tx.get("fee", 0),
        "bank_name": tx.get("bank_name", ""),
        "account_number": tx.get("account_number", ""),
        "full_name": tx.get("full_name", ""),
    }


async def verify_transfer(*, transfer_id: int) -> dict:
    """Verify the status of a bank transfer.

    Args:
        transfer_id: The Flutterwave transfer identifier.

    Returns:
        Dict containing transfer details and current status.

    Raises:
        FlutterwaveError: If the verification request fails.
    """
    async with _client() as client:
        resp = await client.get(f"/transfers/{transfer_id}")
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Transfer verification failed: {data.get('message')}")

    return data["data"]


async def initiate_bank_transfer_charge(
    *,
    amount: Decimal,
    email: str,
    tx_ref: str,
    currency: str = "NGN",
    fullname: str | None = None,
    narration: str | None = None,
    bank_code: str | None = None,
    meta: dict | None = None,
    subaccounts: list[dict] | None = None,
) -> dict:
    """Create a bank transfer charge for customer payment.

    Generates a virtual bank account that customers can transfer to.
    Flutterwave monitors the account and notifies when payment is received.

    Args:
        amount: Payment amount in Nigerian Naira.
        email: Customer's email address for notifications.
        tx_ref: Unique transaction reference for tracking.
        currency: Currency code (default: "NGN").
        fullname: Customer's full name.
        narration: Payment description.
        bank_code: Optional specific bank code for virtual account.
        meta: Additional metadata to attach to the transaction.
        subaccounts: List of subaccount configurations for payment splitting.

    Returns:
        Dict containing:
            - "account_number": Virtual bank account number
            - "bank_name": Bank name for the virtual account
            - "amount": Payment amount
            - "tx_ref": Transaction reference
            - "instructions": Payment instructions for customer

    Raises:
        FlutterwaveError: If the charge creation fails.
    """
    payload = {
        "amount": _flw_amount(amount),
        "email": email,
        "currency": currency,
        "tx_ref": tx_ref,
    }
    if fullname:
        payload["fullname"] = fullname
    if narration:
        payload["narration"] = narration
    if bank_code:
        payload["bank_code"] = bank_code
    if meta:
        payload["meta"] = meta
    if subaccounts:
        payload["subaccounts"] = subaccounts

    async with _client() as client:
        resp = await client.post("/charges?type=bank_transfer", json=payload)
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Bank transfer charge failed: {data.get('message')}")

    charge_data = data["data"]
    return {
        "account_number": charge_data.get("account_number", ""),
        "bank_name": charge_data.get("bank_name", ""),
        "amount": charge_data.get("amount", amount),
        "tx_ref": tx_ref,
        "instructions": charge_data.get("instructions", {}),
    }


async def create_subaccount(
    *,
    account_bank: str,
    account_number: str,
    business_name: str,
    country: str = "NG",
    split_value: float = 0.5,
    split_type: str = "percentage",
    business_mobile: str,
    business_email: str | None = None,
    business_contact: str | None = None,
) -> dict:
    """Create a new subaccount on Flutterwave for payment splitting.

    Subaccounts allow automatic splitting of payments between the platform
    and tenant businesses. Each tenant gets their own subaccount.

    Args:
        account_bank: Bank code (e.g., "044" for Access Bank).
        account_number: 10-digit bank account number.
        business_name: Name of the business/tenant.
        country: Country code (default: "NG" for Nigeria).
        split_value: Commission amount (percentage or flat, default: 0.5).
        split_type: "percentage" or "flat" (default: "percentage").
        business_mobile: Primary business contact phone number.
        business_email: Optional business email address.
        business_contact: Optional contact person name.

    Returns:
        Dict containing subaccount details including:
            - "id": Flutterwave internal identifier
            - "subaccount_id": Unique subaccount code (e.g., "SUB_xxx")
            - "account_number": Bank account number
            - "account_bank": Bank code
            - "bank_name": Bank name
            - "full_name": Account holder name
            - "split_type": Commission type
            - "split_value": Commission amount

    Raises:
        FlutterwaveError: If subaccount creation fails.
    """
    payload = {
        "account_bank": account_bank,
        "account_number": account_number,
        "business_name": business_name,
        "country": country,
        "split_value": split_value,
        "split_type": split_type,
        "business_mobile": business_mobile,
    }
    if business_email:
        payload["business_email"] = business_email
    if business_contact:
        payload["business_contact"] = business_contact

    async with _client() as client:
        resp = await client.post("/subaccounts", json=payload)
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Subaccount creation failed: {data.get('message')}")

    sub = data["data"]
    return {
        "id": sub["id"],
        "subaccount_id": sub["subaccount_id"],
        "account_number": sub["account_number"],
        "account_bank": sub["account_bank"],
        "bank_name": sub.get("bank_name", ""),
        "full_name": sub.get("full_name", ""),
        "split_type": sub.get("split_type", ""),
        "split_value": sub.get("split_value", 0),
    }


async def fetch_subaccount(*, subaccount_id: str) -> dict:
    """Fetch details of a specific subaccount from Flutterwave.

    Args:
        subaccount_id: The unique subaccount identifier (e.g., "SUB_xxx").

    Returns:
        Dict containing full subaccount details including business information.

    Raises:
        FlutterwaveError: If the fetch request fails.
    """
    async with _client() as client:
        resp = await client.get(f"/subaccounts/{subaccount_id}")
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Subaccount fetch failed: {data.get('message')}")

    sub = data["data"]
    return {
        "id": sub["id"],
        "subaccount_id": sub["subaccount_id"],
        "account_number": sub["account_number"],
        "account_bank": sub["account_bank"],
        "bank_name": sub.get("bank_name", ""),
        "full_name": sub.get("full_name", ""),
        "split_type": sub.get("split_type", ""),
        "split_value": sub.get("split_value", 0),
        "business_name": sub.get("business_name", ""),
        "business_email": sub.get("business_email", ""),
        "business_mobile": sub.get("business_mobile", ""),
    }


async def list_subaccounts() -> list:
    """List all subaccounts configured on the Flutterwave account.

    Returns:
        List of dicts, each containing subaccount details.
        Returns empty list if the request fails.
    """
    async with _client() as client:
        resp = await client.get("/subaccounts")
        data = resp.json()

    if data.get("status") != "success":
        return []

    return [
        {
            "id": sub["id"],
            "subaccount_id": sub["subaccount_id"],
            "account_number": sub["account_number"],
            "account_bank": sub["account_bank"],
            "bank_name": sub.get("bank_name", ""),
            "full_name": sub.get("full_name", ""),
            "split_type": sub.get("split_type", ""),
            "split_value": sub.get("split_value", 0),
            "business_name": sub.get("business_name", ""),
        }
        for sub in data.get("data", [])
    ]


async def update_subaccount(
    *,
    subaccount_id: str,
    account_bank: str | None = None,
    account_number: str | None = None,
    business_name: str | None = None,
    business_mobile: str | None = None,
    business_email: str | None = None,
    split_value: float | None = None,
    split_type: str | None = None,
) -> dict:
    """Update an existing subaccount on Flutterwave.

    Only provided fields (non-None) will be updated.

    Args:
        subaccount_id: The unique subaccount identifier to update.
        account_bank: New bank code.
        account_number: New bank account number.
        business_name: New business name.
        business_mobile: New contact phone number.
        business_email: New business email.
        split_value: New commission amount.
        split_type: New commission type ("percentage" or "flat").

    Returns:
        Dict containing updated subaccount details.

    Raises:
        FlutterwaveError: If the update fails.
    """
    payload = {}
    if account_bank is not None:
        payload["account_bank"] = account_bank
    if account_number is not None:
        payload["account_number"] = account_number
    if business_name is not None:
        payload["business_name"] = business_name
    if business_mobile is not None:
        payload["business_mobile"] = business_mobile
    if business_email is not None:
        payload["business_email"] = business_email
    if split_value is not None:
        payload["split_value"] = split_value
    if split_type is not None:
        payload["split_type"] = split_type

    async with _client() as client:
        resp = await client.patch(f"/subaccounts/{subaccount_id}", json=payload)
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Subaccount update failed: {data.get('message')}")

    sub = data["data"]
    return {
        "id": sub["id"],
        "subaccount_id": sub["subaccount_id"],
        "account_number": sub["account_number"],
        "account_bank": sub["account_bank"],
        "bank_name": sub.get("bank_name", ""),
        "full_name": sub.get("full_name", ""),
        "split_type": sub.get("split_type", ""),
        "split_value": sub.get("split_value", 0),
    }


async def delete_subaccount(*, subaccount_id: str) -> dict:
    """Delete a subaccount from Flutterwave.

    Args:
        subaccount_id: The unique subaccount identifier to delete.

    Returns:
        Dict with deletion status confirmation.

    Raises:
        FlutterwaveError: If the deletion fails.
    """
    async with _client() as client:
        resp = await client.delete(f"/subaccounts/{subaccount_id}")
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Subaccount deletion failed: {data.get('message')}")

    return {"subaccount_id": subaccount_id, "status": "deleted"}


async def list_banks(country: str = "NG") -> list:
    """List all banks supported by Flutterwave in a country.

    Args:
        country: Country code (default: "NG" for Nigeria).

    Returns:
        List of dicts containing bank codes and names.
    """
    async with _client() as client:
        resp = await client.get("/banks", params={"country": country})
        data = resp.json()

    if data.get("status") != "success":
        return []
    return data.get("data", [])


async def resolve_account(*, account_number: str, bank_code: str) -> dict:
    """Resolve a bank account to verify the account holder's name.

    Args:
        account_number: 10-digit bank account number.
        bank_code: Bank code (e.g., "044" for Access Bank).

    Returns:
        Dict containing account resolution details.

    Raises:
        FlutterwaveError: If resolution fails.
    """
    async with _client() as client:
        resp = await client.post(
            "/accounts/resolve",
            json={"account_number": account_number, "account_bank": bank_code},
        )
        data = resp.json()

    if data.get("status") != "success":
        raise FlutterwaveError(f"Account resolution failed: {data.get('message')}")

    return data["data"]


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify the authenticity of a Flutterwave webhook signature.

    Uses HMAC-SHA256 to verify that the webhook was sent by Flutterwave
    and not tampered with in transit.

    Args:
        body: Raw request body bytes from the webhook.
        signature: The signature hash from the request headers.

    Returns:
        True if signature is valid, False otherwise.
    """
    expected = hmac.new(
        settings.flutterwave_secret_hash.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
