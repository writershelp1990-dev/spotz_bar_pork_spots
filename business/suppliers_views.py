"""Director-facing supplier management.

Suppliers are never deleted — receipts and invoices reference them.
Instead a supplier is deactivated, which preserves the history.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from audit.services import record_audit

from .models import Supplier


def _require_director(user):
    if not (user and user.is_authenticated and getattr(user, "is_director", False)):
        raise PermissionDenied("Only the Director may manage suppliers.")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@login_required
def supplier_list(request):
    _require_director(request.user)
    q = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or "active"

    qs = Supplier.objects.all()
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(contact_person__icontains=q) |
            Q(phone__icontains=q) |
            Q(email__icontains=q)
        )

    suppliers = (qs
                 .annotate(
                     receipt_count=Count("stock_receipts", distinct=True),
                     invoice_count=Count("invoices", distinct=True),
                     total_billed=Sum("invoices__computed_total_amount"),
                     total_paid=Sum("invoices__amount_paid"),
                 )
                 .order_by("name"))

    return render(request, "business/supplier_list.html", {
        "suppliers": suppliers,
        "q": q,
        "status": status,
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@login_required
def supplier_create(request):
    _require_director(request.user)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "Supplier name is required.")
        else:
            supplier = Supplier.objects.create(
                name=name,
                contact_person=(request.POST.get("contact_person") or "").strip(),
                phone=(request.POST.get("phone") or "").strip(),
                email=(request.POST.get("email") or "").strip(),
                address=(request.POST.get("address") or "").strip(),
                tax_number=(request.POST.get("tax_number") or "").strip(),
                notes=(request.POST.get("notes") or "").strip(),
                is_active=True,
            )
            record_audit(
                user=request.user, action="CREATE", obj=supplier,
                reason="Supplier created",
                changes={"name": supplier.name, "phone": supplier.phone},
            )
            messages.success(request, f"Supplier {supplier.name} created.")
            return redirect("business:supplier_detail", pk=supplier.pk)

    return render(request, "business/supplier_form.html", {
        "mode": "create",
        "supplier": None,
    })


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
@login_required
def supplier_edit(request, pk):
    _require_director(request.user)
    supplier = get_object_or_404(Supplier, pk=pk)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "Supplier name is required.")
        else:
            old = {
                "name": supplier.name,
                "contact_person": supplier.contact_person,
                "phone": supplier.phone,
                "email": supplier.email,
                "address": supplier.address,
                "tax_number": supplier.tax_number,
                "notes": supplier.notes,
            }
            supplier.name = name
            supplier.contact_person = (request.POST.get("contact_person") or "").strip()
            supplier.phone = (request.POST.get("phone") or "").strip()
            supplier.email = (request.POST.get("email") or "").strip()
            supplier.address = (request.POST.get("address") or "").strip()
            supplier.tax_number = (request.POST.get("tax_number") or "").strip()
            supplier.notes = (request.POST.get("notes") or "").strip()
            supplier.save()

            changes = {}
            for field, before in old.items():
                after = getattr(supplier, field)
                if before != after:
                    changes[field] = {"old": before, "new": after}

            if changes:
                record_audit(
                    user=request.user, action="UPDATE", obj=supplier,
                    reason="Supplier updated", changes=changes,
                )
            messages.success(request, f"Supplier {supplier.name} updated.")
            return redirect("business:supplier_detail", pk=supplier.pk)

    return render(request, "business/supplier_form.html", {
        "mode": "edit",
        "supplier": supplier,
    })


# ---------------------------------------------------------------------------
# Detail with purchase history
# ---------------------------------------------------------------------------
@login_required
def supplier_detail(request, pk):
    _require_director(request.user)
    supplier = get_object_or_404(Supplier, pk=pk)

    # Recent receipts and invoices for this supplier
    from sales.models import StockReceipt, SupplierInvoice

    receipts = (StockReceipt.objects
                .filter(supplier=supplier)
                .select_related("product", "operating_point", "received_by")
                .order_by("-received_at")[:50])

    invoices = (SupplierInvoice.objects
                .filter(supplier=supplier)
                .order_by("-created_at")[:50])

    totals = SupplierInvoice.objects.filter(supplier=supplier).aggregate(
        billed=Sum("computed_total_amount"),
        paid=Sum("amount_paid"),
        due=Sum("balance_due"),
    )

    return render(request, "business/supplier_detail.html", {
        "supplier": supplier,
        "receipts": receipts,
        "invoices": invoices,
        "total_billed": totals["billed"] or 0,
        "total_paid": totals["paid"] or 0,
        "total_due": totals["due"] or 0,
    })


# ---------------------------------------------------------------------------
# Toggle active
# ---------------------------------------------------------------------------
@login_required
def supplier_toggle(request, pk):
    _require_director(request.user)
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    supplier = get_object_or_404(Supplier, pk=pk)
    supplier.is_active = not supplier.is_active
    supplier.save(update_fields=["is_active", "updated_at"])

    verb = "activated" if supplier.is_active else "deactivated"
    record_audit(
        user=request.user, action="UPDATE", obj=supplier,
        reason=f"Supplier {verb}",
        changes={"is_active": supplier.is_active},
    )
    messages.success(request, f"Supplier {supplier.name} {verb}.")
    return redirect("business:supplier_detail", pk=supplier.pk)