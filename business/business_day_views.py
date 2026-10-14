from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
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

    today = timezone.localdate()
    upcoming = BusinessHoliday.objects.filter(date__gte=today).order_by("date")
    past = BusinessHoliday.objects.filter(date__lt=today).order_by("-date")

    return render(
        request,
        "business/business_holidays.html",
        {"upcoming": upcoming, "past": past},
    )


@login_required
def holiday_edit(request, pk):
    _director_required(request)

    holiday = get_object_or_404(BusinessHoliday, pk=pk)

    if request.method == "POST":
        form = BusinessHolidayForm(request.POST, instance=holiday)
        if form.is_valid():
            form.save()
            from audit.services import record_audit
            record_audit(
                request=request,
                user=request.user,
                action="UPDATE",
                obj=holiday,
                model_name="BusinessHoliday",
                reason="Business holiday edited.",
            )
            messages.success(request, "Business holiday updated.")
            return redirect("business_day:holidays")
    else:
        form = BusinessHolidayForm(instance=holiday)

    return render(
        request,
        "business/business_day_form.html",
        {
            "form": form,
            "title": "Edit Business Holiday",
            "submit_label": "Save Changes",
        },
    )


@login_required
def holiday_toggle(request, pk):
    _director_required(request)

    if request.method != "POST":
        raise PermissionDenied("POST required.")

    holiday = get_object_or_404(BusinessHoliday, pk=pk)
    old = holiday.is_active
    holiday.is_active = not old
    holiday.save(update_fields=["is_active", "updated_at"])

    from audit.services import record_audit
    record_audit(
        request=request,
        user=request.user,
        action="UPDATE",
        obj=holiday,
        model_name="BusinessHoliday",
        reason=f"Holiday {'deactivated' if old else 'reactivated'}.",
        changes={"is_active": {"old": old, "new": holiday.is_active}},
    )

    messages.success(
        request,
        f"{holiday.name} {'deactivated' if old else 'reactivated'}.",
    )
    return redirect("business_day:holidays")


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

# ---------------------------------------------------------------------------
# Daily Closing — read-only reconciliation for a business day
# ---------------------------------------------------------------------------
from decimal import Decimal as _dc_Decimal
from django.db.models import Count as _dc_Count, Sum as _dc_Sum
from sales.models import (
    Sale as _dc_Sale,
    StockBalance as _dc_StockBalance,
    StockLedger as _dc_StockLedger,
)
from accounts.models import OperatingPoint as _dc_OperatingPoint


@login_required
def daily_closing(request):
    _director_required(request)

    open_day = BusinessDay.objects.filter(
        status=BusinessDay.Status.OPEN
    ).first()
    day = open_day or BusinessDay.objects.order_by("-business_date").first()

    if day is None:
        return render(request, "business/business_day_closing.html", {
            "day": None,
            "is_open": False,
        })

    paid = _dc_Sale.objects.filter(
        business_day=day,
        payment_status=_dc_Sale.PaymentStatus.PAID,
    )

    totals = paid.aggregate(count=_dc_Count("id"), total=_dc_Sum("total"))
    sales_count = totals["count"] or 0
    sales_total = totals["total"] or _dc_Decimal("0")

    def _sum_by(**kw):
        return paid.filter(**kw).aggregate(s=_dc_Sum("total"))["s"] or _dc_Decimal("0")

    cash_total = _sum_by(payment_method="CASH")
    mpesa_total = _sum_by(payment_method__in=["MPESA", "MPESA_MANUAL"])
    card_total = _sum_by(payment_method="CARD")
    other_total = _sum_by(payment_method="OTHER")

    staff = (
        StaffDeployment.objects
        .filter(
            business_day=day,
            status=StaffDeployment.Status.ACTIVE,
        )
        .select_related("employee__user", "operating_point")
        .order_by("operating_point__code", "start_time")
    )

    wastage = (
        _dc_StockLedger.objects
        .filter(
            movement_type=_dc_StockLedger.MovementType.WASTAGE,
            created_at__date=day.business_date,
        )
        .select_related("product", "operating_point", "created_by")
        .order_by("-created_at")
    )
    wastage_sum = wastage.aggregate(s=_dc_Sum("quantity_delta"))["s"] or _dc_Decimal("0")

    point_stock = []
    for point in _dc_OperatingPoint.objects.filter(is_active=True).order_by("code"):
        rows = list(
            _dc_StockBalance.objects
            .filter(operating_point=point, quantity__gt=0)
            .select_related("product")
            .order_by("-quantity")[:10]
        )
        if rows:
            point_stock.append({"point": point, "balances": rows})

    return render(request, "business/business_day_closing.html", {
        "day": day,
        "is_open": day.status == BusinessDay.Status.OPEN,
        "sales_count": sales_count,
        "sales_total": sales_total,
        "cash_total": cash_total,
        "mpesa_total": mpesa_total,
        "card_total": card_total,
        "other_total": other_total,
        "staff": staff,
        "wastage": wastage,
        "wastage_total": abs(wastage_sum),
        "point_stock": point_stock,
    })