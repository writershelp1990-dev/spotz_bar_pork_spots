"""Stock module views. All actions gated server-side."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import OperatingPoint, Employee
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
        pending_qs = StockTransfer.objects.filter(status=StockTransfer.Status.REQUESTED)
        approved_qs = StockTransfer.objects.filter(status=StockTransfer.Status.SENT)
        batch_qs = ProductionBatch.objects.all()
    else:
        pending_qs = StockTransfer.objects.filter(
            status=StockTransfer.Status.REQUESTED,
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
        "requested_by", "sent_by", "received_by",
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
            "requested_by", "sent_by", "received_by", "unit",
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

# =========================================================================
# Sender / recipient actions (Stage: recipient acknowledgement)
# =========================================================================
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone as _tz


@login_required
@require_active_staff
def transfer_accept(request, pk):
    """Sender confirms the shipment and enters the quantity sent."""
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    transfer = get_object_or_404(StockTransfer, pk=pk)
    qty = request.POST.get("quantity_sent", "").strip()
    if not qty:
        messages.error(request, "Quantity sent is required.")
        return redirect("stock:transfer_detail", pk=transfer.pk)
    try:
        svc.sender_accept(transfer=transfer, user=request.user, quantity_sent=qty)
        messages.success(request, f"Transfer {transfer.transfer_number} confirmed as sent.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("stock:transfer_detail", pk=transfer.pk)


@login_required
@require_active_staff
def transfer_sender_reject(request, pk):
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    transfer = get_object_or_404(StockTransfer, pk=pk)
    reason = request.POST.get("reason", "").strip()
    try:
        svc.sender_reject(transfer=transfer, user=request.user, reason=reason)
        messages.success(request, f"Transfer {transfer.transfer_number} rejected.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("stock:transfer_detail", pk=transfer.pk)


@login_required
@require_active_staff
def transfer_recipient_acknowledge(request, pk):
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    transfer = get_object_or_404(StockTransfer, pk=pk)
    qty = request.POST.get("quantity_received", "").strip()
    condition = request.POST.get("condition", "").strip()
    notes = request.POST.get("notes", "").strip()
    variance_reason = request.POST.get("variance_reason", "").strip()
    if not qty:
        messages.error(request, "Quantity received is required.")
        return redirect("stock:transfer_detail", pk=transfer.pk)
    try:
        svc.recipient_acknowledge(
            transfer=transfer, user=request.user,
            quantity_received=qty, condition=condition,
            notes=notes, variance_reason=variance_reason,
        )
        messages.success(request, f"Transfer {transfer.transfer_number} completed.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("stock:transfer_detail", pk=transfer.pk)


@login_required
@require_active_staff
def transfer_recipient_reject(request, pk):
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    transfer = get_object_or_404(StockTransfer, pk=pk)
    reason = request.POST.get("reason", "").strip()
    try:
        svc.recipient_reject(transfer=transfer, user=request.user, reason=reason)
        messages.success(request, f"Transfer {transfer.transfer_number} rejected.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("stock:transfer_detail", pk=transfer.pk)


# =========================================================================
# Popup API — pending transfer actions for the logged-in user
# =========================================================================
@login_required
@require_active_staff
def api_pending_transfers(request):
    """Return transfers where the current user must act.

    Sender: REQUESTED transfers from a source point the user can act on.
    Recipient: SENT transfers to a destination point the user can act on.
    """
    user = request.user
    out = []

    if is_director(user):
        # Directors see everything as informational (no required action).
        pending_requested = StockTransfer.objects.filter(
            status=StockTransfer.Status.REQUESTED)
        pending_sent = StockTransfer.objects.filter(
            status=StockTransfer.Status.SENT)
    else:
        point = stock_point_for(user)
        if point is None:
            return JsonResponse({"actions": []})
        pending_requested = StockTransfer.objects.filter(
            status=StockTransfer.Status.REQUESTED,
            source_point=point,
        )
        pending_sent = StockTransfer.objects.filter(
            status=StockTransfer.Status.SENT,
            destination_point=point,
        )

    for t in pending_requested.select_related(
            "product", "source_point", "destination_point", "unit"):
        out.append({
            "id": f"req-{t.pk}",
            "transfer_id": t.pk,
            "kind": "sender_accept",
            "title": f"New transfer request: {t.product.name}",
            "from_area": t.source_point.code,
            "to_area": t.destination_point.code,
            "quantity_requested": str(t.quantity),
            "unit": t.unit.abbreviation,
            "requested_by": t.requested_by.username if t.requested_by_id else "",
            "created_at": t.requested_at.isoformat(),
            "url": f"/stock/transfers/{t.pk}/",
            "accept_url": f"/stock/transfers/{t.pk}/accept/",
            "reject_url": f"/stock/transfers/{t.pk}/sender-reject/",
        })

    for t in pending_sent.select_related(
            "product", "source_point", "destination_point", "unit", "sent_by"):
        out.append({
            "id": f"sent-{t.pk}",
            "transfer_id": t.pk,
            "kind": "recipient_acknowledge",
            "title": f"Awaiting receipt: {t.product.name}",
            "from_area": t.source_point.code,
            "to_area": t.destination_point.code,
            "quantity_sent": str(t.quantity_sent),
            "unit": t.unit.abbreviation,
            "sent_by": t.sent_by.username if t.sent_by_id else "",
            "deadline": t.acknowledgement_deadline.isoformat() if t.acknowledgement_deadline else "",
            "url": f"/stock/transfers/{t.pk}/",
            "accept_url": f"/stock/transfers/{t.pk}/acknowledge/",
            "reject_url": f"/stock/transfers/{t.pk}/recipient-reject/",
        })

    return JsonResponse({"actions": out})

# ---------------------------------------------------------------------------
# Point operations dashboard — one page per operating point
# ---------------------------------------------------------------------------
from datetime import timedelta as _pd_td
from django.utils import timezone as _pd_tz
from django.db.models import Sum as _pd_Sum, Count as _pd_Count
from business.business_day_models import (
    BusinessDay as _pd_BusinessDay,
    StaffDeployment as _pd_StaffDeployment,
)
from sales.models import (
    StockBalance as _pd_StockBalance,
    StockLedger as _pd_StockLedger,
    StockAlert as _pd_StockAlert,
    StockAlertRule as _pd_StockAlertRule,
    Sale as _pd_Sale,
    SaleItem as _pd_SaleItem,
)
from accounts.models import OperatingPoint as _pd_OperatingPoint
from business.business_day_models import PointDay as _pd_PointDay


@login_required
@require_active_staff
def point_detail(request, code):
    """Read-only operational dashboard for one operating point."""
    point = get_object_or_404(_pd_OperatingPoint, code__iexact=code)
    user = request.user

    if not is_director(user):
        own = stock_point_for(user)
        if own is None or own.pk != point.pk:
            raise PermissionDenied("You can only view your own operating point.")

    open_day = _pd_BusinessDay.objects.filter(
        status=_pd_BusinessDay.Status.OPEN).first()
    if open_day:
        day_state = "OPEN"
        day_date = open_day.business_date
        day_timestamp = open_day.actual_opened_at
    else:
        latest = _pd_BusinessDay.objects.order_by("-business_date").first()
        if latest:
            day_state = "CLOSED"
            day_date = latest.business_date
            day_timestamp = latest.actual_closed_at
        else:
            day_state = "NONE"
            day_date = None
            day_timestamp = None

    deployed = []
    if open_day:
        deployed = list(
            _pd_StaffDeployment.objects
            .filter(
                business_day=open_day,
                operating_point=point,
                status=_pd_StaffDeployment.Status.ACTIVE,
            )
            .select_related("employee__user")
            .order_by("start_time", "employee__user__first_name")
        )

    assigned = list(
        Employee.objects
        .filter(operating_point=point, is_active=True)
        .select_related("user")
        .order_by("user__first_name", "user__username")[:20]
    )

    sales_qs = _pd_Sale.objects.filter(operating_point=point)
    if open_day:
        sales_qs = sales_qs.filter(business_day=open_day)
    else:
        sales_qs = sales_qs.filter(
            sold_at__gte=_pd_tz.now() - _pd_td(hours=24))
    sales_qs = sales_qs.filter(payment_status=_pd_Sale.PaymentStatus.PAID)

    totals = sales_qs.aggregate(
        count=_pd_Count("id"),
        total=_pd_Sum("total"),
    )
    sales_count = totals["count"] or 0
    sales_total = totals["total"] or 0

    payment_mix = list(
        sales_qs.values("payment_method")
        .annotate(count=_pd_Count("id"), total=_pd_Sum("total"))
        .order_by("-total")
    )

    top_sellers = list(
        _pd_SaleItem.objects
        .filter(sale__in=sales_qs)
        .values("product_name_snapshot", "unit_name_snapshot")
        .annotate(qty=_pd_Sum("quantity"), revenue=_pd_Sum("line_total"))
        .order_by("-qty")[:5]
    )

    balances = list(
        _pd_StockBalance.objects
        .filter(operating_point=point, quantity__gt=0)
        .select_related("product")
        .order_by("-quantity")[:8]
    )

    rules = (
        _pd_StockAlertRule.objects
        .filter(operating_point=point, is_active=True)
        .select_related("product")
    )
    balance_map = {
        b.product_id: b.quantity
        for b in _pd_StockBalance.objects.filter(operating_point=point)
    }
    low_stock = []
    for rule in rules:
        have = balance_map.get(rule.product_id, 0)
        if have < rule.minimum_quantity:
            low_stock.append({
                "product": rule.product,
                "have": have,
                "minimum": rule.minimum_quantity,
                "target": rule.target_quantity,
            })

    active_alerts = list(
        _pd_StockAlert.objects
        .filter(operating_point=point, status=_pd_StockAlert.Status.ACTIVE)
        .select_related("product")
        .order_by("-triggered_at")[:5]
    )

    recent_movements = list(
        _pd_StockLedger.objects
        .filter(operating_point=point)
        .select_related("product", "created_by")
        .order_by("-created_at")[:8]
    )

    return render(request, "stock/point_detail.html", {
        "point": point,
        "is_dir": is_director(user),
        "day_state": day_state,
        "day_date": day_date,
        "day_timestamp": day_timestamp,
        "deployed": deployed,
        "assigned": assigned,
        "sales_count": sales_count,
        "sales_total": sales_total,
        "payment_mix": payment_mix,
        "top_sellers": top_sellers,
        "balances": balances,
        "low_stock": low_stock,
        "active_alerts": active_alerts,
                "point_day_state": (
            "OPEN" if (_pd_PointDay.objects.filter(
                business_day=open_day, operating_point=point,
                closed_at__isnull=True).exists())
            else ("CLOSED" if (_pd_PointDay.objects.filter(
                business_day=open_day, operating_point=point).exists())
            else None)
        ),        "recent_movements": recent_movements,
    })