from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from .forms import OperatingPointForm
from .models import OperatingPoint


def _director_required(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active_staff_user
        and user.is_director
    )


def director_operating_point_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped_view(request, *args, **kwargs):
        if not _director_required(request.user):
            raise PermissionDenied("Director authorization is required.")
        return view_func(request, *args, **kwargs)

    return wrapped_view


@director_operating_point_required
def manage_operating_points(request):
    operating_points = OperatingPoint.objects.all().order_by("code")

    return render(
        request,
        "accounts/manage_operating_points.html",
        {"operating_points": operating_points},
    )


@director_operating_point_required
def create_operating_point(request):
    if request.method == "POST":
        form = OperatingPointForm(request.POST)

        if form.is_valid():
            operating_point = form.save(commit=False)
            operating_point.code = operating_point.code.upper()
            operating_point.is_system_point = False
            operating_point.save()
            return redirect("manage_operating_points")
    else:
        form = OperatingPointForm()

    return render(
        request,
        "accounts/operating_point_form.html",
        {
            "form": form,
            "page_title": "Add Operating Point",
            "submit_label": "Create Operating Point",
        },
    )


@director_operating_point_required
def edit_operating_point(request, pk):
    operating_point = get_object_or_404(OperatingPoint, pk=pk)
    original_system_code = (
        operating_point.code
        if operating_point.is_system_point
        else None
    )

    if request.method == "POST":
        form = OperatingPointForm(request.POST, instance=operating_point)

        if operating_point.is_system_point:
            form.fields["code"].disabled = True
            form.fields["code"].help_text = (
                "System operating-point codes are protected."
            )

        if form.is_valid():
            with transaction.atomic():
                updated = form.save(commit=False)

                if original_system_code is not None:
                    updated.code = original_system_code

                updated.save()

            return redirect("manage_operating_points")
    else:
        form = OperatingPointForm(instance=operating_point)

        if operating_point.is_system_point:
            form.fields["code"].disabled = True
            form.fields["code"].help_text = (
                "System operating-point codes are protected."
            )

    return render(
        request,
        "accounts/operating_point_form.html",
        {
            "form": form,
            "operating_point": operating_point,
            "page_title": "Edit Operating Point",
            "submit_label": "Save Changes",
        },
    )


@director_operating_point_required
def toggle_operating_point(request, pk):
    if request.method != "POST":
        raise PermissionDenied("Operating-point status changes require POST.")

    operating_point = get_object_or_404(OperatingPoint, pk=pk)
    operating_point.is_active = not operating_point.is_active
    operating_point.save(update_fields=["is_active", "updated_at"])

    return redirect("manage_operating_points")

