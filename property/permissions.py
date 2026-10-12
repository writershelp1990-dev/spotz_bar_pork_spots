"""Server-side authorization for the Property & Assets module."""
from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied

from accounts.models import Employee
try:
    from accounts.employee_management import effective_employment_status
except ImportError:
    from accounts.models import effective_employment_status  # fallback
from .models import PhysicalArea, StaffAreaAccess


def is_director(user) -> bool:
    return bool(user and user.is_authenticated and user.is_director)


def is_active_staff(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active_staff_user)


def _employee_for(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "employee_profile", None)


def all_area_codes() -> set:
    return set(PhysicalArea.objects.filter(is_active=True)
               .values_list("code", flat=True))


def areas_for(user) -> set:
    if is_director(user):
        return all_area_codes()
    if not is_active_staff(user):
        return set()
    emp = _employee_for(user)
    if emp is None:
        return set()
    return set(
        StaffAreaAccess.objects
        .filter(employee=emp, is_active=True, area__is_active=True)
        .values_list("area__code", flat=True)
    )


def user_can_access_area(user, area_code: str) -> bool:
    return area_code in areas_for(user)


def user_can_view_asset(user, asset) -> bool:
    if is_director(user):
        return True
    if not is_active_staff(user):
        return False
    return bool(asset.current_area_id and
                user_can_access_area(user, asset.current_area.code))


def user_can_report_on_asset(user, asset) -> bool:
    if is_director(user):
        return True
    if not is_active_staff(user):
        return False
    if not user_can_view_asset(user, asset):
        return False
    emp = _employee_for(user)
    if emp is None:
        return False
    status = effective_employment_status(emp)
    return status in {Employee.EmploymentStatus.ACTIVE,
                      Employee.EmploymentStatus.OFF_DUTY}


def _deny(msg="You are not authorized to perform this action."):
    raise PermissionDenied(msg)


def require_active_staff(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_active_staff(request.user):
            _deny("Active staff account required.")
        return view(request, *args, **kwargs)
    return wrapped


def require_director(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_director(request.user):
            _deny("Only the Director may perform this action.")
        return view(request, *args, **kwargs)
    return wrapped