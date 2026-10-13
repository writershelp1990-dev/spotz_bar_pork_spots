"""Template context processors for the sales app."""
from __future__ import annotations


def receiving_badge(request):
    """Provide `pending_receipt_count` for the sidebar Stock link.

    Directors see the total count of PENDING receipts across all points.
    Attendants see the count of their own PENDING receipts only.
    Anonymous users get 0.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"pending_receipt_count": 0}

    from .models import StockReceipt

    qs = StockReceipt.objects.filter(status=StockReceipt.Status.PENDING)

    if getattr(user, "is_director", False):
        return {"pending_receipt_count": qs.count()}

    if not getattr(user, "is_active_staff_user", False):
        return {"pending_receipt_count": 0}

    # Attendant: their own pending receipts only.
    return {"pending_receipt_count": qs.filter(received_by=user).count()}