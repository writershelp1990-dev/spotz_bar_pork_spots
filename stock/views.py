"""Stock module views. All actions gated server-side."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import OperatingPoint
from catalog.models import Product

from .forms import (
    ProductionBatchForm, RecipeForm, RecipeIngredientLineForm,
    TransferRequestForm, WastageForm,
)
from .models import ProductionBatch, Recipe, StockTransfer
from .permissions import (
    is_active_staff, is_director, require_active_staff, require_director,
    stock_point_for, user_can_act_at_point, user_can_transfer_out_of,
)
from . import services as svc
from . import recipes as recipe_svc


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@require_active_staff
def dashboard(request):
    user = request.user
    is_dir = is_director(user)
    point = stock_point_for(user)

    if is_dir:
        pending_qs = StockTransfer.objects.filter(status=StockTransfer.Status.PENDING)
        approved_qs = StockTransfer.objects.filter(status=StockTransfer.Status.APPROVED)
        batch_qs = ProductionBatch.objects.all()
    else:
        pending_qs = StockTransfer.objects.filter(
            status=StockTransfer.Status.PENDING,
            source_point=point,
        )
        approved_qs = StockTransfer.objects.none()
        batch_qs = ProductionBatch.objects.filter(preparation_point=point)

    context = {
        "is_dir": is_dir,
        "point": point,
        "pending_count": pending_qs.count(),
        "approved_count": approved_qs.count() if is_dir else 0,
        "batch_count": batch_qs.count(),
        "recent_transfers": StockTransfer.objects.select_related(
            "product", "source_point", "destination_point", "requested_by"
        ).order_by("-requested_at")[:10],
        "recent_batches": batch_qs.select_related(
            "recipe", "preparation_point", "output_product", "produced_by"
        ).order_by("-produced_at")[:10],
    }
    return render(request, "stock/dashboard.html", context)


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------
@login_required
@require_active_staff
def transfer_list(request):
    user = request.user
    if is_director(user):
        qs = StockTransfer.objects.all()
    else:
        point = stock_point_for(user)
        qs = StockTransfer.objects.filter(
            Q(source_point=point) | Q(destination_point=point)
        ).distinct()
    qs = qs.select_related(
        "product", "source_point", "destination_point",
        "requested_by", "approved_by",
    ).order_by("-requested_at")
    return render(request, "stock/transfer_list.html", {"transfers": qs})


@login_required
@require_active_staff
def transfer_request(request):
    user = request.user
    if request.method == "POST":
        form = TransferRequestForm(request.POST, user=user)
        if form.is_valid():
            try:
                t = svc.create_transfer(
                    user=user,
                    product=form.cleaned_data["product"],
                    source_point=form.cleaned_data["source_point"],
                    destination_point=form.cleaned_data["destination_point"],
                    quantity=form.cleaned_data["quantity"],
                    reason=form.cleaned_data["reason"],
                    reference=form.cleaned_data["reference"],
                    transfer_type=form.cleaned_data["transfer_type"],
                )
                messages.success(request, f"Transfer {t.transfer_number} created (pending approval).")
                return redirect("stock:transfer_detail", pk=t.pk)
            except (PermissionDenied, ValidationError) as e:
                messages.error(request, str(e))
    else:
        form = TransferRequestForm(user=user)
    return render(request, "stock/transfer_request.html", {"form": form})


@login_required
@require_active_staff
def transfer_detail(request, pk):
    t = get_object_or_404(
        StockTransfer.objects.select_related(
            "product", "source_point", "destination_point",
            "requested_by", "approved_by", "completed_by", "unit",
        ),
        pk=pk,
    )
    user = request.user
    can_see = (
        is_director(user)
        or t.requested_by_id == user.pk
        or (stock_point_for(user) in (t.source_point, t.destination_point))
    )
    if not can_see:
        raise PermissionDenied("No access to this transfer.")
    return render(request, "stock/transfer_detail.html", {
        "transfer": t,
        "is_dir": is_director(user),
    })


@login_required
@require_director
def transfer_approve(request, pk):
    t = get_object_or_404(StockTransfer, pk=pk)
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    try:
        svc.approve_transfer(transfer=t, user=request.user)
        messages.success(request, f"Transfer {t.transfer_number} approved.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("stock:transfer_detail", pk=t.pk)


@login_required
@require_director
def transfer_complete(request, pk):
    t = get_object_or_404(StockTransfer, pk=pk)
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    try:
        svc.complete_transfer(transfer=t, user=request.user)
        messages.success(request, f"Transfer {t.transfer_number} completed.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("stock:transfer_detail", pk=t.pk)


@login_required
@require_director
def transfer_cancel(request, pk):
    t = get_object_or_404(StockTransfer, pk=pk)
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    reason = request.POST.get("reason", "").strip()
    try:
        svc.cancel_transfer(transfer=t, user=request.user, reason=reason)
        messages.success(request, f"Transfer {t.transfer_number} cancelled.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("stock:transfer_detail", pk=t.pk)


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------
@login_required
@require_director
def recipe_list(request):
    recipes = (
        Recipe.objects.select_related("product")
        .prefetch_related("items__ingredient", "items__unit")
        .order_by("product__name")
    )
    return render(request, "stock/recipe_list.html", {"recipes": recipes})


@login_required
@require_director
def recipe_create(request):
    # Products without a recipe yet
    candidate_products = (
        Product.objects.filter(is_active=True)
        .exclude(recipe__isnull=False)
        .order_by("name")
    )
    ingredients = (
        Product.objects.filter(is_active=True, track_inventory=True)
        .order_by("name")
    )
    from catalog.models import UnitOfMeasure
    units = UnitOfMeasure.objects.order_by("abbreviation")

    if request.method == "POST":
        product_id = request.POST.get("product")
        name = request.POST.get("name", "").strip()
        # Collect ingredient rows: ingredient_<idx>, quantity_<idx>, unit_<idx>
        items = []
        idx = 0
        while f"ingredient_{idx}" in request.POST:
            ing_id = request.POST.get(f"ingredient_{idx}")
            qty = request.POST.get(f"quantity_{idx}", "").strip()
            unit_id = request.POST.get(f"unit_{idx}")
            if ing_id and qty:
                try:
                    from decimal import Decimal, InvalidOperation
                    qty_d = Decimal(qty)
                except (InvalidOperation, ValueError):
                    messages.error(request, f"Row {idx+1}: invalid quantity.")
                    qty_d = None
                if qty_d is not None and qty_d > 0:
                    items.append({
                        "ingredient": Product.objects.get(pk=ing_id),
                        "quantity": qty_d,
                        "unit": UnitOfMeasure.objects.get(pk=unit_id),
                    })
            idx += 1
        if not product_id:
            messages.error(request, "Pick a product.")
        elif not items:
            messages.error(request, "Add at least one ingredient.")
        else:
            try:
                product = Product.objects.get(pk=product_id)
                r = recipe_svc.create_recipe(
                    product=product, name=name or product.display_name,
                    items=items, user=request.user,
                )
                messages.success(request, f"Recipe created for {product.display_name}.")
                return redirect("stock:recipe_list")
            except (PermissionDenied, ValidationError) as e:
                messages.error(request, str(e))

    return render(request, "stock/recipe_create.html", {
        "products": candidate_products,
        "ingredients": ingredients,
        "units": units,
        "ingredient_rows": range(8),  # 8 blank rows on the form
    })


# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------
@login_required
@require_active_staff
def batch_list(request):
    qs = ProductionBatch.objects.select_related(
        "recipe", "source_point", "preparation_point",
        "output_product", "produced_by",
    )
    if not is_director(request.user):
        qs = qs.filter(preparation_point=stock_point_for(request.user))
    return render(request, "stock/batch_list.html", {"batches": qs.order_by("-produced_at")[:200]})


@login_required
@require_active_staff
def batch_create(request):
    if request.method == "POST":
        form = ProductionBatchForm(request.POST, user=request.user)
        if form.is_valid():
            recipe = form.cleaned_data["recipe"]
            try:
                b = recipe_svc.create_production_batch(
                    user=request.user,
                    recipe=recipe,
                    source_point=form.cleaned_data["source_point"],
                    preparation_point=form.cleaned_data["preparation_point"],
                    quantity_produced=form.cleaned_data["quantity_produced"],
                    notes=form.cleaned_data["notes"],
                )
                messages.success(request, f"Batch {b.batch_number} recorded.")
                return redirect("stock:batch_list")
            except (PermissionDenied, ValidationError) as e:
                messages.error(request, str(e))
    else:
        form = ProductionBatchForm(user=request.user)
    return render(request, "stock/batch_create.html", {"form": form})


# ---------------------------------------------------------------------------
# Wastage
# ---------------------------------------------------------------------------
@login_required
@require_active_staff
def wastage_create(request):
    if request.method == "POST":
        form = WastageForm(request.POST)
        if form.is_valid():
            try:
                svc.record_wastage(
                    user=request.user,
                    product=form.cleaned_data["product"],
                    operating_point=form.cleaned_data["operating_point"],
                    quantity=form.cleaned_data["quantity"],
                    reason=form.cleaned_data["reason"],
                    reference=form.cleaned_data["reference"],
                )
                messages.success(request, "Wastage recorded.")
                return redirect("stock:dashboard")
            except (PermissionDenied, ValidationError) as e:
                messages.error(request, str(e))
    else:
        form = WastageForm()
    return render(request, "stock/wastage_create.html", {"form": form})


# ---------------------------------------------------------------------------
# Reconciliation report (Director)
# ---------------------------------------------------------------------------
@login_required
@require_director
def reconciliation(request):
    from sales.models import StockLedger
    from django.utils.dateparse import parse_date

    date_str = request.GET.get("date") or ""
    target_date = parse_date(date_str) if date_str else timezone.localdate()

    rows = []
    products = Product.objects.filter(is_active=True).order_by("name")
    points = OperatingPoint.objects.filter(is_active=True).order_by("code")

    for p in products:
        point_rows = []
        for op in points:
            day_movements = StockLedger.objects.filter(
                product=p, operating_point=op,
                created_at__date=target_date,
            ).order_by("created_at")
            if not day_movements.exists():
                continue
            opening = None
            summary = {}
            closing = None
            for m in day_movements:
                if opening is None:
                    opening = m.balance_after - m.quantity_delta
                summary[m.movement_type] = (
                    summary.get(m.movement_type, 0) + m.quantity_delta
                )
                closing = m.balance_after
            point_rows.append({
                "operating_point": op,
                "opening": opening,
                "closing": closing,
                "summary": summary,
            })
        if point_rows:
            rows.append({"product": p, "points": point_rows})

    return render(request, "stock/reconciliation.html", {
        "target_date": target_date,
        "rows": rows,
    })