from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from .forms import AuditLogFilterForm
from .models import AuditLog


def _director_required(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active_staff_user
        and user.is_director
    )


def director_audit_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped_view(request, *args, **kwargs):
        if not _director_required(request.user):
            raise PermissionDenied("Director authorization is required.")
        return view_func(request, *args, **kwargs)

    return wrapped_view


@director_audit_required
def audit_log(request):
    form = AuditLogFilterForm(request.GET or None)

    queryset = AuditLog.objects.select_related(
        "user",
        "operating_point",
    ).order_by("-timestamp", "-id")

    if form.is_valid():
        start = form.cleaned_data.get("start_datetime")
        end = form.cleaned_data.get("end_datetime")
        user = form.cleaned_data.get("user")
        action = form.cleaned_data.get("action")
        model_name = form.cleaned_data.get("model_name")
        operating_point = form.cleaned_data.get("operating_point")
        search = form.cleaned_data.get("search")

        if start:
            if start.tzinfo is None:
                from django.utils import timezone

                start = timezone.make_aware(start)
            queryset = queryset.filter(timestamp__gte=start)

        if end:
            if end.tzinfo is None:
                from django.utils import timezone

                end = timezone.make_aware(end)
            queryset = queryset.filter(timestamp__lte=end)

        if user:
            queryset = queryset.filter(user=user)

        if action:
            queryset = queryset.filter(action=action)

        if model_name:
            queryset = queryset.filter(model_name__icontains=model_name.strip())

        if operating_point:
            queryset = queryset.filter(operating_point=operating_point)

        if search:
            search = search.strip()
            if search:
                queryset = queryset.filter(
                    Q(object_repr__icontains=search)
                    | Q(object_id__icontains=search)
                    | Q(reason__icontains=search)
                    | Q(user__username__icontains=search)
                    | Q(user__first_name__icontains=search)
                    | Q(user__last_name__icontains=search)
                    | Q(model_name__icontains=search)
                )

    paginator = Paginator(queryset, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "audit/audit_log.html",
        {
            "form": form,
            "page_obj": page_obj,
            "audit_count": paginator.count,
        },
    )
