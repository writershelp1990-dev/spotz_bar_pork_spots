from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    EmployeeLifecycleForm,
    EmployeeWarningForm,
    EmployeeSuspensionForm,
    EmployeeLeaveForm,
    EmployeeTerminationForm,
)
from .models import (
    Employee,
    OperatingPoint,
    EmployeeWarning,
    EmployeeSuspension,
    EmployeeLeave,
    DeploymentHistory,
    EmployeeTermination,
)


def _director_required(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active_staff_user
        and user.is_director
    )


def director_employee_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not _director_required(request.user):
            raise PermissionDenied("Director authorization is required.")
        return view_func(request, *args, **kwargs)
    return wrapped


def effective_employment_status(employee, today=None):
    today = today or timezone.localdate()

    if employee.employment_status == Employee.EmploymentStatus.TERMINATED:
        return Employee.EmploymentStatus.TERMINATED

    active_suspension = employee.suspensions.filter(
        start_date__lte=today,
        end_date__gte=today,
        activated_early=False,
    ).exists()

    if active_suspension:
        return Employee.EmploymentStatus.SUSPENDED

    active_leave = employee.leave_records.filter(
        start_date__lte=today,
        end_date__gte=today,
    ).exists()

    if active_leave:
        return Employee.EmploymentStatus.ON_LEAVE

    # If the stored lifecycle status represents a suspension or leave
    # that has now expired, its effective status is ACTIVE.
    if employee.employment_status in {
        Employee.EmploymentStatus.SUSPENDED,
        Employee.EmploymentStatus.ON_LEAVE,
    }:
        return Employee.EmploymentStatus.ACTIVE

    return employee.employment_status




def _sync_effective_employment_status(employee, today=None):
    """
    Synchronize stored lifecycle status with date-driven suspension/leave
    records without changing TERMINATED employees.
    """
    effective = effective_employment_status(employee, today=today)

    if employee.employment_status == Employee.EmploymentStatus.TERMINATED:
        return effective

    if effective in {
        Employee.EmploymentStatus.SUSPENDED,
        Employee.EmploymentStatus.ON_LEAVE,
    }:
        if employee.employment_status != effective:
            employee.employment_status = effective
            employee.save(update_fields=["employment_status", "updated_at"])
        return effective

    if employee.employment_status in {
        Employee.EmploymentStatus.SUSPENDED,
        Employee.EmploymentStatus.ON_LEAVE,
    }:
        employee.employment_status = Employee.EmploymentStatus.ACTIVE
        employee.save(update_fields=["employment_status", "updated_at"])
        return Employee.EmploymentStatus.ACTIVE

    return employee.employment_status




def _audit(request, action, obj, reason="", changes=None):
    from audit.models import AuditLog

    operating_point = None

    if hasattr(obj, "operating_point_id"):
        operating_point = getattr(obj, "operating_point", None)
    elif hasattr(obj, "employee_id"):
        employee = getattr(obj, "employee", None)
        if employee is not None:
            operating_point = getattr(employee, "operating_point", None)

    AuditLog.objects.create(
        user=request.user,
        action=action,
        model_name=obj.__class__.__name__,
        object_id=str(obj.pk),
        object_repr=str(obj),
        operating_point=operating_point,
        reason=reason,
        changes=changes or {},
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )


def _status_blocks_operational_action(status):
    return status in {
        Employee.EmploymentStatus.INVITED,
        Employee.EmploymentStatus.OFF_DUTY,
        Employee.EmploymentStatus.ON_LEAVE,
        Employee.EmploymentStatus.SUSPENDED,
        Employee.EmploymentStatus.TERMINATED,
    }


