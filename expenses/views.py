"""Expense views: list, create, detail, edit, approve, reject, delete."""
from decimal import Decimal
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from audit.services import record_audit
from accounts.models import OperatingPoint
from .forms import ExpenseApproveForm, ExpenseForm, ExpenseRejectForm
from .models import Expense, ExpenseCategory


# --- Permission helpers ---------------------------------------------------

def _is_director(user):
    return bool(user and user.is_authenticated
                and getattr(user, "is_director", False))


def _is_active_staff(user):
    return bool(user and user.is_authenticated
                and getattr(user, "is_active_staff_user", False))


def _stock_point_for(user):
    emp = getattr(user, "employee_profile", None)
    if emp is None:
        return None
    return emp.operating_point


def _visible_qs(user):
    """Return the queryset of expenses the user may see."""
    if _is_director(user):
        return Expense.objects.all()
    point = _stock_point_for(user)
    q = Q(entered_by=user)
    if point is not None:
        q = q | Q(operating_point=point)
    return Expense.objects.filter(q).distinct()


def _can_see_expense(user, expense):
    if _is_director(user):
        return True
    if expense.entered_by_id == user.pk:
        return True
    point = _stock_point_for(user)
    return point is not None and expense.operating_point_id == point.pk


# --- Views ----------------------------------------------------------------

@login_required
def expense_list(request):
    if not _is_active_staff(request.user):
        raise PermissionDenied("Active staff account required.")

    qs = _visible_qs(request.user)

    # Filters
    try:
        days = int(request.GET.get("days", 30) or 30)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 365))
    start = timezone.localdate() - timedelta(days=days - 1)
    qs = qs.filter(expense_date__gte=start)

    category_code = (request.GET.get("category") or "").strip()
    if category_code:
        qs = qs.filter(category__code=category_code)

    status_filter = (request.GET.get("status") or "").strip().upper()
    if status_filter:
        qs = qs.filter(status=status_filter)

    qs = qs.select_related(
        "category", "category__parent", "operating_point",
        "entered_by", "approved_by",
    )

    totals = qs.aggregate(total=Sum("amount"))
    total_amount = totals["total"] or Decimal("0")

    entries = list(qs[:200])

    return render(request, "expenses/expense_list.html", {
        "entries": entries,
        "entry_count": len(entries),
        "total_amount": total_amount,
        "days": days,
        "category_filter": category_code,
        "status_filter": status_filter,
        "categories": ExpenseCategory.objects.filter(
            is_active=True, parent__isnull=True).order_by("sort_order", "name"),
        "status_choices": Expense.Status.choices,
        "is_dir": _is_director(request.user),
    })


@login_required
def expense_create(request):
    if not _is_active_staff(request.user):
        raise PermissionDenied("Active staff account required.")

    if request.method == "POST":
        form = ExpenseForm(request.POST, user=request.user)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.entered_by = request.user
            expense.status = Expense.Status.PENDING
            expense.save()
            try:
                record_audit(
                    request=request, user=request.user,
                    action="CREATE", model_name="Expense", obj=expense,
                    operating_point=expense.operating_point,
                    reason="Expense recorded",
                    changes={"amount": str(expense.amount),
                             "category": str(expense.category)},
                )
            except Exception:
                pass
            messages.success(request, "Expense recorded. Awaiting approval.")
            return redirect("expenses:expense_detail", pk=expense.pk)
    else:
        form = ExpenseForm(user=request.user)

    return render(request, "expenses/expense_form.html", {
        "form": form,
        "title": "Record Expense",
        "submit_label": "Record expense",
    })


@login_required
def expense_detail(request, pk):
    if not _is_active_staff(request.user):
        raise PermissionDenied("Active staff account required.")

    expense = get_object_or_404(
        Expense.objects.select_related(
            "category", "category__parent", "operating_point",
            "entered_by", "approved_by",
        ),
        pk=pk,
    )
    if not _can_see_expense(request.user, expense):
        raise PermissionDenied("You cannot view this expense.")

    return render(request, "expenses/expense_detail.html", {
        "expense": expense,
        "is_dir": _is_director(request.user),
        "can_edit": (
            expense.status == Expense.Status.PENDING
            and (_is_director(request.user)
                 or expense.entered_by_id == request.user.pk)
        ),
        "can_approve": (
            expense.status == Expense.Status.PENDING
            and _is_director(request.user)
        ),
    })


