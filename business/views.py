from django.shortcuts import render

# Create your views here.


# ---------------------------------------------------------------------------
# Point-scoped open / close
# ---------------------------------------------------------------------------
from django.contrib.auth.decorators import login_required as _pd_login_required
from django.contrib import messages as _pd_messages
from django.core.exceptions import PermissionDenied as _pd_PermDenied, ValidationError as _pd_ValErr
from django.shortcuts import get_object_or_404 as _pd_404, redirect as _pd_redirect
from accounts.models import OperatingPoint as _pd_OP
from stock.permissions import require_active_staff as _pd_req_staff
from business.services import open_point as _pd_open, close_point as _pd_close


@_pd_login_required
@_pd_req_staff
def point_open(request, code):
    if request.method != "POST":
        raise _pd_PermDenied("POST required.")
    point = _pd_404(_pd_OP, code__iexact=code)
    try:
        _pd_open(user=request.user, point=point)
        _pd_messages.success(request, f"{point.code} opened.")
    except (_pd_PermDenied, _pd_ValErr) as e:
        _pd_messages.error(request, str(e))
    return _pd_redirect("stock:point_detail", code=point.code)


@_pd_login_required
@_pd_req_staff
def point_close(request, code):
    if request.method != "POST":
        raise _pd_PermDenied("POST required.")
    point = _pd_404(_pd_OP, code__iexact=code)
    try:
        _pd_close(user=request.user, point=point)
        _pd_messages.success(request, f"{point.code} closed.")
    except (_pd_PermDenied, _pd_ValErr) as e:
        _pd_messages.error(request, str(e))
    return _pd_redirect("stock:point_detail", code=point.code)