@director_employee_required
def employee_directory(request):
    employees = Employee.objects.select_related(
        "user",
        "operating_point",
        "user__role_definition",
    ).order_by(
        "user__last_name",
        "user__first_name",
        "user__username",
    )

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    point_filter = request.GET.get("point", "").strip()

    if q:
        employees = employees.filter(
            models.Q(user__first_name__icontains=q)
            | models.Q(user__last_name__icontains=q)
            | models.Q(user__email__icontains=q)
            | models.Q(user__username__icontains=q)
        )

    if point_filter:
        employees = employees.filter(operating_point_id=point_filter)

    rows = [
        {
            "employee": employee,
            "status": effective_employment_status(employee),
        }
        for employee in employees
    ]

    if status_filter:
        rows = [
            row for row in rows
            if row["status"] == status_filter
        ]

    counts = {
        code: sum(1 for row in rows if row["status"] == code)
        for code in Employee.EmploymentStatus.values
    }

    return render(
        request,
        "accounts/employee_directory.html",
        {
            "rows": rows,
            "counts": counts,
            "operating_points": OperatingPoint.objects.filter(is_active=True),
            "query": q,
            "selected_status": status_filter,
            "selected_point": point_filter,
            "status_choices": Employee.EmploymentStatus.choices,
        },
    )


@director_employee_required
def employee_detail(request, pk):
    employee = get_object_or_404(
        Employee.objects.select_related(
            "user",
            "operating_point",
            "user__role_definition",
        ),
        pk=pk,
    )

    return render(
        request,
        "accounts/employee_detail.html",
        {
            "employee": employee,
            "status": effective_employment_status(employee),
            "warnings": employee.warnings.select_related("issued_by"),
            "suspensions": employee.suspensions.select_related(
                "issued_by",
                "activated_by",
            ),
            "leave_records": employee.leave_records.select_related(
                "approved_by"
            ),
            "deployments": employee.deployment_history.select_related(
                "from_operating_point",
                "to_operating_point",
                "changed_by",
            ),
            "termination": getattr(employee, "termination", None),
        },
    )



def _authorize_employee_deployment(employee, operating_point, status_override=None):
    """
    Server-side deployment authorization.

    Deployment is permitted only for ACTIVE employees and only to
    an active operating point.

    Explicit SUSPENDED and ON_LEAVE lifecycle states are denied here
    even when effective_employment_status() is being evaluated
    independently of the synchronization workflow.
    """
    if status_override is not None:
        status = status_override
    else:
        status = effective_employment_status(employee)

        if employee.employment_status in {
            Employee.EmploymentStatus.SUSPENDED,
            Employee.EmploymentStatus.ON_LEAVE,
        }:
            status = employee.employment_status

    if status != Employee.EmploymentStatus.ACTIVE:
        raise PermissionDenied(
            "Only ACTIVE employees may be assigned to an operating point."
        )

    if operating_point is None:
        raise PermissionDenied(
            "An operating point is required for deployment."
        )

    if not operating_point.is_active:
        raise PermissionDenied(
            "Employees cannot be deployed to an inactive operating point."
        )




@director_employee_required
@transaction.atomic

