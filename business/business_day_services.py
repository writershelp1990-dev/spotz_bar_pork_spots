from datetime import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Employee

from audit.services import record_audit

from .business_day_models import BusinessDay, BusinessHoliday, StaffDeployment


BUSINESS_TIMEZONE = "Africa/Nairobi"


def _require_director(user):
    if not (
        user
        and user.is_authenticated
        and user.is_active_staff_user
        and user.is_director
    ):
        raise PermissionDenied("Director authorization is required.")


def _scheduled_times(for_date):
    holiday = BusinessHoliday.objects.filter(
        date=for_date,
        is_active=True,
    ).first()

    weekday = for_date.weekday()

    if holiday or weekday >= 5:
        bar_open = datetime.strptime("14:00", "%H:%M").time()
    else:
        bar_open = datetime.strptime("16:00", "%H:%M").time()

    general_open = datetime.strptime("08:00", "%H:%M").time()
    close = datetime.strptime("23:00", "%H:%M").time()

    return general_open, close, bar_open


def get_scheduled_business_day_times(for_date):
    general_open, close, bar_open = _scheduled_times(for_date)
    return {
        "business_open": general_open,
        "bar_open": bar_open,
        "close": close,
    }


def get_previous_open_business_day(business_date):
    return (
        BusinessDay.objects
        .filter(
            status=BusinessDay.Status.OPEN,
            business_date__lt=business_date,
        )
        .order_by("-business_date")
        .first()
    )


def assert_business_day_open(business_day):
    if business_day.status != BusinessDay.Status.OPEN:
        raise ValidationError(
            "This business day is closed. Reopen it through the authorized Director workflow before making permitted post-close changes."
        )


@transaction.atomic
def open_business_day(*, user, business_date=None, notes=""):
    _require_director(user)

    now = timezone.localtime()
    business_date = business_date or now.date()

    existing_open = (
        BusinessDay.objects
        .select_for_update()
        .filter(status=BusinessDay.Status.OPEN)
        .order_by("-business_date")
        .first()
    )

    if existing_open:
        if existing_open.business_date == business_date:
            raise ValidationError("This business day is already open.")

        raise ValidationError(
            "Another business day is still open. Review and resolve the previous open business day before opening a new ordinary business day."
        )

    scheduled = get_scheduled_business_day_times(business_date)

    business_day, created = BusinessDay.objects.get_or_create(
        business_date=business_date,
        defaults={
            "status": BusinessDay.Status.OPEN,
            "scheduled_open_time": scheduled["business_open"],
            "scheduled_close_time": scheduled["close"],
            "actual_opened_at": now,
            "opened_by": user,
            "notes": notes,
        },
    )

    if not created:
        if business_day.status == BusinessDay.Status.OPEN:
            raise ValidationError("This business day is already open.")

        raise ValidationError(
            "A historical business day already exists for this date. Reopening must use the explicit reopen workflow."
        )

    record_audit(
        request=None,
        user=user,
        action="CREATE",
        obj=business_day,
        model_name="BusinessDay",
        reason="Business day opened.",
        changes={
            "status": {
                "old": None,
                "new": BusinessDay.Status.OPEN,
            },
            "actual_opened_at": now.isoformat(),
        },
    )

    return business_day


@transaction.atomic
def close_business_day(*, user, business_day, notes=""):
    _require_director(user)

    business_day = (
        BusinessDay.objects
        .select_for_update()
        .get(pk=business_day.pk)
    )

    if business_day.status != BusinessDay.Status.OPEN:
        raise ValidationError("Only an open business day can be closed.")

    now = timezone.localtime()

    business_day.status = BusinessDay.Status.CLOSED
    business_day.actual_closed_at = now
    business_day.closed_by = user

    if notes:
        business_day.notes = notes

    business_day.full_clean()
    business_day.save()

    record_audit(
        user=user,
        action="CLOSE",
        obj=business_day,
        model_name="BusinessDay",
        reason=notes or "Business day closed.",
        changes={
            "status": {
                "old": BusinessDay.Status.OPEN,
                "new": BusinessDay.Status.CLOSED,
            },
            "actual_closed_at": now.isoformat(),
        },
    )

    return business_day


