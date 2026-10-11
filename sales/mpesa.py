"""
M-Pesa (Safaricom Daraja) integration.

Modes
-----
- MPESA_MOCK_MODE=True: simulates a successful STK push. Used in development
  without Daraja credentials. A background thread marks the transaction as
  SUCCESS after MPESA_MOCK_DELAY_SECONDS.
- MPESA_MOCK_MODE=False + MPESA_ENVIRONMENT="sandbox": real STK push to
  Safaricom sandbox.
- MPESA_MOCK_MODE=False + MPESA_ENVIRONMENT="production": live.

Public API
----------
    normalize_phone(raw)                      -> "254712345678" or ""
    initiate_stk_push(sale, phone_number)     -> MpesaTransaction
    parse_callback_payload(payload)           -> dict
    apply_callback(mpesa_transaction, parsed) -> MpesaTransaction
"""
from __future__ import annotations

import base64
import logging
import re
import threading
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def normalize_phone(raw) -> str:
    """Convert various Kenyan phone formats to 254XXXXXXXXX form."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    if len(digits) == 9:
        return "254" + digits
    if digits.startswith("254") and len(digits) > 12:
        return digits[:12]
    return ""


def _base_url() -> str:
    if settings.MPESA_ENVIRONMENT == "production":
        return "https://api.safaricom.co.ke"
    return "https://sandbox.safaricom.co.ke"


def _timestamp() -> str:
    return timezone.localtime().strftime("%Y%m%d%H%M%S")


def _password() -> str:
    raw = f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{_timestamp()}"
    return base64.b64encode(raw.encode()).decode()


def _get_access_token() -> str:
    import requests
    url = f"{_base_url()}/oauth/v1/generate?grant_type=client_credentials"
    resp = requests.get(
        url,
        auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@transaction.atomic
def initiate_stk_push(*, sale, phone_number: str):
    """Create an MpesaTransaction row and either mock or call Daraja."""
    from .models import MpesaTransaction

    normalized = normalize_phone(phone_number)
    if not normalized:
        raise ValueError(
            "Invalid M-Pesa phone number. Use a Kenyan number like 0712345678."
        )

    mpesa = MpesaTransaction.objects.create(
        sale=sale,
        phone_number=normalized,
        amount=sale.total,
        status=MpesaTransaction.Status.PENDING,
    )

    if settings.MPESA_MOCK_MODE:
        logger.info(
            "M-Pesa MOCK: simulating STK push to %s for sale %s",
            normalized, sale.sale_number,
        )
        mpesa.raw_request = {
            "mock": True,
            "phone": normalized,
            "amount": str(sale.total),
            "sale_number": sale.sale_number,
            "shortcode": settings.MPESA_SHORTCODE,
            "transaction_type": settings.MPESA_TRANSACTION_TYPE,
        }
        mpesa.save(update_fields=["raw_request"])
        _schedule_mock_callback(mpesa.pk)
        return mpesa

    if not settings.MPESA_SHORTCODE or not settings.MPESA_PASSKEY:
        raise ValueError(
            "M-Pesa is not configured. Set MPESA_SHORTCODE and MPESA_PASSKEY "
            "in .env, or enable MPESA_MOCK_MODE."
        )

    import requests

    token = _get_access_token()
    url = f"{_base_url()}/mpesa/stkpush/v1/processrequest"
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": _password(),
        "Timestamp": _timestamp(),
        "TransactionType": settings.MPESA_TRANSACTION_TYPE,
        "Amount": int(sale.total),
        "PartyA": normalized,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": normalized,
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": sale.sale_number[:12],
        "TransactionDesc": f"Sale {sale.sale_number}"[:30],
    }

    mpesa.raw_request = payload
    mpesa.save(update_fields=["raw_request"])

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.exception("Daraja STK push failed")
        mpesa.status = MpesaTransaction.Status.FAILED
        mpesa.result_description = str(exc)[:500]
        mpesa.save(update_fields=["status", "result_description", "updated_at"])
        raise

    mpesa.merchant_request_id = data.get("MerchantRequestID", "") or ""
    mpesa.checkout_request_id = data.get("CheckoutRequestID", "") or ""
    mpesa.result_code = data.get("ResponseCode", "") or ""
    mpesa.result_description = data.get("ResponseDescription", "") or ""
    mpesa.save(update_fields=[
        "merchant_request_id", "checkout_request_id",
        "result_code", "result_description", "updated_at",
    ])
    return mpesa


def _schedule_mock_callback(mpesa_pk: int):
    """Thread that simulates Daraja's callback after a short delay."""
    def _run():
        delay = getattr(settings, "MPESA_MOCK_DELAY_SECONDS", 6)
        time.sleep(delay)

        from django.db import connection
        from .models import MpesaTransaction, Sale

        try:
            mpesa = MpesaTransaction.objects.get(pk=mpesa_pk)
        except MpesaTransaction.DoesNotExist:
            return

        if mpesa.status != MpesaTransaction.Status.PENDING:
            return

        mpesa.status = MpesaTransaction.Status.SUCCESS
        mpesa.mpesa_receipt_number = f"MOCK{int(time.time()):010d}"
        mpesa.result_code = "0"
        mpesa.result_description = (
            "The service request is processed successfully. (mock)"
        )
        mpesa.raw_callback = {"mock": True, "auto_success": True}
        mpesa.save(update_fields=[
            "status", "mpesa_receipt_number", "result_code",
            "result_description", "raw_callback", "updated_at",
        ])

        # Mirror what the real Daraja callback does: complete the sale.
        sale = mpesa.sale
        sale.payment_status = Sale.PaymentStatus.PAID
        sale.completed = True
        sale.save(update_fields=["completed", "payment_status"])

        try:
            connection.close()
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def parse_callback_payload(payload: dict) -> dict:
    """Extract fields from a Daraja callback body."""
    try:
        cb = payload["Body"]["stkCallback"]
    except (KeyError, TypeError):
        return {}

    result_code = str(cb.get("ResultCode", ""))
    metadata = {}
    for item in cb.get("CallbackMetadata", {}).get("Item", []) or []:
        if isinstance(item, dict) and "Name" in item:
            metadata[item["Name"]] = item.get("Value")

    return {
        "checkout_request_id": cb.get("CheckoutRequestID", "") or "",
        "merchant_request_id": cb.get("MerchantRequestID", "") or "",
        "result_code": result_code,
        "result_description": cb.get("ResultDesc", "") or "",
        "mpesa_receipt_number": metadata.get("MpesaReceiptNumber", "") or "",
        "amount": metadata.get("Amount"),
        "phone": metadata.get("PhoneNumber"),
    }


