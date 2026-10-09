from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from .business_day_forms import (
    BusinessDayCloseForm,
    BusinessDayOpenForm,
    BusinessDayReopenForm,
    BusinessHolidayForm,
    StaffDeploymentForm,
)
from .business_day_models import BusinessDay, BusinessHoliday, StaffDeployment
from .business_day_services import (
    cancel_staff_deployment,
    create_staff_deployment,
    get_previous_open_business_day,
    open_business_day,
    close_business_day,
    reopen_business_day,
)


def _director_required(request):
    user = request.user
    if not (
        user
        and user.is_authenticated
        and user.is_active_staff_user
        and user.is_director
    ):
        raise PermissionDenied("Director authorization is required.")


@login_required
def business_day_dashboard(request):
    _director_required(request)

    latest = BusinessDay.objects.order_by("-business_date").first()
    open_day = BusinessDay.objects.filter(
        status=BusinessDay.Status.OPEN
    ).first()

    previous_warning = None

    if open_day:
        previous_warning = get_previous_open_business_day(
            open_day.business_date
        )

    deployments = (
        StaffDeployment.objects
        .filter(
            business_day=open_day,
            status=StaffDeployment.Status.ACTIVE,
        )
        .select_related(
            "employee__user",
            "operating_point",
        )
        if open_day
        else StaffDeployment.objects.none()
    )

    return render(
        request,
        "business/business_day_dashboard.html",
        {
            "latest_business_day": latest,
            "open_business_day": open_day,
            "previous_warning": previous_warning,
            "deployments": deployments,
        },
    )


@login_required
def open_business_day_view(request):
    _director_required(request)

    if request.method == "POST":
        form = BusinessDayOpenForm(request.POST)
        if form.is_valid():
            try:
                open_business_day(
                    user=request.user,
                    business_date=form.cleaned_data["business_date"],
                    notes=form.cleaned_data["notes"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Business day opened successfully.")
                return redirect("business_day:dashboard")
    else:
        form = BusinessDayOpenForm()

    return render(
        request,
        "business/business_day_form.html",
        {
            "form": form,
            "title": "Open Business Day",
            "submit_label": "Open Business Day",
        },
    )


@login_required
def close_business_day_view(request, pk):
    _director_required(request)

    business_day = get_object_or_404(BusinessDay, pk=pk)

    if request.method == "POST":
        form = BusinessDayCloseForm(request.POST)
        if form.is_valid():
            try:
                close_business_day(
                    user=request.user,
                    business_day=business_day,
                    notes=form.cleaned_data["notes"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Business day closed successfully.")
                return redirect("business_day:dashboard")
    else:
        form = BusinessDayCloseForm()

    return render(
        request,
        "business/business_day_form.html",
        {
            "form": form,
            "title": f"Close Business Day — {business_day.business_date}",
            "submit_label": "Close Business Day",
            "business_day": business_day,
        },
    )


@login_required
def reopen_business_day_view(request, pk):
    _director_required(request)

    business_day = get_object_or_404(BusinessDay, pk=pk)

    if request.method == "POST":
        form = BusinessDayReopenForm(request.POST)
        if form.is_valid():
            try:
                reopen_business_day(
                    user=request.user,
                    business_day=business_day,
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Business day reopened successfully.")
                return redirect("business_day:dashboard")
    else:
        form = BusinessDayReopenForm()

    return render(
        request,
        "business/business_day_form.html",
        {
            "form": form,
            "title": f"Reopen Business Day — {business_day.business_date}",
            "submit_label": "Reopen Business Day",
            "business_day": business_day,
        },
    )


@login_required
def holiday_list(request):
    _director_required(request)

    holidays = BusinessHoliday.objects.order_by("-date")

    return render(
        request,
        "business/business_holidays.html",
        {"holidays": holidays},
    )


@login_required
def holiday_create(request):
    _director_required(request)

    if request.method == "POST":
        form = BusinessHolidayForm(request.POST)
        if form.is_valid():
            holiday = form.save()

            from audit.services import record_audit

            record_audit(
                request=request,
                user=request.user,
                action="CREATE",
                obj=holiday,
                model_name="BusinessHoliday",
                reason="Business holiday created.",
            )

            messages.success(request, "Business holiday created.")
            return redirect("business_day:holidays")
    else:
        form = BusinessHolidayForm()

    return render(
        request,
        "business/business_day_form.html",
        {
            "form": form,
            "title": "Add Business Holiday",
            "submit_label": "Save Holiday",
        },
    )


@login_required
def deployment_create(request):
    _director_required(request)

    business_day = BusinessDay.objects.filter(
        status=BusinessDay.Status.OPEN
    ).first()

    if business_day is None:
        raise PermissionDenied(
            "Open a business day before creating staff deployment coverage."
        )

    if request.method == "POST":
        form = StaffDeploymentForm(
            request.POST,
            business_day=business_day,
        )

        if form.is_valid():
            deployment = form.save(commit=False)

            try:
                create_staff_deployment(
                    user=request.user,
                    employee=deployment.employee,
                    operating_point=deployment.operating_point,
                    business_day=business_day,
                    start_time=deployment.start_time,
                    end_time=deployment.end_time,
                    deployment_type=deployment.deployment_type,
                    reason=deployment.reason,
                    notes=deployment.notes,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Staff deployment created.")
                return redirect("business_day:dashboard")
    else:
        form = StaffDeploymentForm(business_day=business_day)

    return render(
        request,
        "business/business_day_form.html",
        {
            "form": form,
            "title": f"Deploy Staff — {business_day.business_date}",
            "submit_label": "Create Deployment",
        },
    )


@login_required
def deployment_cancel(request, pk):
    _director_required(request)

    deployment = get_object_or_404(
        StaffDeployment,
        pk=pk,
    )

    if request.method != "POST":
        raise PermissionDenied("POST is required.")

    reason = request.POST.get("reason", "").strip()

    try:
        cancel_staff_deployment(
            user=request.user,
            deployment=deployment,
            reason=reason,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Staff deployment cancelled.")

    return redirect("business_day:dashboard")