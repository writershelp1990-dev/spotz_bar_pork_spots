"""Transactional workflows for Property & Assets.

Every function here runs inside a single atomic block and updates the
append-only AuditLog. State transitions are enforced by explicit
precondition checks; invalid transitions raise ValidationError.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.services import record_audit
from .models import (
    Asset, AssetNewItemReport, AssetRelocation, ConditionReport,
    PropertyCheck, PropertyCheckLine,
)
from .permissions import is_director, user_can_view_asset, areas_for

# ---------------------------------------------------------------------------
# Asset number generation
# ---------------------------------------------------------------------------
AREA_PREFIX = {
    "BAR": "BAR",
    "BAR_COUNTER": "BARC",
    "BUTCHERY": "BUT",
    "CHOMA": "CHO",
    "KITCHEN": "KIT",
    "DINING_AREA": "DIN",
    "OUTDOOR_AREA": "OUT",
    "WASHROOMS": "WSH",
    "GARDEN_COMPOUND": "GAR",
    "OFFICE": "OFF",
    "STORE": "STR",
    "OTHER": "OTH",
}


def _next_asset_number(area_code: str) -> str:
    """Return the next unused SPZ-<AREA>-#### number for the given area.

    Must be called inside an atomic transaction so the SELECT FOR UPDATE
    serializes concurrent creates. Never reuses a number, even for
    disposed assets.
    """
    prefix = AREA_PREFIX.get(area_code, "GEN")
    full_prefix = f"SPZ-{prefix}-"
    last = (Asset.objects
            .select_for_update()
            .filter(asset_number__startswith=full_prefix)
            .order_by("-asset_number")
            .values_list("asset_number", flat=True)
            .first())
    if last:
        try:
            n = int(last.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            n = 0
    else:
        n = 0
    return f"{full_prefix}{n + 1:04d}"


# ---------------------------------------------------------------------------
# Asset register (Director only)
# ---------------------------------------------------------------------------
@transaction.atomic
def create_asset(*, user, data: dict) -> Asset:
    if not is_director(user):
        raise PermissionDenied("Only the Director may create assets.")
    if not data.get("asset_number"):
        data["asset_number"] = _next_asset_number(data["current_area"].code)
    asset = Asset.objects.create(created_by=user, **data)
    record_audit(user=user, action="CREATE", obj=asset,
                 reason="Created asset",
                 changes={"asset_number": asset.asset_number, "name": asset.name,
                          "area": asset.current_area.code})
    return asset


@transaction.atomic
def update_asset(*, user, asset: Asset, data: dict) -> Asset:
    if not is_director(user):
        raise PermissionDenied("Only the Director may edit assets.")
    before_area = asset.current_area.code
    for k, v in data.items():
        setattr(asset, k, v)
    asset.updated_by = user
    asset.save()
    after_area = asset.current_area.code
    changes = {k: str(v) for k, v in data.items()}
    if before_area != after_area:
        changes["current_area"] = {"old": before_area, "new": after_area}
    record_audit(user=user, action="UPDATE", obj=asset,
                 reason="Edited asset", changes=changes)
    return asset


@transaction.atomic
def dispose_asset(*, user, asset: Asset, reason: str) -> Asset:
    if not is_director(user):
        raise PermissionDenied("Only the Director may dispose assets.")
    if not reason:
        raise ValidationError("Disposal reason is required.")
    old = asset.lifecycle_status
    asset.lifecycle_status = Asset.Lifecycle.DISPOSED
    asset.updated_by = user
    asset.save(update_fields=["lifecycle_status", "updated_by", "updated_at"])
    record_audit(user=user, action="UPDATE", obj=asset,
                 reason=f"Disposed: {reason}",
                 changes={"lifecycle_status": {"old": old, "new": "DISPOSED"}})
    return asset


# ---------------------------------------------------------------------------
# Property check
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_property_check(*, user, area, lines: list, notes: str = "") -> PropertyCheck:
    check = PropertyCheck.objects.create(area=area, checked_by=user, notes=notes)
    for line in lines:
        asset = Asset.objects.get(pk=line["asset_id"])
        if not user_can_view_asset(user, asset):
            raise PermissionDenied(f"No access to {asset.asset_number}.")
        PropertyCheckLine.objects.create(
            property_check=check, asset=asset,
            expected_quantity=line["expected_quantity"],
            observed_quantity=line["observed_quantity"],
            condition=line["condition"], notes=line.get("notes", ""),
        )
    record_audit(user=user, action="CREATE", obj=check,
                 reason="Property check submitted",
                 changes={"area": area.code, "line_count": len(lines)})
    return check


# ---------------------------------------------------------------------------
# New-item report
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_new_item_report(*, user, data: dict) -> AssetNewItemReport:
    report = AssetNewItemReport.objects.create(reported_by=user, **data)
    record_audit(user=user, action="CREATE", obj=report,
                 reason="New-item report submitted",
                 changes={"proposed_name": report.proposed_name,
                          "area": report.area.code})
    return report


@transaction.atomic
def approve_new_item_report(*, user, report: AssetNewItemReport,
                            review_notes: str = "") -> Asset:
    if not is_director(user):
        raise PermissionDenied("Only the Director may approve new-item reports.")
    if report.status != AssetNewItemReport.Status.PENDING:
        raise ValidationError("This report has already been reviewed.")
    if report.reported_by_id == user.pk:
        raise PermissionDenied("You cannot approve your own report.")
    asset = Asset.objects.create(
        asset_number=report.asset_identifier or _next_asset_number(report.area.code),
        name=report.proposed_name, category=report.proposed_category,
        description=report.description, quantity=report.quantity,
        current_area=report.area, condition=report.condition,
        created_by=user,
    )
    report.status = AssetNewItemReport.Status.APPROVED
    report.reviewed_by = user
    report.reviewed_at = timezone.now()
    report.review_notes = review_notes
    report.resulting_asset = asset
    report.save()
    record_audit(user=user, action="UPDATE", obj=report,
                 reason="Approved new-item report",
                 changes={"status": "APPROVED", "asset_number": asset.asset_number})
    return asset


@transaction.atomic
def reject_new_item_report(*, user, report: AssetNewItemReport,
                           review_notes: str) -> AssetNewItemReport:
    if not is_director(user):
        raise PermissionDenied("Only the Director may reject new-item reports.")
    if report.status != AssetNewItemReport.Status.PENDING:
        raise ValidationError("This report has already been reviewed.")
    if not review_notes:
        raise ValidationError("Reason is required to reject.")
    report.status = AssetNewItemReport.Status.REJECTED
    report.reviewed_by = user
    report.reviewed_at = timezone.now()
    report.review_notes = review_notes
    report.save()
    record_audit(user=user, action="UPDATE", obj=report,
                 reason="Rejected new-item report",
                 changes={"status": "REJECTED", "reason": review_notes})
    return report


# ---------------------------------------------------------------------------
# Condition reports
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_condition_report(*, user, asset: Asset, data: dict) -> ConditionReport:
    if not user_can_view_asset(user, asset):
        raise PermissionDenied("No access to this asset.")
    report = ConditionReport.objects.create(
        asset=asset, area=asset.current_area, reported_by=user, **data,
    )
    record_audit(user=user, action="CREATE", obj=report,
                 reason="Condition report submitted",
                 changes={"asset": asset.asset_number,
                          "condition": report.condition_reported})
    return report


@transaction.atomic
def resolve_condition_report(*, user, report: ConditionReport,
                             resolution_notes: str) -> ConditionReport:
    if not is_director(user):
        raise PermissionDenied("Only the Director may resolve condition reports.")
    report.status = ConditionReport.Status.RESOLVED
    report.resolved_by = user
    report.resolved_at = timezone.now()
    report.resolution_notes = resolution_notes
    report.save()
    record_audit(user=user, action="UPDATE", obj=report,
                 reason="Resolved condition report",
                 changes={"status": "RESOLVED"})
    return report


# ---------------------------------------------------------------------------
# Relocation workflow
# ---------------------------------------------------------------------------
@transaction.atomic
def create_relocation_request(*, user, asset: Asset, data: dict) -> AssetRelocation:
    """Rule A: requester must be able to see the asset; the receiving
    attendant must be authorized for the destination area. The requester's
    own areas do NOT restrict the destination."""
    if not user_can_view_asset(user, asset):
        raise PermissionDenied("No access to this asset.")
    if data["destination_area"].pk == asset.current_area_id:
        raise ValidationError("Destination must differ from current area.")
    recipient = data.get("receiving_attendant")
    dest = data.get("destination_area")
    if recipient is not None and dest is not None:
        from .permissions import eligible_recipients_for_area
        is_dir = bool(
            getattr(recipient, "is_superuser", False) or
            getattr(recipient, "is_director", False)
        )
        if not is_dir:
            eligible = eligible_recipients_for_area(dest.code)
            if not eligible.filter(pk=recipient.pk).exists():
                raise ValidationError(
                    f"{getattr(recipient, 'username', recipient)} is not "
                    f"deployed to or granted access at {dest.code}. Choose a "
                    f"staff member assigned to that area, or the Director."
                )
    relocation = AssetRelocation.objects.create(
        asset=asset, origin_area=asset.current_area,
        requested_by=user, **data,
    )
    record_audit(user=user, action="CREATE", obj=relocation,
                 reason="Relocation requested",
                 changes={"asset": asset.asset_number,
                          "from": asset.current_area.code,
                          "to": data["destination_area"].code})
    return relocation


@transaction.atomic
def director_decide_relocation(*, user, relocation: AssetRelocation,
                               action: str, notes: str = "") -> AssetRelocation:
    if not is_director(user):
        raise PermissionDenied("Only the Director may approve relocations.")
    if relocation.status != AssetRelocation.Status.PENDING_APPROVAL:
        raise ValidationError("This relocation is not pending approval.")
    if action == "approve":
        relocation.status = AssetRelocation.Status.APPROVED
        relocation.approved_by = user
        relocation.approved_at = timezone.now()
        relocation.director_decision_notes = notes
    elif action == "decline":
        if not notes:
            raise ValidationError("Reason is required when declining.")
        relocation.status = AssetRelocation.Status.DECLINED_BY_DIRECTOR
        relocation.approved_by = user
        relocation.approved_at = timezone.now()
        relocation.director_decision_notes = notes
    else:
        raise ValidationError("Invalid action.")
    relocation.save()
    record_audit(user=user, action="UPDATE", obj=relocation,
                 reason=f"Relocation {action}d",
                 changes={"status": relocation.status})
    return relocation


@transaction.atomic
def recipient_decide(*, user, relocation: AssetRelocation,
                    action: str, decline_reason: str = "") -> AssetRelocation:
    if relocation.receiving_attendant_id != user.pk:
        raise PermissionDenied("Only the designated recipient may respond.")
    if not relocation.is_awaiting_recipient:
        raise ValidationError("This relocation is not awaiting your decision.")
    if action == "accept":
        relocation.recipient_decision = "ACCEPTED"
        relocation.recipient_decided_at = timezone.now()
        # status remains APPROVED until receipt condition is submitted
    elif action == "decline":
        if not decline_reason:
            raise ValidationError("Reason is required when declining receipt.")
        relocation.recipient_decision = "DECLINED"
        relocation.recipient_decided_at = timezone.now()
        relocation.decline_reason = decline_reason
        relocation.status = AssetRelocation.Status.TRANSFER_EXCEPTION
    else:
        raise ValidationError("Invalid action.")
    relocation.save()
    record_audit(user=user, action="UPDATE", obj=relocation,
                 reason=f"Recipient {action}ed",
                 changes={"status": relocation.status,
                          "recipient_decision": relocation.recipient_decision})
    return relocation


@transaction.atomic
def confirm_receipt(*, user, relocation: AssetRelocation,
                   condition_at_receipt: str, receipt_notes: str = "") -> AssetRelocation:
    if relocation.receiving_attendant_id != user.pk:
        raise PermissionDenied("Only the designated recipient may confirm receipt.")
    if relocation.status not in {AssetRelocation.Status.APPROVED,
                                 AssetRelocation.Status.AWAITING_RECIPIENT}:
        raise ValidationError("This relocation is not ready for receipt confirmation.")
    if relocation.recipient_decision != "ACCEPTED":
        raise ValidationError("You must first accept this transfer.")

    relocation.received_by = user
    relocation.received_at = timezone.now()
    relocation.condition_at_receipt = condition_at_receipt
    relocation.receipt_notes = receipt_notes
    relocation.status = AssetRelocation.Status.RECEIVED
    relocation.save()

    # Officially move the asset only now, inside the same atomic block.
    asset = relocation.asset
    old_area = asset.current_area.code
    asset.current_area = relocation.destination_area
    asset.condition = condition_at_receipt
    asset.updated_by = user
    asset.save(update_fields=["current_area", "condition", "updated_by", "updated_at"])

    relocation.status = AssetRelocation.Status.COMPLETED
    relocation.completed_at = timezone.now()
    relocation.save(update_fields=["status", "completed_at"])

    record_audit(user=user, action="UPDATE", obj=relocation,
                 reason="Receipt confirmed; asset relocated",
                 changes={"from": old_area,
                          "to": relocation.destination_area.code,
                          "condition_at_receipt": condition_at_receipt,
                          "status": "COMPLETED"})
    return relocation


@transaction.atomic
def resolve_relocation_exception(*, user, relocation: AssetRelocation,
                                action: str, cancellation_reason: str = "",
                                new_recipient=None) -> AssetRelocation:
    if not is_director(user):
        raise PermissionDenied("Only the Director may resolve exceptions.")
    if relocation.status != AssetRelocation.Status.TRANSFER_EXCEPTION:
        raise ValidationError("This relocation is not in exception state.")
    if action == "cancel":
        relocation.status = AssetRelocation.Status.CANCELLED
        relocation.cancelled_at = timezone.now()
        relocation.cancellation_reason = cancellation_reason or "Cancelled by Director"
    elif action == "new_recipient":
        if not new_recipient:
            raise ValidationError("A new recipient is required.")
        relocation.receiving_attendant = new_recipient
        relocation.status = AssetRelocation.Status.APPROVED
        relocation.recipient_decision = ""
        relocation.recipient_decided_at = None
        relocation.decline_reason = ""
    else:
        raise ValidationError("Invalid action.")
    relocation.save()
    record_audit(user=user, action="UPDATE", obj=relocation,
                 reason=f"Exception resolved: {action}",
                 changes={"status": relocation.status})
    return relocation