@director_employee_required
@transaction.atomic
def employee_edit(request, pk):
    employee = get_object_or_404(
        Employee.objects.select_related("user", "operating_point"),
        pk=pk,
    )

    if employee.user_id == request.user.id:
        messages.error(
            request,
            "You cannot change your own employment lifecycle or deployment.",
        )
        return redirect("employee_detail", pk=pk)

    _sync_effective_employment_status(employee)
    current_status = effective_employment_status(employee)

    if request.method == "POST":
        form = EmployeeLifecycleForm(request.POST)

        if form.is_valid():
            new_status = form.cleaned_data["employment_status"]
            new_role = form.cleaned_data["role_definition"]
            new_date_joined = form.cleaned_data["date_joined"]
            new_point = form.cleaned_data["operating_point"]

            if current_status == Employee.EmploymentStatus.TERMINATED:
                messages.error(
                    request,
                    "Terminated employees cannot be edited through this workflow.",
                )
                return redirect("employee_detail", pk=pk)

            if employee.employment_status == Employee.EmploymentStatus.INVITED:
                if new_status != Employee.EmploymentStatus.INVITED:
                    messages.error(
                        request,
                        "An invited employee must complete account activation first.",
                    )
                    return redirect("employee_detail", pk=pk)

            protected_statuses = {
                Employee.EmploymentStatus.SUSPENDED,
                Employee.EmploymentStatus.ON_LEAVE,
                Employee.EmploymentStatus.TERMINATED,
            }

            if new_status in protected_statuses:
                messages.error(
                    request,
                    "Use the dedicated lifecycle workflow for suspension, leave or termination.",
                )
                return redirect("employee_detail", pk=pk)

            allowed = {
                Employee.EmploymentStatus.INVITED: {
                    Employee.EmploymentStatus.INVITED,
                },
                Employee.EmploymentStatus.ACTIVE: {
                    Employee.EmploymentStatus.ACTIVE,
                    Employee.EmploymentStatus.OFF_DUTY,
                },
                Employee.EmploymentStatus.OFF_DUTY: {
                    Employee.EmploymentStatus.OFF_DUTY,
                    Employee.EmploymentStatus.ACTIVE,
                },
                Employee.EmploymentStatus.ON_LEAVE: {
                    Employee.EmploymentStatus.ON_LEAVE,
                },
                Employee.EmploymentStatus.SUSPENDED: {
                    Employee.EmploymentStatus.SUSPENDED,
                },
                Employee.EmploymentStatus.TERMINATED: {
                    Employee.EmploymentStatus.TERMINATED,
                },
            }

            if new_status not in allowed.get(current_status, {current_status}):
                messages.error(
                    request,
                    "That employment-status transition is not permitted.",
                )
                return redirect("employee_edit", pk=pk)

            if new_status == Employee.EmploymentStatus.ACTIVE:
                _authorize_employee_deployment(
                    employee,
                    new_point,
                    status_override=Employee.EmploymentStatus.ACTIVE,
                )

            old_role = employee.user.role_definition
            old_point = employee.operating_point
            old_status = employee.employment_status

            point_changed = (
                getattr(old_point, "pk", None)
                != getattr(new_point, "pk", None)
            )

            if point_changed and new_status != Employee.EmploymentStatus.ACTIVE:
                messages.error(
                    request,
                    "Only ACTIVE employees may be deployed to an operating point.",
                )
                return redirect("employee_edit", pk=pk)

            if point_changed:
                _authorize_employee_deployment(
                    employee,
                    new_point,
                    status_override=new_status,
                )

            employee.user.role_definition = new_role
            employee.user.role = new_role.code
            employee.user.save(update_fields=["role_definition", "role"])

            employee.date_joined = new_date_joined
            employee.employment_status = new_status
            employee.operating_point = new_point
            employee.save(
                update_fields=[
                    "date_joined",
                    "employment_status",
                    "operating_point",
                    "updated_at",
                ]
            )

            if getattr(old_role, "pk", None) != getattr(new_role, "pk", None):
                _audit(
                    request,
                    "UPDATE",
                    employee.user,
                    reason="Employee role changed.",
                    changes={
                        "role": [
                            old_role.code if old_role else None,
                            new_role.code,
                        ]
                    },
                )

            if point_changed:
                DeploymentHistory.objects.create(
                    employee=employee,
                    from_operating_point=old_point,
                    to_operating_point=new_point,
                    reason="Director deployment change.",
                    changed_by=request.user,
                )
                _audit(
                    request,
                    "UPDATE",
                    employee,
                    reason="Employee deployment changed.",
                    changes={
                        "operating_point": [
                            old_point.code if old_point else None,
                            new_point.code if new_point else None,
                        ]
                    },
                )

            if old_status != new_status:
                _audit(
                    request,
                    "UPDATE",
                    employee,
                    reason="Employee employment status changed.",
                    changes={
                        "employment_status": [old_status, new_status]
                    },
                )

            messages.success(request, "Employee profile updated.")
            return redirect("employee_detail", pk=pk)

    else:
        form = EmployeeLifecycleForm(
            initial={
                "role_definition": employee.user.role_definition,
                "employment_status": employee.employment_status,
                "date_joined": employee.date_joined,
                "operating_point": employee.operating_point,
            }
        )

    return render(
        request,
        "accounts/employee_form.html",
        {
            "form": form,
            "employee": employee,
            "title": "Edit Employee",
            "submit_label": "Save Changes",
        },
    )