@transaction.atomic
def apply_callback(*, mpesa_transaction, parsed: dict):
    """Update the MpesaTransaction and its Sale based on a callback."""
    from .models import MpesaTransaction, Sale

    mpesa_transaction.result_code = parsed.get("result_code", "") or ""
    mpesa_transaction.result_description = parsed.get("result_description", "") or ""
    mpesa_transaction.mpesa_receipt_number = (
        parsed.get("mpesa_receipt_number", "") or ""
    )

    code = parsed.get("result_code", "")
    if code == "0":
        mpesa_transaction.status = MpesaTransaction.Status.SUCCESS
    elif code == "1032":
        mpesa_transaction.status = MpesaTransaction.Status.CANCELLED
    elif code == "1037":
        mpesa_transaction.status = MpesaTransaction.Status.TIMEOUT
    else:
        mpesa_transaction.status = MpesaTransaction.Status.FAILED

    mpesa_transaction.save()

    sale = mpesa_transaction.sale
    if mpesa_transaction.status == MpesaTransaction.Status.SUCCESS:
        sale.payment_status = Sale.PaymentStatus.PAID
        sale.completed = True
        sale.save(update_fields=["completed", "payment_status"])
    else:
        sale.payment_status = Sale.PaymentStatus.FAILED
        sale.save(update_fields=["payment_status"])

    return mpesa_transaction