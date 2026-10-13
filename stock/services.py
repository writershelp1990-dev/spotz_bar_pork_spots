from __future__ import annotations
from datetime import timedelta
"""Atomic stock movement services.

Every function here is @transaction.atomic and calls
sales.low_stock.evaluate_after_stock_change so low-stock alerts fire on
every balance change. Uses sales.StockLedger and sales.StockBalance as
the single source of truth — no competing ledger, no duplicate balance.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from catalog.models import Product
from sales.models import StockBalance, StockLedger
from sales.low_stock import evaluate_after_stock_change

from .models import StockTransfer
from .permissions import (
    is_director,
    user_can_act_at_point,
    user_can_transfer_out_of,
)

THREE = Decimal("0.001")


# ===========================================================================
# Helpers
# ===========================================================================
def _quantize(quantity) -> Decimal:
    try:
        q = Decimal(str(quantity)).quantize(THREE)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Quantity must be a valid number.")
    if q <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    return q


def _lock_balance_pair(source_product, source_point, dest_product, dest_point):
    """Return the two StockBalance rows, locked in stable pk order to
    prevent deadlocks between concurrent transfers.

    The two rows are (source_product, source_point) and
    (dest_product, dest_point). This is a same-product transfer in the
    current design (pork moves, product identity doesn't change), so
    source_product == dest_product == the transferred product.
    """
    # Create-or-get both, then lock in pk order
    src_balance, _ = StockBalance.objects.get_or_create(
        product=source_product, operating_point=source_point,
        defaults={"quantity": Decimal("0")},
    )
    dst_balance, _ = StockBalance.objects.get_or_create(
        product=dest_product, operating_point=dest_point,
        defaults={"quantity": Decimal("0")},
    )
    pks = sorted([src_balance.pk, dst_balance.pk])
    locked = list(
        StockBalance.objects.select_for_update()
        .filter(pk__in=pks)
        .order_by("pk")
    )
    by_pk = {b.pk: b for b in locked}
    return by_pk[src_balance.pk], by_pk[dst_balance.pk]


# ===========================================================================
# Transfer: create / approve / complete / cancel
# ===========================================================================
@transaction.atomic
def create_transfer(*, user, product, source_point, destination_point,
                    quantity, reason="", reference="",
                    transfer_type=StockTransfer.TransferType.ALLOCATION):
    """Create a PENDING StockTransfer.

    Validates: source and destination differ, user may act at the source,
    product tracks inventory, quantity is positive.
    Does NOT touch any balance — the transfer is not yet in effect.
    """
    if source_point.pk == destination_point.pk:
        raise ValidationError("Source and destination must be different.")

    if not user_can_transfer_out_of(user, source_point):
        raise PermissionDenied(
            f"You are not authorized to issue stock from {source_point.code}."
        )

    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active or not product.track_inventory:
        raise ValidationError(
            "Transfers can only be created for active, inventory-tracked products."
        )

    qty = _quantize(quantity)

    transfer_number = "TR-" + timezone.localtime(timezone.now()).strftime(
        "%Y%m%d%H%M%S"
    ) + "-" + str(StockTransfer.objects.count() + 1).zfill(4)

    transfer = StockTransfer.objects.create(
        transfer_number=transfer_number,
        product=product,
        source_point=source_point,
        destination_point=destination_point,
        quantity=qty,
        unit=product.selling_unit,
        transfer_type=transfer_type,
        reason=reason,
        reference=reference,
        requested_by=user,
        status=StockTransfer.Status.REQUESTED,
    )

    record_audit(
        user=user, action=AuditLog.Action.CREATE, obj=transfer,
        operating_point=source_point,
        reason=f"Transfer created: {source_point.code} -> {destination_point.code}",
        changes={
            "transfer_number": transfer.transfer_number,
            "product": product.name,
            "quantity": str(qty),
            "source": source_point.code,
            "destination": destination_point.code,
        },
    )
    return transfer


# ---------------------------------------------------------------------------
# Transfer state machine (recipient-acknowledgement workflow)
#
# REQUESTED -> SENT -> RECEIVED -> COMPLETED
#
# The Director is not in the critical path. The sender confirms, walks over,
# hands over the goods, and the recipient acknowledges. If the recipient does
# not acknowledge within ACK_DEADLINE, the transfer auto-completes with the
# sender's quantity and the Director is notified.
#
# Small variances (<= 100 g and <= 2%) auto-accept. Larger variances require a
# written reason and the difference is recorded as wastage at the destination.
# ---------------------------------------------------------------------------
ACK_DEADLINE = timedelta(minutes=30)
VARIANCE_AUTO_KG = Decimal("0.100")
VARIANCE_AUTO_PCT = Decimal("2.0")


def _notify_directors_of_transfer_event(transfer, message):
    """Best-effort notification to Directors. Not blocking."""
    try:
        from django.core.mail import send_mail
        from django.conf import settings
        recipients = list(
            User.objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(role="DIRECTOR"))
            .exclude(email="")
            .values_list("email", flat=True)
        )
        if recipients:
            send_mail(
                subject=f"[SPOTZ] Transfer #{transfer.pk} {transfer.status}",
                message=message,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=recipients,
                fail_silently=True,
            )
    except Exception:
        log.exception("Transfer notification email failed for #%s", transfer.pk)


@transaction.atomic
def sender_accept(*, transfer, user, quantity_sent):
    """Sender confirms the shipment. REQUESTED -> SENT."""
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != StockTransfer.Status.REQUESTED:
        raise ValidationError(
            f"Cannot accept a transfer in status {transfer.status}."
        )
    if transfer.source_point_id is None:
        raise ValidationError("Transfer has no source point.")
    if not user_can_transfer_out_of(user, transfer.source_point):
        raise PermissionDenied(
            f"You are not authorized to ship from {transfer.source_point.code}."
        )

    qty = Decimal(quantity_sent).quantize(THREE)
    if qty <= 0:
        raise ValidationError("Quantity sent must be greater than zero.")
    if qty > transfer.quantity:
        # The sender can send less than requested (partial fill), but never more.
        raise ValidationError(
            f"Cannot send more than requested. Requested: {transfer.quantity}."
        )

    transfer.quantity_sent = qty
    transfer.sent_by = user
    transfer.sent_at = timezone.now()
    transfer.acknowledgement_deadline = transfer.sent_at + ACK_DEADLINE
    transfer.status = StockTransfer.Status.SENT
    transfer.save(update_fields=[
        "quantity_sent", "sent_by", "sent_at",
        "acknowledgement_deadline", "status",
    ])

    record_audit(
        user=user, action="UPDATE", obj=transfer,
        operating_point=transfer.source_point,
        reason="Sender confirmed shipment",
        changes={
            "status": "SENT",
            "quantity_sent": str(qty),
            "deadline": transfer.acknowledgement_deadline.isoformat(),
        },
    )
    return transfer


@transaction.atomic
def sender_reject(*, transfer, user, reason):
    """Sender refuses the request. REQUESTED -> REJECTED_BY_SENDER."""
    if not reason:
        raise ValidationError("A rejection reason is required.")
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != StockTransfer.Status.REQUESTED:
        raise ValidationError(
            f"Cannot reject a transfer in status {transfer.status}."
        )
    if not user_can_transfer_out_of(user, transfer.source_point):
        raise PermissionDenied("You are not authorized for this transfer.")

    transfer.status = StockTransfer.Status.REJECTED_BY_SENDER
    transfer.sent_by = user
    transfer.sent_at = timezone.now()
    transfer.save(update_fields=["status", "sent_by", "sent_at"])

    record_audit(
        user=user, action="UPDATE", obj=transfer,
        operating_point=transfer.source_point,
        reason=f"Sender rejected: {reason}",
        changes={"status": "REJECTED_BY_SENDER", "reason": reason},
    )
    return transfer


@transaction.atomic
def recipient_acknowledge(*, transfer, user, quantity_received, condition="",
                          notes="", variance_reason=""):
    """Recipient confirms receipt. SENT -> RECEIVED -> COMPLETED.

    Applies the balance move and writes two ledger rows (TRANSFER_OUT at
    source, TRANSFER_IN at destination). If a variance exceeds the auto-
    accept threshold, requires a reason and records the difference as
    wastage at the destination.
    """
    transfer = (
        StockTransfer.objects.select_for_update()
        .select_related("product", "source_point", "destination_point", "unit")
        .get(pk=transfer.pk)
    )
    if transfer.status != StockTransfer.Status.SENT:
        raise ValidationError(
            f"Cannot acknowledge a transfer in status {transfer.status}."
        )
    if transfer.destination_point_id is None:
        raise ValidationError("Transfer has no destination point.")
    if not user_can_act_at_point(user, transfer.destination_point):
        raise PermissionDenied(
            f"You are not authorized to receive at {transfer.destination_point.code}."
        )

    qty = Decimal(quantity_received).quantize(THREE)
    if qty <= 0:
        raise ValidationError("Quantity received must be greater than zero.")
    if qty > transfer.quantity_sent:
        raise ValidationError(
            f"Quantity received ({qty}) cannot exceed quantity sent "
            f"({transfer.quantity_sent})."
        )

    sent = transfer.quantity_sent
    variance = (sent - qty).quantize(THREE)
    variance_pct = (variance / sent * Decimal("100")) if sent > 0 else Decimal("0")

    auto_ok = (variance <= VARIANCE_AUTO_KG) and (variance_pct <= VARIANCE_AUTO_PCT)
    if not auto_ok and not variance_reason:
        raise ValidationError(
            f"Variance of {variance} kg ({variance_pct:.2f}%) exceeds the "
            f"auto-accept threshold. A reason is required."
        )

    # Lock both balance rows in stable pk order
    src_balance, _ = StockBalance.objects.get_or_create(
        product=transfer.product, operating_point=transfer.source_point,
        defaults={"quantity": Decimal("0")},
    )
    dst_balance, _ = StockBalance.objects.get_or_create(
        product=transfer.product, operating_point=transfer.destination_point,
        defaults={"quantity": Decimal("0")},
    )
    pks = sorted([src_balance.pk, dst_balance.pk])
    locked = list(StockBalance.objects.select_for_update()
                  .filter(pk__in=pks).order_by("pk"))
    by_pk = {b.pk: b for b in locked}
    src_balance = by_pk[src_balance.pk]
    dst_balance = by_pk[dst_balance.pk]

    if src_balance.quantity < sent:
        raise ValidationError(
            f"Insufficient stock at {transfer.source_point.code} for "
            f"{transfer.product.name}. Available: {src_balance.quantity}; "
            f"requested: {sent}."
        )

    # Move the SENT quantity out of source, the RECEIVED quantity into dest.
    src_balance.quantity -= sent
    dst_balance.quantity += qty
    src_balance.save(update_fields=["quantity", "updated_at"])
    dst_balance.save(update_fields=["quantity", "updated_at"])

    # TRANSFER_OUT ledger row
    out_movement = StockLedger.objects.create(
        product=transfer.product,
        operating_point=transfer.source_point,
        movement_type=StockLedger.MovementType.TRANSFER_OUT,
        quantity_delta=-sent,
        balance_after=src_balance.quantity,
        reference=transfer.transfer_number,
        reason=transfer.reason or f"Transfer to {transfer.destination_point.code}",
        created_by=user,
    )
    # TRANSFER_IN ledger row (the actual received quantity)
    in_movement = StockLedger.objects.create(
        product=transfer.product,
        operating_point=transfer.destination_point,
        movement_type=StockLedger.MovementType.TRANSFER_IN,
        quantity_delta=qty,
        balance_after=dst_balance.quantity,
        reference=transfer.transfer_number,
        reason=transfer.reason or f"Transfer from {transfer.source_point.code}",
        created_by=user,
    )

    # If there was a variance beyond the auto threshold, record as wastage
    # at the destination point so the physical loss is on record.
    if variance > 0:
        StockLedger.objects.create(
            product=transfer.product,
            operating_point=transfer.destination_point,
            movement_type=StockLedger.MovementType.WASTAGE,
            quantity_delta=-variance,   # negative; already not added to dst
            balance_after=dst_balance.quantity,
            reference=transfer.transfer_number,
            reason=variance_reason or "Auto-accepted variance",
            created_by=user,
        )
        # Note: dst_balance was credited with qty, not sent, so no second
        # adjustment is needed. The wastage row exists for reconciliation.

    transfer.quantity_received = qty
    transfer.received_by = user
    transfer.received_at = timezone.now()
    transfer.variance_quantity = variance
    transfer.variance_reason = variance_reason
    transfer.status = StockTransfer.Status.COMPLETED
    transfer.completed_at = timezone.now()
    transfer.completion_method = "NORMAL"
    transfer.save(update_fields=[
        "quantity_received", "received_by", "received_at",
        "variance_quantity", "variance_reason",
        "status", "completed_at", "completion_method",
    ])

    record_audit(
        user=user, action="UPDATE", obj=transfer,
        operating_point=transfer.destination_point,
        reason="Recipient acknowledged receipt",
        changes={
            "status": "COMPLETED",
            "quantity_sent": str(sent),
            "quantity_received": str(qty),
            "variance": str(variance),
        },
    )

    evaluate_after_stock_change(
        product=transfer.product, operating_point=transfer.source_point, actor=user,
    )
    evaluate_after_stock_change(
        product=transfer.product, operating_point=transfer.destination_point, actor=user,
    )
    return transfer


@transaction.atomic
def recipient_reject(*, transfer, user, reason):
    """Recipient refuses receipt. SENT -> REJECTED_BY_RECIPIENT. No balance change."""
    if not reason:
        raise ValidationError("A rejection reason is required.")
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != StockTransfer.Status.SENT:
        raise ValidationError(
            f"Cannot reject a transfer in status {transfer.status}."
        )
    if not user_can_act_at_point(user, transfer.destination_point):
        raise PermissionDenied("You are not authorized for this transfer.")

    transfer.status = StockTransfer.Status.REJECTED_BY_RECIPIENT
    transfer.received_by = user
    transfer.received_at = timezone.now()
    transfer.variance_reason = reason
    transfer.save(update_fields=[
        "status", "received_by", "received_at", "variance_reason",
    ])

    record_audit(
        user=user, action="UPDATE", obj=transfer,
        operating_point=transfer.destination_point,
        reason=f"Recipient rejected: {reason}",
        changes={"status": "REJECTED_BY_RECIPIENT", "reason": reason},
    )
    _notify_directors_of_transfer_event(
        transfer,
        f"Recipient rejected transfer #{transfer.pk} "
        f"({transfer.product.name}, {transfer.source_point.code} -> "
        f"{transfer.destination_point.code}). Reason: {reason}",
    )
    return transfer


@transaction.atomic
def expire_unacknowledged_transfers():
    """Auto-complete transfers whose acknowledgement deadline has passed.

    Uses the sender's quantity as the received quantity. Records an audit
    entry and notifies Directors. Called by the reminder sweep.
    """
    now = timezone.now()
    stale = (StockTransfer.objects
             .select_for_update(skip_locked=True)
             .filter(status=StockTransfer.Status.SENT,
                     acknowledgement_deadline__lte=now)
             .select_related("product", "source_point", "destination_point"))
    for transfer in stale:
        # Complete using the sender's quantity.
        try:
            recipient_acknowledge(
                transfer=transfer,
                user=transfer.sent_by or transfer.requested_by,
                quantity_received=transfer.quantity_sent,
                notes="Auto-completed without recipient acknowledgement.",
            )
            transfer.refresh_from_db()
            transfer.completion_method = "AUTO"
            transfer.save(update_fields=["completion_method"])
            _notify_directors_of_transfer_event(
                transfer,
                f"Transfer #{transfer.pk} auto-completed without recipient "
                f"acknowledgement after 30 minutes. "
                f"{transfer.quantity_sent} of {transfer.product.name} moved "
                f"{transfer.source_point.code} -> {transfer.destination_point.code}.",
            )
        except Exception:
            log.exception("Auto-complete failed for transfer %s", transfer.pk)


@transaction.atomic
def cancel_transfer(*, transfer, user, reason=""):
    """Director-only: cancel a transfer that hasn't been shipped. No balance change."""
    if not is_director(user):
        raise PermissionDenied("Only the Director may cancel transfers.")
    if not reason:
        raise ValidationError("A cancellation reason is required.")
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status not in (
        StockTransfer.Status.REQUESTED,
        StockTransfer.Status.SENT,
    ):
        raise ValidationError(
            f"Cannot cancel a transfer in status {transfer.status}."
        )
    transfer.status = StockTransfer.Status.CANCELLED
    transfer.save(update_fields=["status"])
    record_audit(
        user=user, action="UPDATE", obj=transfer,
        operating_point=transfer.source_point,
        reason=f"Transfer cancelled: {reason}",
        changes={"status": "CANCELLED", "reason": reason},
    )
    return transfer

# ===========================================================================
@transaction.atomic
def record_wastage(*, user, product, operating_point, quantity,
                   reason, reference=""):
    """Record a WASTAGE movement. Debits the balance at the given point.

    Raises ValidationError if the balance is insufficient.
    Fires low-stock evaluation.
    """
    if not user_can_act_at_point(user, operating_point):
        raise PermissionDenied(
            f"You are not authorized for stock operations at {operating_point.code}."
        )
    if not reason:
        raise ValidationError("A wastage reason is required.")

    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active or not product.track_inventory:
        raise ValidationError(
            "Wastage can only be recorded for active, inventory-tracked products."
        )

    qty = _quantize(quantity)

    balance, _ = StockBalance.objects.get_or_create(
        product=product, operating_point=operating_point,
        defaults={"quantity": Decimal("0")},
    )
    balance = StockBalance.objects.select_for_update().get(pk=balance.pk)

    if balance.quantity < qty:
        raise ValidationError(
            f"Cannot record wastage exceeding stock. Available: "
            f"{balance.quantity} {product.selling_unit.abbreviation}; "
            f"requested: {qty}."
        )

    balance.quantity -= qty
    balance.save(update_fields=["quantity", "updated_at"])

    movement = StockLedger.objects.create(
        product=product,
        operating_point=operating_point,
        movement_type=StockLedger.MovementType.WASTAGE,
        quantity_delta=-qty,
        balance_after=balance.quantity,
        reference=reference,
        reason=reason,
        created_by=user,
    )

    record_audit(
        user=user, action=AuditLog.Action.CREATE, obj=movement,
        operating_point=operating_point,
        reason=f"Wastage: {reason}",
        changes={
            "product": product.name,
            "quantity_delta": str(-qty),
            "balance_after": str(balance.quantity),
            "reference": reference,
        },
    )

    evaluate_after_stock_change(
        product=product,
        operating_point=operating_point,
        actor=user,
    )
    return movement