@director_employee_required
@transaction.atomic
def employee_warning(request, pk):
    employee = get_object_or_404(Employee, pk=pk)

    if request.method == "POST":
        form = EmployeeWarningForm(request.POST)

        if form.is_valid():
            warning = form.save(commit=False)
            warning.employee = employee
            warning.issued_by = request.user
            warning.save()

            _audit(
                request,
                "OTHER",
                warning,
                reason=warning.reason,
                changes={
                    "warning_type": warning.warning_type,
                    "severity": warning.severity,
                },
            )

            messages.success(request, "Warning recorded permanently.")
            return redirect("employee_detail", pk=pk)
    else:
        form = EmployeeWarningForm(
            initial={"incident_date": timezone.localdate()}
        )

    return render(
        request,
        "accounts/employee_form.html",
        {
            "form": form,
            "employee": employee,
            "title": "Issue Warning",
            "submit_label": "Issue Warning",
        },
    )


@director_employee_required
@transaction.atomic

@director_employee_required
@transaction.atomic
@director_employee_required
@transaction.atomic
def employee_suspend(request, pk):
    employee = get_object_or_404(Employee, pk=pk)

    if employee.user_id == request.user.id:
        messages.error(request, "You cannot suspend yourself.")
        return redirect("employee_detail", pk=pk)

    _sync_effective_employment_status(employee)
    current_status = effective_employment_status(employee)

    if request.method == "POST":
        form = EmployeeSuspensionForm(request.POST)

        if form.is_valid():
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data["end_date"]
            today = timezone.localdate()

            overlap = employee.suspensions.filter(
                start_date__lte=end,
                end_date__gte=start,
                activated_early=False,
            ).exists()

            if overlap:
                form.add_error(
                    None,
                    "This suspension overlaps an existing suspension.",
                )
            elif current_status == Employee.EmploymentStatus.TERMINATED:
                messages.error(
                    request,
                    "Terminated employees cannot be suspended.",
                )
                return redirect("employee_detail", pk=pk)
            elif current_status != Employee.EmploymentStatus.ACTIVE:
                messages.error(
                    request,
                    "Only ACTIVE employees can be placed on suspension.",
                )
                return redirect("employee_detail", pk=pk)
            else:
                suspension = form.save(commit=False)
                suspension.employee = employee
                suspension.issued_by = request.user
                suspension.save()

                old_status = employee.employment_status

                if start <= today <= end:
                    employee.employment_status = Employee.EmploymentStatus.SUSPENDED
                    employee.save(
                        update_fields=["employment_status", "updated_at"]
                    )

                _audit(
                    request,
                    "OTHER",
                    suspension,
                    reason=suspension.reason,
                    changes={
                        "employment_status": [
                            old_status,
                            Employee.EmploymentStatus.SUSPENDED
                            if start <= today <= end
                            else old_status,
                        ],
                        "start_date": str(start),
                        "end_date": str(end),
                    },
                )

                messages.success(request, "Employee suspension approved.")
                return redirect("employee_detail", pk=pk)
    else:
        if current_status == Employee.EmploymentStatus.TERMINATED:
            messages.error(
                request,
                "Terminated employees cannot be suspended.",
            )
            return redirect("employee_detail", pk=pk)

        if current_status != Employee.EmploymentStatus.ACTIVE:
            messages.error(
                request,
                "Only ACTIVE employees can be placed on suspension.",
            )
            return redirect("employee_detail", pk=pk)

        form = EmployeeSuspensionForm(
            initial={
                "start_date": timezone.localdate(),
                "end_date": timezone.localdate(),
            }
        )

    return render(
        request,
        "accounts/employee_form.html",
        {
            "form": form,
            "employee": employee,
            "title": "Suspend Employee",
            "submit_label": "Confirm Suspension",
            "danger": True,
        },
    )