@login_required
def expense_edit(request, pk):
    if not _is_active_staff(request.user):
        raise PermissionDenied("Active staff account required.")

    expense = get_object_or_404(Expense, pk=pk)
    if not _can_see_expense(request.user, expense):
        raise PermissionDenied("You cannot edit this expense.")
    if expense.status != Expense.Status.PENDING:
        raise PermissionDenied("Only pending expenses can be edited.")
    if not (_is_director(request.user)
            or expense.entered_by_id == request.user.pk):
        raise PermissionDenied("You did not enter this expense.")

    if request.method == "POST":
        form = ExpenseForm(request.POST, instance=expense, user=request.user)
        if form.is_valid():
            form.save()
            try:
                record_audit(
                    request=request, user=request.user,
                    action="UPDATE", model_name="Expense", obj=expense,
                    operating_point=expense.operating_point,
                    reason="Expense edited",
                )
            except Exception:
                pass
            messages.success(request, "Expense updated.")
            return redirect("expenses:expense_detail", pk=expense.pk)
    else:
        form = ExpenseForm(instance=expense, user=request.user)

    return render(request, "expenses/expense_form.html", {
        "form": form,
        "title": "Edit Expense",
        "submit_label": "Save changes",
        "expense": expense,
    })


@login_required
def expense_approve(request, pk):
    if not _is_director(request.user):
        raise PermissionDenied("Director authorization required.")
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    expense = get_object_or_404(Expense, pk=pk)
    if expense.status != Expense.Status.PENDING:
        messages.error(request, "Only pending expenses can be approved.")
        return redirect("expenses:expense_detail", pk=expense.pk)

    form = ExpenseApproveForm(request.POST)
    notes = (form.cleaned_data.get("notes", "") if form.is_valid()
             else request.POST.get("notes", "")).strip()

    expense.status = Expense.Status.APPROVED
    expense.approved_by = request.user
    expense.approved_at = timezone.now()
    expense.approval_notes = notes
    expense.save(update_fields=[
        "status", "approved_by", "approved_at", "approval_notes"])

    try:
        record_audit(
            request=request, user=request.user,
            action="APPROVE", model_name="Expense", obj=expense,
            operating_point=expense.operating_point,
            reason="Expense approved",
            changes={"status": "APPROVED", "notes": notes},
        )
    except Exception:
        pass

    messages.success(request, "Expense approved.")
    return redirect("expenses:expense_detail", pk=expense.pk)


@login_required
def expense_reject(request, pk):
    if not _is_director(request.user):
        raise PermissionDenied("Director authorization required.")
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    expense = get_object_or_404(Expense, pk=pk)
    if expense.status != Expense.Status.PENDING:
        messages.error(request, "Only pending expenses can be rejected.")
        return redirect("expenses:expense_detail", pk=expense.pk)

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "A rejection reason is required.")
        return redirect("expenses:expense_detail", pk=expense.pk)

    expense.status = Expense.Status.REJECTED
    expense.approved_by = request.user
    expense.approved_at = timezone.now()
    expense.approval_notes = reason
    expense.save(update_fields=[
        "status", "approved_by", "approved_at", "approval_notes"])

    try:
        record_audit(
            request=request, user=request.user,
            action="REJECT", model_name="Expense", obj=expense,
            operating_point=expense.operating_point,
            reason="Expense rejected: " + reason,
            changes={"status": "REJECTED", "reason": reason},
        )
    except Exception:
        pass

    messages.success(request, "Expense rejected.")
    return redirect("expenses:expense_detail", pk=expense.pk)


@login_required
def expense_delete(request, pk):
    if not _is_director(request.user):
        raise PermissionDenied("Director authorization required.")
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    expense = get_object_or_404(Expense, pk=pk)
    if expense.status != Expense.Status.PENDING:
        messages.error(request,
            "Only pending expenses can be deleted. "
            "Approved or rejected expenses must stay in the record.")
        return redirect("expenses:expense_detail", pk=expense.pk)

    pk_saved = expense.pk
    try:
        record_audit(
            request=request, user=request.user,
            action="DELETE", model_name="Expense", obj=None,
            operating_point=expense.operating_point,
            reason="Expense deleted (was pending). Expense pk=" + str(pk_saved),
            changes={"amount": str(expense.amount),
                     "category": str(expense.category)},
        )
    except Exception:
        pass

    expense.delete()
    messages.success(request, "Expense deleted.")
    return redirect("expenses:expense_list")