@transaction.atomic
def reopen_business_day(*, user, business_day, reason):
    _require_director(user)

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError("A reason is required when reopening a business day.")

    business_day = (
        BusinessDay.objects
        .select_for_update()
        .get(pk=business_day.pk)
    )

    if business_day.status != BusinessDay.Status.CLOSED:
        raise ValidationError("Only a closed business day can be reopened.")

    existing_open = (
        BusinessDay.objects
        .select_for_update()
        .filter(
            status=BusinessDay.Status.OPEN,
        )
        .exclude(pk=business_day.pk)
        .first()
    )

    if existing_open:
        raise ValidationError(
            "Another business day is already open. Resolve it before reopening this day."
        )

    now = timezone.localtime()

    business_day.status = BusinessDay.Status.OPEN
    business_day.reopened_at = now
    business_day.reopened_by = user
    business_day.reopen_reason = reason
    business_day.actual_closed_at = None
    business_day.closed_by = None

    business_day.full_clean()
    business_day.save()

    record_audit(
        user=user,
        action="REOPEN",
        obj=business_day,
        model_name="BusinessDay",
        reason=reason,
        changes={
            "status": {
                "old": BusinessDay.Status.CLOSED,
                "new": BusinessDay.Status.OPEN,
            },
            "reopened_at": now.isoformat(),
        },
    )

    return business_day


def _deployment_conflicts(deployment):
    return (
        StaffDeployment.objects
        .filter(
            employee=deployment.employee,
            business_day=deployment.business_day,
            status=StaffDeployment.Status.ACTIVE,
        )
        .exclude(pk=deployment.pk)
        .filter(
            start_time__lt=deployment.end_time,
            end_time__gt=deployment.start_time,
        )
        .select_related("operating_point")
    )


@transaction.atomic
def create_staff_deployment(
    *,
    user,
    employee,
    operating_point,
    business_day,
    start_time,
    end_time,
    deployment_type=StaffDeployment.DeploymentType.NORMAL,
    reason="",
    notes="",
    allow_conflict=False,
):
    _require_director(user)

    # Reload the business day so validation uses current database state.
    business_day = (
        BusinessDay.objects
        .select_for_update()
        .get(pk=business_day.pk)
    )

    if business_day.status != BusinessDay.Status.OPEN:
        raise ValidationError(
            "Staff deployment can only be created for an open business day."
        )

    if not operating_point.is_active:
        raise ValidationError(
            "Employees cannot be deployed to an inactive operating point."
        )

    # Only ACTIVE employees may be deployed.
    # Employment status is persisted on Employee and is the current
    # authoritative lifecycle state for deployment eligibility.
    if employee.employment_status != Employee.EmploymentStatus.ACTIVE:
        raise PermissionDenied(
            "Only ACTIVE employees may be assigned to an operating point."
        )

    deployment = StaffDeployment(
        employee=employee,
        operating_point=operating_point,
        business_day=business_day,
        start_time=start_time,
        end_time=end_time,
        deployment_type=deployment_type,
        assigned_by=user,
        reason=reason,
        notes=notes,
    )

    deployment.full_clean()

    conflicts = _deployment_conflicts(deployment)

    if conflicts.exists() and not allow_conflict:
        points = ", ".join(
            str(item.operating_point)
            for item in conflicts
        )
        raise ValidationError(
            f"Deployment overlaps existing coverage for this employee: {points}. "
            "Review the conflict before proceeding."
        )

    deployment.save()

    record_audit(
        user=user,
        action="CREATE",
        obj=deployment,
        model_name="StaffDeployment",
        operating_point=operating_point,
        reason=reason or "Staff deployment created.",
        changes={
            "employee": employee.pk,
            "operating_point": operating_point.pk,
            "business_day": business_day.business_date.isoformat(),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "deployment_type": deployment_type,
            "conflict_override": bool(allow_conflict),
        },
    )

    if conflicts.exists() and allow_conflict:
        record_audit(
            user=user,
            action="APPROVE",
            obj=deployment,
            model_name="StaffDeployment",
            operating_point=operating_point,
            reason="Director explicitly allowed an overlapping deployment.",
            changes={
                "conflicting_deployments": list(
                    conflicts.values_list("pk", flat=True)
                ),
            },
        )

    return deployment


@transaction.atomic
def cancel_staff_deployment(*, user, deployment, reason):
    _require_director(user)

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError("A reason is required when cancelling a deployment.")

    deployment = (
        StaffDeployment.objects
        .select_for_update()
        .get(pk=deployment.pk)
    )

    if deployment.status == StaffDeployment.Status.CANCELLED:
        raise ValidationError("This deployment is already cancelled.")

    deployment.status = StaffDeployment.Status.CANCELLED
    deployment.notes = (
        f"{deployment.notes}\nCancellation reason: {reason}"
    ).strip()
    deployment.save(update_fields=["status", "notes", "updated_at"])

    record_audit(
        user=user,
        action="DELETE",
        obj=deployment,
        model_name="StaffDeployment",
        operating_point=deployment.operating_point,
        reason=reason,
        changes={
            "status": {
                "old": StaffDeployment.Status.ACTIVE,
                "new": StaffDeployment.Status.CANCELLED,
            }
        },
    )

    return deployment
