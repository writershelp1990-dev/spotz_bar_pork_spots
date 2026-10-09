from decimal import Decimal
from datetime import date, datetime
from uuid import UUID

from django.utils import timezone

from .models import AuditLog



def _json_safe(value):
    """
    Recursively convert a value into a JSON-serializable representation.

    The AuditLog.changes field is a JSONField, so any caller that passes
    Decimal / date / datetime / UUID values would otherwise raise
    TypeError from json.dumps. This helper normalizes such values.

    - Decimal      -> str (preserves precision)
    - date/datetime-> ISO 8601 string
    - UUID         -> str
    - dict/list/tuple -> recursed
    - everything else -> unchanged
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value

def _request_ip(request):
    if request is None:
        return None

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None

    return request.META.get("REMOTE_ADDR") or None


def _request_user_agent(request):
    if request is None:
        return ""

    return request.META.get("HTTP_USER_AGENT", "")[:10000]


def _infer_operating_point(obj):
    if obj is None:
        return None

    if obj.__class__.__name__ == "OperatingPoint":
        return obj

    direct_point = getattr(obj, "operating_point", None)
    if direct_point is not None:
        return direct_point

    employee = getattr(obj, "employee", None)
    if employee is not None:
        return getattr(employee, "operating_point", None)

    profile = getattr(obj, "employee_profile", None)
    if profile is not None:
        return getattr(profile, "operating_point", None)

    return None


def record_audit(
    *,
    request=None,
    user=None,
    action,
    obj=None,
    model_name=None,
    object_id=None,
    object_repr=None,
    operating_point=None,
    reason="",
    changes=None,
):
    """
    Central append-only audit writer.

    This helper records audit metadata without replacing the original
    transaction record. Existing audit records remain immutable through
    AuditLog.save()/delete().
    """
    if user is None and request is not None:
        request_user = getattr(request, "user", None)
        if getattr(request_user, "is_authenticated", False):
            user = request_user

    if model_name is None and obj is not None:
        model_name = obj.__class__.__name__

    if object_id is None and obj is not None:
        object_id = getattr(obj, "pk", None)

    if object_repr is None and obj is not None:
        object_repr = str(obj)

    if operating_point is None:
        operating_point = _infer_operating_point(obj)

    if changes is None:
        changes = {}

    return AuditLog.objects.create(
        user=user,
        action=action,
        model_name=(model_name or "System")[:100],
        object_id="" if object_id is None else str(object_id)[:100],
        object_repr=(object_repr or "")[:255],
        operating_point=operating_point,
        reason=reason or "",
        changes=_json_safe(changes) if changes is not None else {},
        ip_address=_request_ip(request),
        user_agent=_request_user_agent(request),
    )

