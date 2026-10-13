"""Server-side authorization for stock operations.

Mirrors the pattern in property.permissions: role + employment status +
operating point. Every stock-mutating service calls into these helpers.
"""
from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied

from accounts.models import Employee
try:
    from accounts.employee_management import effective_employment_status
except ImportError:
    from accounts.models import effective_employment_status  # fallback


def is_director(user) -> bool:
    return bool(user and user.is_authenticated and user.is_director)


def is_active_staff(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active_staff_user)


def _employee_for(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "employee_profile", None)


def stock_point_for(user):
    """Return the OperatingPoint this user is deployed to, or None.

    Director: None (they can act on any point).
    Staff: their Employee.operating_point if employment status allows it.
    """
    if is_director(user):
        return None
    emp = _employee_for(user)
    if emp is None or emp.operating_point_id is None:
        return None
    status = effective_employment_status(emp)
    if status not in {
        Employee.EmploymentStatus.ACTIVE,
        Employee.EmploymentStatus.OFF_DUTY,
    }:
        return None
    return emp.operating_point


def user_can_act_at_point(user, point) -> bool:
    """True if the user may perform stock operations at the given point.

    Director: always. Staff: only at their deployed operating point.
    """
    if is_director(user):
        return True
    if not is_active_staff(user):
        return False
    deployed = stock_point_for(user)
    if deployed is None:
        return False
    return deployed.pk == getattr(point, "pk", point)


def user_can_receive_at_point(user, point) -> bool:
    """A user may receive stock at their deployed point only, unless Director."""
    return user_can_act_at_point(user, point)


def user_can_transfer_out_of(user, source_point) -> bool:
    """Only the source point's staff (or Director) may issue a transfer out."""
    if is_director(user):
        return True
    if not is_active_staff(user):
        return False
    deployed = stock_point_for(user)
    return bool(deployed and deployed.pk == source_point.pk)


def user_can_transfer_into(user, destination_point) -> bool:
    """Only the destination point's staff (or Director) may accept a transfer.

    Under the current design, transfers are approved by the Director and
    completed by the Director. Staff in the destination may create a
    PENDING transfer that awaits Director approval. This helper is used
    when staff explicitly initiate or accept a transfer.
    """
    return user_can_act_at_point(user, destination_point)


def require_director(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_director(request.user):
            raise PermissionDenied("Only the Director may perform this action.")
        return view(request, *args, **kwargs)
    return wrapped


def require_active_staff(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_active_staff(request.user):
            raise PermissionDenied("Active staff account required.")
        return view(request, *args, **kwargs)
    return wrapped


def assert_stock_permission(user, point):
    """Raise PermissionDenied unless the user may act at the given point."""
    if not user_can_act_at_point(user, point):
        raise PermissionDenied(
            f"You are not authorized for stock operations at "
            f"{getattr(point, 'code', point)}."
        )