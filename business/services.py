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
