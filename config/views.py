from django.contrib.auth.decorators import login_required
from django.db import connection
from django.shortcuts import render

from business.business_day_models import BusinessDay


def _dashboard_business_day_context(request):
    if not request.user.is_director:
        return {
            "dashboard_business_day_available": False,
            "dashboard_open_business_day": None,
            "dashboard_latest_business_day": None,
            "dashboard_previous_open_warning": None,
        }

    # Business-Day production migration is intentionally still pending.
    # Do not query its table until it exists in the active database.
    table_names = connection.introspection.table_names()

    if BusinessDay._meta.db_table not in table_names:
        return {
            "dashboard_business_day_available": False,
            "dashboard_open_business_day": None,
            "dashboard_latest_business_day": None,
            "dashboard_previous_open_warning": None,
        }

    open_day = (
        BusinessDay.objects
        .filter(status=BusinessDay.Status.OPEN)
        .order_by("-business_date", "-id")
        .first()
    )

    latest_day = (
        BusinessDay.objects
        .order_by("-business_date", "-id")
        .first()
    )

    previous_warning = None

    if open_day:
        from business.business_day_services import get_previous_open_business_day

        previous_warning = get_previous_open_business_day(
            open_day.business_date
        )

    return {
        "dashboard_business_day_available": True,
        "dashboard_open_business_day": open_day,
        "dashboard_latest_business_day": latest_day,
        "dashboard_previous_open_warning": previous_warning,
    }


@login_required
def dashboard(request):
    context = _dashboard_business_day_context(request)
    return render(request, "dashboard.html", context)