@director_employee_required
@transaction.atomic

@director_employee_required
@transaction.atomic
def employee_activate(request, pk):
    employee = get_object_or_404(
        Employee.objects.select_related("operating_point"),
        pk=pk,
    )

    if employee.user_id == request.user.id:
        messages.error(request, "You cannot change your own status.")
        return redirect("employee_detail", pk=pk)

    if employee.employment_status == Employee.EmploymentStatus.TERMINATED:
        messages.error(
            request,
            "Terminated employees cannot be reactivated.",
        )
        return redirect("employee_detail", pk=pk)

    _sync_effective_employment_status(employee)
    current_status = effective_employment_status(employee)
    today = timezone.localdate()

    suspension = employee.suspensions.filter(
        start_date__lte=today,
        end_date__gte=today,
        activated_early=False,
    ).first()

    if current_status == Employee.EmploymentStatus.SUSPENDED and suspension:
        _authorize_employee_deployment(
            employee,
            employee.operating_point,
            status_override=Employee.EmploymentStatus.ACTIVE,
        )

        suspension.activated_early = True
        suspension.activated_by = request.user
        suspension.activated_at = timezone.now()
        suspension.save(
            update_fields=[
                "activated_early",
                "activated_by",
                "activated_at",
            ]
        )

        employee.employment_status = Employee.EmploymentStatus.ACTIVE
        employee.save(
            update_fields=["employment_status", "updated_at"]
        )

        _audit(
            request,
            "OTHER",
            suspension,
            reason="Suspension activated early.",
            changes={
                "employment_status": [
                    Employee.EmploymentStatus.SUSPENDED,
                    Employee.EmploymentStatus.ACTIVE,
                ]
            },
        )

        messages.success(request, "Suspension ended early.")
        return redirect("employee_detail", pk=pk)

    if current_status == Employee.EmploymentStatus.OFF_DUTY:
        _authorize_employee_deployment(
            employee,
            employee.operating_point,
            status_override=Employee.EmploymentStatus.ACTIVE,
        )

        employee.employment_status = Employee.EmploymentStatus.ACTIVE
        employee.save(
            update_fields=["employment_status", "updated_at"]
        )

        _audit(
            request,
            "OTHER",
            employee,
            reason="Employee activated from off duty.",
            changes={
                "employment_status": [
                    Employee.EmploymentStatus.OFF_DUTY,
                    Employee.EmploymentStatus.ACTIVE,
                ]
            },
        )

        messages.success(request, "Employee reactivated.")
        return redirect("employee_detail", pk=pk)

    messages.info(request, "No early activation is available.")
    return redirect("employee_detail", pk=pk)


@director_employee_required
@transaction.atomic

