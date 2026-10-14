"""Business-day service layer.

Centralises deployment logic so it can be called from signals, views,
and management commands without duplicating business rules.
"""
from __future__ import annotations

import logging

from django.db import transaction

from accounts.models import Employee
from business.business_day_models import BusinessDay, StaffDeployment

log = logging.getLogger(__name__)


def _eligible_for_auto_deploy(employee) -> bool:
    if not employee or not employee.is_active:
        return False
    if employee.operating_point_id is None:
        return False
    point = employee.operating_point
    if point is None or not point.is_active:
        return False
    # Conservative: only auto-deploy employees whose status is plainly ACTIVE.
    # Suspended / on-leave / off-duty / terminated employees are skipped —
    # the Director can still deploy them manually if needed.
    return employee.employment_status == Employee.EmploymentStatus.ACTIVE


def _auto_deploy_actor(business_day):
    if business_day.opened_by_id:
        return business_day.opened_by
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.filter(
        is_active=True, is_staff=True, role="DIRECTOR",
    ).first()


@transaction.atomic
def auto_deploy_employee(employee, business_day=None):
    if not _eligible_for_auto_deploy(employee):
        return None

    if business_day is None:
        business_day = BusinessDay.objects.filter(
            status=BusinessDay.Status.OPEN,
        ).first()
    if business_day is None or not business_day.is_open:
        return None

    point = employee.operating_point

    existing = StaffDeployment.objects.filter(
        employee=employee,
        business_day=business_day,
        operating_point=point,
        status=StaffDeployment.Status.ACTIVE,
    ).first()
    if existing:
        return existing

    actor = _auto_deploy_actor(business_day)
    if actor is None:
        log.warning(
            "auto_deploy_employee: no actor for business_day=%s",
            business_day.pk,
        )
        return None

    deployment = StaffDeployment.objects.create(
        employee=employee,
        operating_point=point,
        business_day=business_day,
        start_time=business_day.scheduled_open_time,
        end_time=business_day.scheduled_close_time,
        deployment_type=StaffDeployment.DeploymentType.NORMAL,
        status=StaffDeployment.Status.ACTIVE,
        assigned_by=actor,
        reason="Auto-deployed from assignment",
    )
    log.info(
        "Auto-deployed %s to %s on %s",
        employee, point.code, business_day.business_date,
    )
    return deployment


def auto_deploy_all_for_day(business_day):
    if business_day is None or not business_day.is_open:
        return []
    created = []
    for employee in (
        Employee.objects
        .filter(is_active=True, operating_point__is_active=True)
        .select_related("operating_point", "user")
    ):
        result = auto_deploy_employee(employee, business_day=business_day)
        if result:
            created.append(result)
    return created


# ---------------------------------------------------------------------------
# Point-scoped open / close
# ---------------------------------------------------------------------------
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone as _pd_tz2
from business.business_day_models import PointDay as _pd_PointDay
from business.permissions import user_can_open_point as _pd_can_open


@transaction.atomic
def open_point(*, user, point, business_day=None):
    """Open a point for business.

    Creates the global BusinessDay if none is open (opened_by=user),
    then get_or_creates the PointDay for (day, point). If a PointDay
    already exists and is open, returns it untouched. If it exists and
    is closed, reopens it and bumps reopened_count.
    """
    if not point.is_active:
        raise ValidationError(f"{point.code} is inactive.")
    if not _pd_can_open(user, point):
        raise PermissionDenied(
            f"You are not authorized to open {point.code}.")

    if business_day is None:
        business_day = BusinessDay.objects.filter(
            status=BusinessDay.Status.OPEN).first()

    # Stale-day guard: if the open day isn't from today (local date),
    # close it before starting a fresh one. Prevents a lingering day
    # from silently absorbing a week of sales.
    today = _pd_tz2.localdate()
    if business_day is not None and business_day.business_date != today:
        business_day.status = BusinessDay.Status.CLOSED
        business_day.actual_closed_at = _pd_tz2.now()
        business_day.closed_by = user
        business_day.save(update_fields=[
            "status", "actual_closed_at", "closed_by", "updated_at"])
        business_day = None

    if business_day is None:
        # Look for an existing day for today (may be CLOSED from a
        # prior auto-close this same date — reuse it rather than
        # creating a duplicate).
        business_day = BusinessDay.objects.filter(
            business_date=today).first()
        if business_day is None:
            business_day = BusinessDay.objects.create(
                business_date=today,
                status=BusinessDay.Status.OPEN,
                actual_opened_at=_pd_tz2.now(),
                opened_by=user,
            )
        else:
            # Reopen a same-date closed day rather than creating a new one.
            business_day.status = BusinessDay.Status.OPEN
            if business_day.actual_opened_at is None:
                business_day.actual_opened_at = _pd_tz2.now()
            business_day.reopened_at = _pd_tz2.now()
            business_day.reopened_by = user
            business_day.save(update_fields=[
                "status", "actual_opened_at", "reopened_at",
                "reopened_by", "updated_at"])

    pd = _pd_PointDay.objects.select_for_update().filter(
        business_day=business_day,
        operating_point=point,
    ).first()

    if pd is None:
        pd = _pd_PointDay.objects.create(
            business_day=business_day,
            operating_point=point,
            opened_at=_pd_tz2.now(),
            opened_by=user,
        )
    else:
        if pd.is_open:
            return pd  # already open, no-op
        pd.closed_at = None
        pd.closed_by = None
        pd.reopened_count = pd.reopened_count + 1
        pd.save(update_fields=["closed_at", "closed_by",
                               "reopened_count", "updated_at"])

    try:
        from audit.services import record_audit
        record_audit(
            user=user, action="UPDATE", obj=business_day,
            operating_point=point,
            reason=f"Opened {point.code}",
            changes={"action": "point_open", "point": point.code},
        )
    except Exception:
        log.exception("audit failed for point_open")
    return pd


@transaction.atomic
def close_point(*, user, point, business_day=None):
    """Close a point. If it was the last open point, close the global day."""
    if not _pd_can_open(user, point):
        raise PermissionDenied(
            f"You are not authorized to close {point.code}.")

    if business_day is None:
        business_day = BusinessDay.objects.filter(
            status=BusinessDay.Status.OPEN).first()
    if business_day is None:
        raise ValidationError("No open business day.")

    pd = _pd_PointDay.objects.select_for_update().filter(
        business_day=business_day,
        operating_point=point,
    ).first()
    if pd is None or not pd.is_open:
        raise ValidationError(f"{point.code} is not currently open.")

    pd.closed_at = _pd_tz2.now()
    pd.closed_by = user
    pd.save(update_fields=["closed_at", "closed_by", "updated_at"])

    # Auto-close the global day if no PointDays remain open
    still_open = _pd_PointDay.objects.filter(
        business_day=business_day, closed_at__isnull=True,
    ).exists()
    if not still_open:
        business_day.status = BusinessDay.Status.CLOSED
        business_day.actual_closed_at = _pd_tz2.now()
        business_day.closed_by = user
        business_day.save(update_fields=[
            "status", "actual_closed_at", "closed_by", "updated_at"])

    try:
        from audit.services import record_audit
        record_audit(
            user=user, action="UPDATE", obj=business_day,
            operating_point=point,
            reason=f"Closed {point.code}",
            changes={"action": "point_close", "point": point.code},
        )
    except Exception:
        log.exception("audit failed for point_close")
    return pd


def point_day_for(point, business_day):
    """Return the PointDay row for a point on a day, or None."""
    if business_day is None:
        return None
    return _pd_PointDay.objects.filter(
        business_day=business_day,
        operating_point=point,
    ).first()