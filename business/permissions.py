"""Authorization for business-day operations, point-scoped."""
from __future__ import annotations

from django.utils import timezone

from accounts.models import Employee
from business.business_day_models import BusinessDay, StaffDeployment
try:
    from stock.permissions import is_active_staff, is_director, user_can_act_at_point
except ImportError:
    # Fallbacks if stock.permissions ever moves
    def is_director(user):
        return bool(user and user.is_authenticated and getattr(user, "is_director", False))

    def is_active_staff(user):
        return bool(user and user.is_authenticated
                    and getattr(user, "is_active_staff_user", False))

    def user_can_act_at_point(user, point):
        if is_director(user):
            return True
        emp = getattr(user, "employee_profile", None)
        return bool(emp and emp.operating_point_id == getattr(point, "pk", point))


def user_can_open_point(user, point) -> bool:
    """Director: any point. Staff: home point or today's deployment."""
    if is_director(user):
        return True
    if not is_active_staff(user):
        return False

    # Any open business day takes precedence
    open_day = BusinessDay.objects.filter(
        status=BusinessDay.Status.OPEN).first()

    # If no open day, fall back to home-point assignment
    if open_day is None:
        emp = getattr(user, "employee_profile", None)
        return bool(emp and emp.operating_point_id == point.pk)

    # Home assignment counts
    if user_can_act_at_point(user, point):
        return True

    # Deployment to this point today counts
    return StaffDeployment.objects.filter(
        employee__user=user,
        business_day=open_day,
        operating_point=point,
        status=StaffDeployment.Status.ACTIVE,
    ).exists()