@director_employee_required
@transaction.atomic
@director_employee_required
@transaction.atomic
def employee_leave(request, pk):
    employee = get_object_or_404(Employee, pk=pk)

    if employee.user_id == request.user.id:
        messages.error(request, "You cannot place yourself on leave.")
        return redirect("employee_detail", pk=pk)

    _sync_effective_employment_status(employee)
    current_status = effective_employment_status(employee)

    if request.method == "POST":
        form = EmployeeLeaveForm(request.POST)

        if form.is_valid():
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data["end_date"]
            today = timezone.localdate()

            overlap_leave = employee.leave_records.filter(
                start_date__lte=end,
                end_date__gte=start,
            ).exists()

            overlap_suspension = employee.suspensions.filter(
                start_date__lte=end,
                end_date__gte=start,
                activated_early=False,
            ).exists()

            if overlap_leave or overlap_suspension:
                form.add_error(
                    None,
                    "The requested leave overlaps existing leave or suspension.",
                )
            elif current_status == Employee.EmploymentStatus.TERMINATED:
                messages.error(
                    request,
                    "Terminated employees cannot be placed on leave.",
                )
                return redirect("employee_detail", pk=pk)
            elif current_status != Employee.EmploymentStatus.ACTIVE:
                messages.error(
                    request,
                    "Only ACTIVE employees can be placed on leave.",
                )
                return redirect("employee_detail", pk=pk)
            else:
                leave = form.save(commit=False)
                leave.employee = employee
                leave.approved_by = request.user
                leave.save()

                old_status = employee.employment_status

                if start <= today <= end:
                    employee.employment_status = Employee.EmploymentStatus.ON_LEAVE
                    employee.save(
                        update_fields=["employment_status", "updated_at"]
                    )

                _audit(
                    request,
                    "APPROVE",
                    leave,
                    reason=leave.reason,
                    changes={
                        "employment_status": [
                            old_status,
                            Employee.EmploymentStatus.ON_LEAVE
                            if start <= today <= end
                            else old_status,
                        ],
                        "start_date": str(start),
                        "end_date": str(end),
                    },
                )

                messages.success(request, "Leave approved.")
                return redirect("employee_detail", pk=pk)
    else:
        if current_status == Employee.EmploymentStatus.TERMINATED:
            messages.error(
                request,
                "Terminated employees cannot be placed on leave.",
            )
            return redirect("employee_detail", pk=pk)

        if current_status != Employee.EmploymentStatus.ACTIVE:
            messages.error(
                request,
                "Only ACTIVE employees can be placed on leave.",
            )
            return redirect("employee_detail", pk=pk)

        form = EmployeeLeaveForm(
            initial={
                "start_date": timezone.localdate(),
                "end_date": timezone.localdate(),
            }
        )

    return render(
        request,
        "accounts/employee_form.html",
        {
            "form": form,
            "employee": employee,
            "title": "Approve Leave",
            "submit_label": "Approve Leave",
        },
    )



@director_employee_required
@transaction.atomic

@director_employee_required
@transaction.atomic
def employee_terminate(request, pk):
    employee = get_object_or_404(Employee, pk=pk)

    if employee.user_id == request.user.id:
        messages.error(request, "You cannot terminate yourself.")
        return redirect("employee_detail", pk=pk)

    _sync_effective_employment_status(employee)
    current_status = effective_employment_status(employee)

    if current_status == Employee.EmploymentStatus.INVITED:
        messages.error(
            request,
            "Invited employees must complete account activation before termination.",
        )
        return redirect("employee_detail", pk=pk)

    if current_status == Employee.EmploymentStatus.TERMINATED:
        messages.error(
            request,
            "This employee is already terminated.",
        )
        return redirect("employee_detail", pk=pk)

    if request.method == "POST":
        form = EmployeeTerminationForm(request.POST)

        if form.is_valid():
            termination = form.save(commit=False)
            termination.employee = employee
            termination.terminated_by = request.user
            termination.save()

            old_status = employee.employment_status
            employee.employment_status = Employee.EmploymentStatus.TERMINATED
            employee.save(
                update_fields=["employment_status", "updated_at"]
            )

            _audit(
                request,
                "OTHER",
                termination,
                reason=termination.reason,
                changes={
                    "employment_status": [
                        old_status,
                        Employee.EmploymentStatus.TERMINATED,
                    ],
                    "termination_date": str(
                        termination.termination_date
                    ),
                },
            )

            messages.success(
                request,
                "Employee terminated. Final settlement remains a future payroll responsibility.",
            )
            return redirect("employee_detail", pk=pk)

    else:
        form = EmployeeTerminationForm(
            initial={"termination_date": timezone.localdate()}
        )

    return render(
        request,
        "accounts/employee_form.html",
        {
            "form": form,
            "employee": employee,
            "title": "Terminate Employee",
            "submit_label": "Confirm Termination",
            "danger": True,
        },
    )
