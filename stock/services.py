"""Atomic stock movement services.

Every function here is @transaction.atomic and calls
sales.low_stock.evaluate_after_stock_change so low-stock alerts fire on
every balance change. Uses sales.StockLedger and sales.StockBalance as
the single source of truth — no competing ledger, no duplicate balance.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
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
        status=StockTransfer.Status.PENDING,
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


@transaction.atomic
def approve_transfer(*, transfer, user):
    """Director-only. PENDING -> APPROVED."""
    if not is_director(user):
        raise PermissionDenied("Only the Director may approve transfers.")
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != StockTransfer.Status.PENDING:
        raise ValidationError(
            f"Cannot approve a transfer in status {transfer.status}."
        )
    transfer.status = StockTransfer.Status.APPROVED
    transfer.approved_by = user
    transfer.approved_at = timezone.now()
    transfer.save(update_fields=["status", "approved_by", "approved_at"])

    record_audit(
        user=user, action=AuditLog.Action.UPDATE, obj=transfer,
        operating_point=transfer.source_point,
        reason="Transfer approved",
        changes={"status": "APPROVED"},
    )
    return transfer


@transaction.atomic
def complete_transfer(*, transfer, user):
    """APPROVED -> COMPLETED. Performs the balance move and writes two
    StockLedger rows (TRANSFER_OUT at source, TRANSFER_IN at dest).

    Requires: user is Director. Locks both balance rows in stable pk
    order. Raises ValidationError if source stock is insufficient.
    """
    if not is_director(user):
        raise PermissionDenied("Only the Director may complete transfers.")

    transfer = (
        StockTransfer.objects.select_for_update()
        .select_related("product", "source_point", "destination_point", "unit")
        .get(pk=transfer.pk)
    )
    if transfer.status != StockTransfer.Status.APPROVED:
        raise ValidationError(
            f"Cannot complete a transfer in status {transfer.status}."
        )

    src_balance, dst_balance = _lock_balance_pair(
        transfer.product, transfer.source_point,
        transfer.product, transfer.destination_point,
    )

    if src_balance.quantity < transfer.quantity:
        raise ValidationError(
            f"Insufficient stock at {transfer.source_point.code} for "
            f"{transfer.product.name}. Available: {src_balance.quantity} "
            f"{transfer.unit.abbreviation}; requested: {transfer.quantity}."
        )

    src_balance.quantity -= transfer.quantity
    dst_balance.quantity += transfer.quantity
    src_balance.save(update_fields=["quantity", "updated_at"])
    dst_balance.save(update_fields=["quantity", "updated_at"])

    out_movement = StockLedger.objects.create(
        product=transfer.product,
        operating_point=transfer.source_point,
        movement_type=StockLedger.MovementType.TRANSFER_OUT,
        quantity_delta=-transfer.quantity,
        balance_after=src_balance.quantity,
        reference=transfer.transfer_number,
        reason=transfer.reason or f"Transfer to {transfer.destination_point.code}",
        created_by=user,
    )
    in_movement = StockLedger.objects.create(
        product=transfer.product,
        operating_point=transfer.destination_point,
        movement_type=StockLedger.MovementType.TRANSFER_IN,
        quantity_delta=transfer.quantity,
        balance_after=dst_balance.quantity,
        reference=transfer.transfer_number,
        reason=transfer.reason or f"Transfer from {transfer.source_point.code}",
        created_by=user,
    )

    transfer.status = StockTransfer.Status.COMPLETED
    transfer.completed_by = user
    transfer.completed_at = timezone.now()
    transfer.save(update_fields=["status", "completed_by", "completed_at"])

    record_audit(
        user=user, action=AuditLog.Action.CREATE, obj=out_movement,
        operating_point=transfer.source_point,
        reason="Stock issued for transfer",
        changes={
            "transfer_number": transfer.transfer_number,
            "product": transfer.product.name,
            "quantity_delta": str(-transfer.quantity),
            "balance_after": str(src_balance.quantity),
            "destination": transfer.destination_point.code,
        },
    )
    record_audit(
        user=user, action=AuditLog.Action.CREATE, obj=in_movement,
        operating_point=transfer.destination_point,
        reason="Stock received from transfer",
        changes={
            "transfer_number": transfer.transfer_number,
            "product": transfer.product.name,
            "quantity_delta": str(transfer.quantity),
            "balance_after": str(dst_balance.quantity),
            "source": transfer.source_point.code,
        },
    )

    evaluate_after_stock_change(
        product=transfer.product,
        operating_point=transfer.source_point,
        actor=user,
    )
    evaluate_after_stock_change(
        product=transfer.product,
        operating_point=transfer.destination_point,
        actor=user,
    )

    record_audit(
        user=user, action=AuditLog.Action.UPDATE, obj=transfer,
        operating_point=transfer.source_point,
        reason="Transfer completed",
        changes={"status": "COMPLETED"},
    )
    return transfer


@transaction.atomic
def cancel_transfer(*, transfer, user, reason=""):
    """Director-only. PENDING or APPROVED -> CANCELLED. No balance change."""
    if not is_director(user):
        raise PermissionDenied("Only the Director may cancel transfers.")
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status not in (
        StockTransfer.Status.PENDING,
        StockTransfer.Status.APPROVED,
    ):
        raise ValidationError(
            f"Cannot cancel a transfer in status {transfer.status}."
        )
    if not reason:
        raise ValidationError("A cancellation reason is required.")

    transfer.status = StockTransfer.Status.CANCELLED
    transfer.save(update_fields=["status"])

    record_audit(
        user=user, action=AuditLog.Action.UPDATE, obj=transfer,
        operating_point=transfer.source_point,
        reason=f"Transfer cancelled: {reason}",
        changes={"status": "CANCELLED", "reason": reason},
    )
    return transfer


# ===========================================================================
# Wastage
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