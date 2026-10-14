from django.conf import settings
from django.db import transaction
from decimal import Decimal, InvalidOperation
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.http import JsonResponse
import json
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from audit.services import record_audit
from catalog.models import Category, Product, ProductPriceHistory, UnitOfMeasure
from accounts.models import OperatingPoint
from .forms import CategoryForm, UnitForm, ProductForm, SellingPriceForm, ReceiptForm, CheckoutForm
from .models import (
    Sale,
    StockBalance,
    StockLedger,
    MpesaPayment,
    MpesaPaymentAllocation,
)
from .mpesa import (
    initiate_stk_push,
    parse_callback_payload,
    apply_callback,
)
from .services import (
    checkout, receive_stock, is_director, employee_point, require_active_user,
    current_selling_price
)

def director_required(user):
    require_active_user(user)
    if not is_director(user):
        raise PermissionDenied("Director access is required.")

@login_required
def pos(request):
    require_active_user(request.user)
    form = CheckoutForm(request.POST or None)
    assigned = employee_point(request.user)
    if not is_director(request.user) and not assigned:
        raise PermissionDenied("Your staff profile needs an assigned operating point.")
    if assigned and not is_director(request.user):
        form.fields["operating_point"].queryset = OperatingPoint.objects.filter(pk=assigned.pk, is_active=True)
        form.fields["operating_point"].initial = assigned.pk

    products = Product.objects.filter(is_active=True).select_related("category", "selling_unit")
    now = timezone.now()
    product_rows = []
    for p in products:
        try:
            price = current_selling_price(p, now)
        except ValidationError:
            continue
        product_rows.append({
            "id": p.pk, "name": p.display_name, "code": p.code,
            "unit": p.selling_unit.abbreviation, "price": str(price),
            "track_inventory": p.track_inventory
        })

    if request.method == "POST" and form.is_valid():
        try:
            # Wrap the entire commit sequence in one transaction so that
            # any failure in the M-Pesa step rolls back the sale creation
            # (no orphaned sales or deducted stock).
            with transaction.atomic():
                sale = checkout(
                    user=request.user,
                    operating_point=form.cleaned_data["operating_point"],
                    payment_method=form.cleaned_data["payment_method"],
                    amount_tendered=form.cleaned_data["amount_tendered"],
                    items_json=form.cleaned_data["items_json"],
                )

                # --- Hold as unpaid ---
                # The cashier saved the sale without payment.
                # The sale is created (PENDING) and can be settled later.
                if form.cleaned_data.get("hold_as_unpaid"):
                    sale.completed = False
                    sale.payment_status = Sale.PaymentStatus.PENDING
                    sale.save(update_fields=["completed", "payment_status"])
                    messages.success(
                        request,
                        f"Sale {sale.sale_number} held as unpaid.",
                    )
                    return redirect("sales:pos")

                # M-Pesa: redirect to the STK-push screen instead of completing
                # ---- Manual M-Pesa (one or more codes, possibly batch) ----
                if sale.payment_method == Sale.PaymentMethod.MPESA_MANUAL:
                    primary_payment = _complete_manual_mpesa_sale(
                        sale=sale,
                        form=form,
                        request=request,
                    )
                    # If batch (multiple sales covered), show batch receipt.
                    if primary_payment and primary_payment.allocations.count() > 1:
                        return redirect("sales:batch_receipt", payment_id=primary_payment.pk)
                    return redirect("sales:receipt", sale_id=sale.pk)

                if sale.payment_method == Sale.PaymentMethod.MPESA:
                    sale.customer_phone = (request.POST.get("phone_number") or "").strip()
                    sale.save(update_fields=["customer_phone"])
                    return redirect("sales:mpesa_pay", sale_id=sale.pk)

                # Cash: complete immediately
                sale.completed = True
                sale.payment_status = Sale.PaymentStatus.PAID
                sale.save(update_fields=["completed", "payment_status"])
                messages.success(
                    request,
                    f"Sale {sale.sale_number} completed. "
                    f"Total KES {sale.total}; change KES {sale.change_due}.",
                )
                return redirect("sales:receipt", sale_id=sale.pk)


        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, getattr(exc, "message", str(exc)))
    return render(request, "sales/pos.html", {
        "form": form, "products_json": json.dumps(product_rows)
    })

@login_required
def receipt(request, sale_id):
    require_active_user(request.user)
    sale = get_object_or_404(
        Sale.objects.prefetch_related(
            "items",
            "mpesa_allocations__payment",
            "mpesa_transactions",
        ),
        pk=sale_id,
        completed=True,
    )
    if not is_director(request.user) and sale.cashier_id != request.user.pk:
        raise PermissionDenied("You cannot view another cashier's receipt.")

    # Compute the cash portion of the payment for split M-Pesa + cash sales.
    mpesa_total = sum(
        (a.amount for a in sale.mpesa_allocations.all()),
        start=Decimal("0.00"),
    )
    cash_paid = max(Decimal("0.00"), (sale.amount_tendered or Decimal("0.00")) - mpesa_total)

    return render(request, "sales/receipt.html", {
        "sale": sale,
        "mpesa_total": mpesa_total,
        "cash_paid": cash_paid,
    })

@login_required
def catalog_home(request):
    director_required(request.user)

    products = list(
        Product.objects.select_related(
            "category", "selling_unit", "size_unit"
        ).order_by("name")
    )
    now = timezone.now()
    for product in products:
        try:
            product.current_price = current_selling_price(product, now)
        except ValidationError:
            product.current_price = None

    recent_prices = ProductPriceHistory.objects.filter(
        price_type=ProductPriceHistory.PriceType.SELLING
    ).select_related("product", "unit").order_by(
        "-effective_from", "-id"
    )[:20]

    return render(request, "sales/catalog.html", {
        "products": products,
        "recent_prices": recent_prices,
    })

@login_required
def product_add(request):
    director_required(request.user)
    form = ProductForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        product = form.save()
        record_audit(
            request=request,
            user=request.user,
            action="CREATE",
            obj=product,
            reason="Product created through catalogue management.",
            changes={
                "name": product.name,
                "code": product.code,
                "brand": product.brand,
                "variety": product.variety,
                "packaging": product.packaging,
                "size_value": product.size_value,
                "size_unit": str(product.size_unit or ""),
            },
        )
        messages.success(
            request,
            f"Product {product.name} created with code {product.code}. "
            "Add its selling price before checkout.",
        )
        return redirect("sales:price_add")

    return render(request, "sales/form.html", {
        "form": form,
        "heading": "Add product",
    })
def _norm_audit_value(value):
    """
    Normalize a value for audit-diff comparison.

    The audit `changes` dict must be empty for a no-op edit. Values that
    are numerically equal but represented differently would otherwise
    produce a spurious diff:
        - Decimal('500') vs Decimal('500.000')
        - Decimal('500') vs '500' (stringified Decimal from a form)
        - 500 (int) vs Decimal('500')
    Normalizing both sides to the same canonical form avoids this while
    leaving the original values untouched in the stored audit record.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        # Try to compare integers and Decimals on the same footing
        try:
            return Decimal(value).normalize()
        except Exception:
            return value
    if isinstance(value, str):
        # Strings that look like numbers should compare numerically
        s = value.strip()
        if s == "":
            return ""
        try:
            return Decimal(s).normalize()
        except Exception:
            return value
    return value


@login_required
def product_edit(request, product_id):
    director_required(request.user)
    product = get_object_or_404(Product, pk=product_id)
    # Snapshot persisted values before ModelForm validation; validation can mutate the instance.
    old_values = {
        "name": product.name,
        "brand": product.brand,
        "variety": product.variety,
        "packaging": product.packaging,
        "size_value": str(product.size_value or ""),
        "size_unit_id": product.size_unit_id,
        "category_id": product.category_id,
        "product_type": product.product_type,
        "purchase_unit_id": product.purchase_unit_id,
        "selling_unit_id": product.selling_unit_id,
        "is_active": product.is_active,
        "track_inventory": product.track_inventory,
    }

    form = ProductForm(request.POST or None, instance=product)

    if request.method == "POST" and form.is_valid():
        saved = form.save()
        new_values = {
            "name": saved.name,
            "brand": saved.brand,
            "variety": saved.variety,
            "packaging": saved.packaging,
            "size_value": saved.size_value,
            "size_unit_id": saved.size_unit_id,
            "category_id": saved.category_id,
            "product_type": saved.product_type,
            "purchase_unit_id": saved.purchase_unit_id,
            "selling_unit_id": saved.selling_unit_id,
            "is_active": saved.is_active,
            "track_inventory": saved.track_inventory,
        }
        changes = {
            key: {"old": old_values[key], "new": new_values[key]}
            for key in old_values
            if _norm_audit_value(old_values[key]) != _norm_audit_value(new_values[key])
        }
        if changes:
            record_audit(
                request=request,
                user=request.user,
                action="UPDATE",
                obj=saved,
                reason="Product updated through catalogue management.",
                changes=changes,
            )
        messages.success(request, f"Product {saved.name} updated.")
        return redirect("sales:catalog")

    return render(request, "sales/form.html", {
        "form": form,
        "heading": f"Edit product: {product.name}",
    })

@login_required
def category_add(request):
    director_required(request.user)
    form = CategoryForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        category = form.save()
        record_audit(
            request=request, user=request.user, action="CREATE",
            obj=category, reason="Catalogue category created.",
            changes={"name": category.name},
        )
        messages.success(request, "Category saved.")
        return redirect("sales:catalog")

    return render(request, "sales/form.html", {
        "form": form, "heading": "Add category"
    })

@login_required
def unit_add(request):
    director_required(request.user)
    form = UnitForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        unit = form.save()
        record_audit(
            request=request, user=request.user, action="CREATE",
            obj=unit, reason="Unit of measure created.",
            changes={
                "name": unit.name,
                "abbreviation": unit.abbreviation,
                "unit_type": unit.unit_type,
            },
        )
        messages.success(request, "Unit saved.")
        return redirect("sales:catalog")

    return render(request, "sales/form.html", {
        "form": form, "heading": "Add unit of measure"
    })

@login_required
def price_add(request):
    director_required(request.user)
    form = SellingPriceForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        price = form.save()
        record_audit(
            request=request, user=request.user, action="CREATE",
            obj=price, reason="Selling price history entry created.",
            changes={
                "product_id": price.product_id,
                "price": str(price.price),
                "price_type": price.price_type,
                "unit": price.unit.abbreviation,
                "effective_from": price.effective_from.isoformat(),
            },
        )
        messages.success(request, f"Selling price saved for {price.product.display_name}.")
        return redirect("sales:catalog")

    return render(request, "sales/form.html", {
        "form": form, "heading": "Add selling price"
    })

def allowed_stock_points(user):
    """Return the operating points a user may see stock for.

    Director: all active points.
    Staff: only their Employee.operating_point, if employment status
    permits (ACTIVE or OFF_DUTY).
    Others: empty list.
    """
    from accounts.models import Employee
    try:
        from accounts.employee_management import effective_employment_status
    except ImportError:
        from accounts.models import effective_employment_status  # fallback
    if not user.is_authenticated:
        return []
    if getattr(user, "is_director", False):
        return list(OperatingPoint.objects.filter(is_active=True).order_by("name"))
    if not getattr(user, "is_active_staff_user", False):
        return []
    emp = getattr(user, "employee_profile", None)
    if emp is None or emp.operating_point_id is None:
        return []
    status = effective_employment_status(emp)
    if status not in {
        Employee.EmploymentStatus.ACTIVE,
        Employee.EmploymentStatus.OFF_DUTY,
    }:
        return []
    if not emp.operating_point.is_active:
        return []
    return [emp.operating_point]


@login_required
def stock_home(request):
    if not (getattr(request.user, "is_director", False) or getattr(request.user, "is_active_staff_user", False)):
        raise PermissionDenied("Active staff account required.")

    points = allowed_stock_points(request.user)

    # Include all products in the lookup so historical stock for an
    # inactive product is not silently hidden.
    products = list(
        Product.objects.select_related(
            "category", "selling_unit", "size_unit"
        ).order_by("category__name", "name", "brand", "variety")
    )
    product_map = {product.pk: product for product in products}

    balances = StockBalance.objects.filter(
        operating_point__in=points,
    ).select_related("product", "operating_point")

    balance_map = {
        (balance.operating_point_id, balance.product_id): balance
        for balance in balances
    }

    # Existing balance records remain visible even if an assignment
    # has not yet been reviewed. No balances or ledger rows are changed.
    balance_product_ids = {}
    for balance in balances:
        balance_product_ids.setdefault(balance.operating_point_id, set()).add(
            balance.product_id
        )

    # --- Load stock alert rules for the restock status column ---
    from sales.models import StockAlertRule as _SAR
    _rules_qs = (
        _SAR.objects
        .filter(is_active=True, operating_point__in=points)
        .select_related('product')
    )
    _rule_map = {(r.operating_point_id, r.product_id): r for r in _rules_qs}

    inventory_sections = []
    for point in points:
        assigned_ids = set(
            Product.objects.filter(
                is_active=True,
                operating_points=point,
            ).values_list("pk", flat=True)
        )
        visible_ids = assigned_ids | balance_product_ids.get(point.pk, set())

        point_products = [
            product for product in products if product.pk in visible_ids
        ]

        rows = []
        for product in point_products:
            balance = balance_map.get((point.pk, product.pk))
            # Determine restock status for this row
            _rule = _rule_map.get((point.pk, product.pk))
            _qty = balance.quantity if balance else 0
            if _rule is None:
                _status = {'state': 'unmonitored', 'label': 'Not monitored', 'rule': None}
            elif _qty <= _rule.minimum_quantity:
                _status = {'state': 'restock', 'label': 'Restock needed', 'rule': _rule}
            elif _qty <= _rule.minimum_quantity * Decimal('1.25'):
                _status = {'state': 'low', 'label': 'Low', 'rule': _rule}
            else:
                _status = {'state': 'ok', 'label': 'OK', 'rule': _rule}

            rows.append({
                "product": product,
                "quantity": balance.quantity if balance else 0,
                "updated_at": balance.updated_at if balance else None,
                "is_zero": balance is None or balance.quantity == 0,
                "restock_status": _status,
            })

        inventory_sections.append({
            "point": point,
            "rows": rows,
            "product_count": len(rows),
            "zero_count": sum(1 for row in rows if row["is_zero"]),
        })

    # --- Restocking alerts (Director-visible low-stock summary) ---
    restock_alerts = []
    for _r in _rules_qs:
        _bal = (
            StockBalance.objects
            .filter(product=_r.product, operating_point=_r.operating_point)
            .values_list('quantity', flat=True)
            .first()
        )
        _bal = _bal if _bal is not None else 0
        if _bal <= _r.minimum_quantity:
            restock_alerts.append({
                'product': _r.product,
                'operating_point': _r.operating_point,
                'balance': _bal,
                'minimum': _r.minimum_quantity,
                'target': _r.target_quantity,
                'shortfall': max(_r.target_quantity - _bal, 0),
            })
    restock_alerts.sort(
        key=lambda r: (r['operating_point'].code, r['product'].name)
    )

    return render(request, "sales/stock.html", {
        "inventory_sections": inventory_sections,
        "restock_alerts": restock_alerts,
    })

@login_required
def stock_receive(request):
    """Receive stock.

    Director submissions apply immediately. Attendant submissions create
    a PENDING StockReceipt and require Director verification before any
    balance change.
    """
    from .services import record_delivery
    from .forms import ReceiptForm

    user = request.user
    if not (getattr(user, "is_director", False)
            or getattr(user, "is_active_staff_user", False)):
        raise PermissionDenied("Active staff account required.")

    if request.method == "POST":
        form = ReceiptForm(request.POST, user=user)
        if form.is_valid():
            try:
                receipt = record_delivery(
                    user=user,
                    product=form.cleaned_data["product"],
                    operating_point=form.cleaned_data["operating_point"],
                    quantity=form.cleaned_data["quantity"],
                    unit=form.cleaned_data["product"].selling_unit,
                    unit_cost=form.cleaned_data["unit_cost"],
                    supplier=form.cleaned_data["supplier"],
                    invoice_number=form.cleaned_data.get("invoice_number", ""),
                    invoice_date=form.cleaned_data.get("invoice_date"),
                    notes=form.cleaned_data.get("notes", ""),
                    reference=form.cleaned_data.get("reference", ""),
                )
                if getattr(user, "is_director", False):
                    messages.success(
                        request,
                        f"Delivery applied. Receipt {receipt.receipt_number} "
                        f"recorded and stock updated.")
                else:
                    messages.success(
                        request,
                        f"Delivery recorded as PENDING. Receipt "
                        f"{receipt.receipt_number} awaits Director verification.")
                return redirect("sales:stock")
            except (PermissionDenied, ValidationError) as e:
                messages.error(request, str(e))
    else:
        form = ReceiptForm(user=user)

    return render(request, "sales/stock_receive.html", {
        "form": form,
        "is_director": getattr(user, "is_director", False),
    })


@login_required
def pending_receipts_list(request):
    """List pending deliveries.

    Director sees all pending receipts.
    Attendant sees their own pending receipts only.
    """
    from .models import StockReceipt

    user = request.user
    if not (getattr(user, "is_director", False)
            or getattr(user, "is_active_staff_user", False)):
        raise PermissionDenied("Active staff account required.")

    qs = (StockReceipt.objects
          .select_related("product", "operating_point", "supplier",
                          "supplier_invoice", "received_by", "verified_by"))
    if getattr(user, "is_director", False):
        pending = qs.filter(status=StockReceipt.Status.PENDING)
        recent = qs.exclude(status=StockReceipt.Status.PENDING)[:50]
    else:
        pending = qs.filter(status=StockReceipt.Status.PENDING, received_by=user)
        recent = qs.filter(received_by=user).exclude(
            status=StockReceipt.Status.PENDING)[:50]

    return render(request, "sales/pending_receipts.html", {
        "pending": pending.order_by("-received_at"),
        "recent": recent,
        "is_director": getattr(user, "is_director", False),
    })


@login_required
def receipt_detail(request, pk):
    """Show one receipt. Directors see verify / reject forms."""
    from .models import StockReceipt

    user = request.user
    receipt = get_object_or_404(
        StockReceipt.objects.select_related(
            "product", "operating_point", "unit", "supplier",
            "supplier_invoice", "received_by", "verified_by",
            "resulting_movement",
        ),
        pk=pk,
    )

    is_dir = getattr(user, "is_director", False)
    is_owner = receipt.received_by_id == user.pk
    if not (is_dir or is_owner):
        raise PermissionDenied("No access to this receipt.")

    return render(request, "sales/receipt_detail.html", {
        "receipt": receipt,
        "is_director": is_dir,
    })


@login_required
def receipt_verify(request, pk):
    """Director-only: verify a pending receipt."""
    from .models import StockReceipt
    from .services import verify_delivery

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may verify deliveries.")
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    receipt = get_object_or_404(StockReceipt, pk=pk)
    notes = request.POST.get("notes", "").strip()
    try:
        verify_delivery(user=request.user, receipt=receipt, notes=notes)
        messages.success(
            request,
            f"Receipt {receipt.receipt_number} verified. Stock updated.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("sales:pending_receipts")


@login_required
def receipt_reject(request, pk):
    """Director-only: reject a pending receipt with a reason."""
    from .models import StockReceipt
    from .services import reject_delivery

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may reject deliveries.")
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    receipt = get_object_or_404(StockReceipt, pk=pk)
    notes = request.POST.get("notes", "").strip()
    if not notes:
        messages.error(request, "A rejection reason is required.")
        return redirect("sales:receipt_detail", pk=receipt.pk)

    try:
        reject_delivery(user=request.user, receipt=receipt, notes=notes)
        messages.success(
            request, f"Receipt {receipt.receipt_number} rejected.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("sales:pending_receipts")
@login_required
def sales_history(request):
    require_active_user(request.user)
    sales = Sale.objects.filter(completed=True).select_related("cashier", "operating_point")
    if not is_director(request.user):
        sales = sales.filter(cashier=request.user)
    return render(request, "sales/history.html", {"sales": sales[:200]})

# ===========================================================================
# M-Pesa STK Push views
# ===========================================================================
import logging as _logging

_mpesa_logger = _logging.getLogger(__name__)


@login_required
def mpesa_pay(request, sale_id):
    """
    Initiate an STK push for a sale.

    - GET: the phone was captured by the POS form and stored on the sale.
           We initiate the push using that saved phone number.
    - POST: legacy path (form resubmits phone_number directly).
    """
    require_active_user(request.user)
    if not settings.MPESA_STK_PUSH_ENABLED:
        messages.error(
            request,
            "STK Push is disabled until the till is registered and integration is configured. "
            "Use Customer-Paid M-Pesa and verify the receipt against the actual transaction."
        )
        return redirect("sales:pos")

    sale = get_object_or_404(Sale, pk=sale_id, completed=False)

    if not is_director(request.user) and sale.cashier_id != request.user.pk:
        raise PermissionDenied("You cannot pay for another cashier's sale.")

    if request.method == "POST":
        phone = (request.POST.get("phone_number") or "").strip()
        if phone:
            sale.customer_phone = phone
            sale.save(update_fields=["customer_phone"])
    else:
        phone = (sale.customer_phone or "").strip()

    if not phone:
        messages.error(
            request,
            "No M-Pesa phone number was captured for this sale.",
        )
        return redirect("sales:pos")

    try:
        initiate_stk_push(sale=sale, phone_number=phone)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("sales:pos")
    except Exception as exc:
        _mpesa_logger.exception("M-Pesa STK push failed")
        messages.error(request, f"M-Pesa error: {exc}")
        return redirect("sales:pos")

    return redirect("sales:mpesa_wait", sale_id=sale.pk)


@login_required
def mpesa_wait(request, sale_id):
    """Waiting page that polls every 3 seconds for payment status."""
    require_active_user(request.user)
    sale = get_object_or_404(Sale, pk=sale_id)

    if not is_director(request.user) and sale.cashier_id != request.user.pk:
        raise PermissionDenied("You cannot view another cashier's sale.")

    latest = sale.mpesa_transactions.order_by("-created_at").first()
    return render(request, "sales/mpesa_wait.html", {
        "sale": sale,
        "mpesa": latest,
    })


@login_required
def mpesa_status(request, sale_id):
    """JSON endpoint polled by the waiting page."""
    require_active_user(request.user)
    sale = get_object_or_404(Sale, pk=sale_id)

    if not is_director(request.user) and sale.cashier_id != request.user.pk:
        raise PermissionDenied()

    latest = sale.mpesa_transactions.order_by("-created_at").first()
    if latest is None:
        return JsonResponse({"status": "NO_TRANSACTION"})

    return JsonResponse({
        "status": latest.status,
        "payment_status": sale.payment_status,
        "receipt_number": latest.mpesa_receipt_number,
        "result_description": latest.result_description,
        "redirect_to": (
            reverse("sales:receipt", args=[sale.pk])
            if sale.payment_status == Sale.PaymentStatus.PAID
            else None
        ),
    })


@csrf_exempt
def mpesa_callback(request):
    """Safaricom POSTs here when the customer finishes entering their PIN."""
    if request.method != "POST":
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Only POST allowed"},
            status=405,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _mpesa_logger.warning("M-Pesa callback: invalid JSON")
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Invalid JSON"},
            status=400,
        )

    _mpesa_logger.info(
        "M-Pesa callback received: %s", json.dumps(payload)[:500]
    )

    parsed = parse_callback_payload(payload)
    if not parsed.get("checkout_request_id"):
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Missing CheckoutRequestID"},
            status=400,
        )

    from .models import MpesaTransaction
    try:
        mpesa = MpesaTransaction.objects.get(
            checkout_request_id=parsed["checkout_request_id"]
        )
    except MpesaTransaction.DoesNotExist:
        _mpesa_logger.warning(
            "M-Pesa callback: no matching MpesaTransaction for %s",
            parsed["checkout_request_id"],
        )
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Unknown transaction"}
        )

    mpesa.raw_callback = payload
    apply_callback(mpesa_transaction=mpesa, parsed=parsed)

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ===========================================================================
# Manual M-Pesa payment helper
# ===========================================================================
def _complete_manual_mpesa_sale(*, sale, form, request):
    """
    Apply one or more M-Pesa codes to a sale (and optionally to other open
    sales selected as a batch). Enforces all fraud-prevention rules:

      R1: sum(M-Pesa codes) <= primary sale total
      R2: sum(M-Pesa) + cash >= sale total
      R3: change only from cash
      R4: if M-Pesa == total, cash must be 0
      R5: code globally unique
      R6: no duplicate code within the same submission
      R7: batch payments must be fully M-Pesa
      R8: batch codes must cover the primary + batch total exactly
    """
    from decimal import Decimal as D
    from django.utils import timezone

    codes = form.cleaned_data.get("mpesa_codes") or []
    mpesa_total = form.cleaned_data.get("mpesa_total") or D("0.00")
    cash_amount = form.cleaned_data.get("mpesa_cash_amount") or D("0.00")
    batch_ids = form.cleaned_data.get("batch_sale_ids") or []

    # ---- Load any batch sales (same cashier, recent, still pending) ----
    batch_sales = []
    if batch_ids:
        cutoff = timezone.now() - timezone.timedelta(minutes=30)
        batch_sales = list(
            Sale.objects
            .select_for_update()
            .filter(
                pk__in=batch_ids,
                cashier=request.user,
                completed=False,
                payment_status=Sale.PaymentStatus.PENDING,
                sold_at__gte=cutoff,
            )
        )
        if len(batch_sales) != len(set(batch_ids)):
            raise ValidationError(
                "One or more selected batch sales are no longer available."
            )
        if sale.pk in {s.pk for s in batch_sales}:
            batch_sales = [s for s in batch_sales if s.pk != sale.pk]

    # ---- R7: batch must be fully M-Pesa (no cash) ----
    if batch_sales and cash_amount > 0:
        raise ValidationError(
            "Batch payments cannot include a cash component. "
            "Pay in full via M-Pesa or handle each sale individually."
        )

    # ---- R1: M-Pesa total <= primary sale total (when not a batch) ----
    if not batch_sales and mpesa_total > sale.total:
        raise ValidationError(
            f"Total M-Pesa (KES {mpesa_total}) cannot exceed the sale total "
            f"(KES {sale.total}). Refunding M-Pesa as cash is not permitted."
        )

    # ---- R4: if M-Pesa == total and not batch, cash must be 0 ----
    if not batch_sales and mpesa_total == sale.total and cash_amount > 0:
        raise ValidationError(
            f"M-Pesa already covers the full sale total of KES {sale.total}. "
            "No cash payment is needed for this sale."
        )

    # ---- R2: total paid (M-Pesa + cash) >= sale total (when not a batch) ----
    total_paid = mpesa_total + cash_amount
    if not batch_sales and total_paid < sale.total:
        remaining = (sale.total - total_paid).quantize(D("0.01"))
        raise ValidationError(
            f"Total paid (KES {total_paid}) is less than the sale total "
            f"(KES {sale.total}). Remaining: KES {remaining}."
        )

    # ---- R8: batch total must match M-Pesa total exactly ----
    if batch_sales:
        batch_total = sum((s.total for s in batch_sales), D("0.00"))
        expected = (sale.total + batch_total).quantize(D("0.01"))
        if mpesa_total != expected:
            raise ValidationError(
                f"M-Pesa total (KES {mpesa_total}) does not match the sum "
                f"of the selected sales (KES {expected})."
            )

    # ---- R5: each code must be globally unique ----
    for entry in codes:
        if MpesaPayment.objects.filter(receipt_code=entry["code"]).exists():
            raise ValidationError(
                f"M-Pesa receipt {entry['code']} has already been recorded."
            )

    # ---- Create MpesaPayment rows and allocate to target sales ----
    all_target_sales = [sale] + batch_sales
    allocations_by_sale = {s.pk: [] for s in all_target_sales}
    remaining_per_sale = {s.pk: s.total for s in all_target_sales}

    primary_payment = None

    for entry in codes:
        payment = MpesaPayment.objects.create(
            receipt_code=entry["code"],
            total_amount=entry["amount"],
            customer_phone="",
            recorded_by=request.user,
        )
        if primary_payment is None:
            primary_payment = payment

        remaining_code = entry["amount"]
        for target in all_target_sales:
            if remaining_code <= 0:
                break
            take = min(remaining_code, remaining_per_sale[target.pk])
            if take <= 0:
                continue
            MpesaPaymentAllocation.objects.create(
                payment=payment,
                sale=target,
                amount=take.quantize(D("0.01")),
            )
            allocations_by_sale[target.pk].append(take)
            remaining_per_sale[target.pk] = (
                remaining_per_sale[target.pk] - take
            ).quantize(D("0.01"))
            remaining_code = (remaining_code - take).quantize(D("0.01"))

    # ---- Compute change for the primary sale ----
    primary_allocated = sum(allocations_by_sale[sale.pk], D("0.00"))
    cash_needed = max(
        D("0.00"), (sale.total - primary_allocated).quantize(D("0.01"))
    )
    change_due = max(
        D("0.00"), (cash_amount - cash_needed).quantize(D("0.01"))
    )

    # ---- Complete the primary sale ----
    sale.amount_tendered = (primary_allocated + cash_amount).quantize(D("0.01"))
    sale.change_due = change_due
    sale.completed = True
    sale.payment_status = Sale.PaymentStatus.PAID
    sale.save(update_fields=[
        "completed", "payment_status", "amount_tendered", "change_due",
    ])

    # ---- Complete the batch sales (fully M-Pesa) ----
    for target in batch_sales:
        target_allocated = sum(allocations_by_sale[target.pk], D("0.00"))
        target.amount_tendered = target_allocated
        target.change_due = D("0.00")
        target.completed = True
        target.payment_status = Sale.PaymentStatus.PAID
        target.save(update_fields=[
            "completed", "payment_status", "amount_tendered", "change_due",
        ])

    return primary_payment

# ===========================================================================
# Open sales API + batch receipt
# ===========================================================================
def api_open_sales(request):
    """JSON list of recent unpaid sales for the current cashier."""
    from django.utils import timezone
    from datetime import timedelta

    if not request.user.is_authenticated:
        return JsonResponse({"error": "auth required"}, status=403)

    cutoff = timezone.now() - timedelta(minutes=30)
    sales = (
        Sale.objects
        .filter(
            cashier=request.user,
            completed=False,
            payment_status=Sale.PaymentStatus.PENDING,
            sold_at__gte=cutoff,
        )
        .order_by("sold_at")
    )

    return JsonResponse([
        {
            "id": s.pk,
            "sale_number": s.sale_number,
            "total": str(s.total),
            "sold_at": s.sold_at.isoformat(),
        }
        for s in sales
    ], safe=False)


@login_required
def batch_receipt(request, payment_id):
    """Show all sales covered by a single M-Pesa payment."""
    require_active_user(request.user)
    payment = get_object_or_404(MpesaPayment, pk=payment_id)

    allocations = list(
        payment.allocations
        .select_related('sale', 'sale__cashier', 'sale__operating_point')
        .order_by("sale__sold_at")
    )
    if not allocations:
        messages.error(request, "This payment has no allocations.")
        return redirect("sales:pos")

    # Cashier restriction: only the cashier of the first sale or a director
    first_sale = allocations[0].sale
    if not is_director(request.user) and first_sale.cashier_id != request.user.pk:
        raise PermissionDenied("You cannot view another cashier's payment.")

    total_paid = sum((a.amount for a in allocations), start=0)

    return render(request, "sales/batch_receipt.html", {
        "payment": payment,
        "allocations": allocations,
        "total_paid": total_paid,
    })



# ===========================================================================
# SMART LOW-STOCK NOTIFICATION VIEWS
# ===========================================================================
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from .models import StockAlert, StockAlertNotification, StockAlertRule
from .low_stock import _describe_product


@login_required
def stock_alert_rules(request):
    if not is_director(request.user):
        raise PermissionDenied
    rules = (StockAlertRule.objects
             .select_related("product", "operating_point")
             .order_by("operating_point__code", "product__name"))
    operating_points = OperatingPoint.objects.filter(is_active=True).order_by("name")
    products = (Product.objects.filter(is_active=True, track_inventory=True)
                .order_by("name"))
    return render(request, "sales/stock_alert_rules.html", {
        "rules": rules,
        "operating_points": operating_points,
        "products": products,
    })


@login_required
def api_pending_low_stock_alerts(request):
    qs = (StockAlertNotification.objects
          .filter(recipient=request.user, alert__status=StockAlert.Status.ACTIVE)
          .select_related("alert__product", "alert__product__category",
                          "alert__product__selling_unit", "alert__operating_point")
          .order_by("created_at"))
    payload = []
    for n in qs:
        a = n.alert
        balance = (StockBalance.objects
                   .filter(product=a.product, operating_point=a.operating_point)
                   .values_list("quantity", flat=True).first()) or 0
        payload.append({
            "id": n.pk,
            "product_line": _describe_product(a.product),
            "category": str(a.product.category) if a.product.category_id else "",
            "operating_point": a.operating_point.name,
            "current": str(balance),
            "unit": a.product.selling_unit.abbreviation if a.product.selling_unit_id else "",
            "minimum": str(a.rule.minimum_quantity),
            "target": str(a.rule.target_quantity),
            "suggested": str(max(a.rule.target_quantity - balance, 0)),
            "triggered_at": a.triggered_at.isoformat(),
        })
    return JsonResponse({"alerts": payload})


@login_required
@require_POST
def api_acknowledge_low_stock_alert(request, notification_id):
    StockAlertNotification.objects.filter(
        pk=notification_id, recipient=request.user,
    ).delete()
    return JsonResponse({"ok": True})

# ===========================================================================
# DIRECTOR RULE MANAGEMENT (create / edit / toggle, no hard delete)
# ===========================================================================
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from decimal import Decimal, InvalidOperation
from catalog.models import Product
from accounts.models import OperatingPoint


def _director_gate(request):
    if not is_director(request.user):
        raise PermissionDenied


def _parse_thresholds(request):
    try:
        mn = Decimal(str(request.POST.get("minimum_quantity", "")).strip())
        tg = Decimal(str(request.POST.get("target_quantity", "")).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Minimum and target must be valid numbers.")
    if mn < 0:
        raise ValidationError("Minimum cannot be negative.")
    if tg <= mn:
        raise ValidationError("Target must be greater than minimum.")
    return mn, tg


@login_required
@require_POST
def stock_alert_rule_create(request):
    _director_gate(request)
    op = get_object_or_404(OperatingPoint, pk=request.POST.get("operating_point"))
    product = get_object_or_404(Product, pk=request.POST.get("product"))
    mn, tg = _parse_thresholds(request)
    rule, created = StockAlertRule.objects.get_or_create(
        product=product, operating_point=op,
        defaults=dict(
            minimum_quantity=mn, target_quantity=tg,
            is_active=True, created_by=request.user,
        ),
    )
    if not created:
        messages.error(request, f"A rule for {product.display_name} @ {op.name} already exists. Edit it instead.")
    else:
        messages.success(request, f"Rule created: {product.display_name} @ {op.name} (min {mn}, target {tg}).")
    return redirect("sales:stock_alert_rules")


@login_required
@require_POST
def stock_alert_rule_edit(request, pk):
    _director_gate(request)
    rule = get_object_or_404(StockAlertRule, pk=pk)
    mn, tg = _parse_thresholds(request)
    rule.minimum_quantity = mn
    rule.target_quantity = tg
    rule.save(update_fields=["minimum_quantity", "target_quantity", "updated_at"])
    messages.success(request, f"Updated {rule.product.display_name} @ {rule.operating_point.name}.")
    return redirect("sales:stock_alert_rules")


@login_required
@require_POST
def stock_alert_rule_toggle(request, pk):
    _director_gate(request)
    rule = get_object_or_404(StockAlertRule, pk=pk)
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active", "updated_at"])
    verb = "Activated" if rule.is_active else "Deactivated"
    messages.success(request, f"{verb} {rule.product.display_name} @ {rule.operating_point.name}.")
    return redirect("sales:stock_alert_rules")

# =========================================================================
# Supplier invoices and payments (Director-only)
# =========================================================================
@login_required
def invoice_list(request):
    """List supplier invoices. Director-only."""
    from .models import SupplierInvoice
    from business.models import Supplier

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may view supplier invoices.")

    qs = (SupplierInvoice.objects
          .select_related("supplier", "created_by")
          .order_by("-created_at"))

    status = request.GET.get("status") or ""
    if status in dict(SupplierInvoice.PaymentStatus.choices):
        qs = qs.filter(payment_status=status)

    supplier_id = request.GET.get("supplier") or ""
    if supplier_id.isdigit():
        qs = qs.filter(supplier_id=int(supplier_id))

    return render(request, "sales/invoice_list.html", {
        "invoices": qs[:500],
        "suppliers": Supplier.objects.filter(is_active=True).order_by("name"),
        "current_status": status,
        "current_supplier": supplier_id,
        "status_choices": SupplierInvoice.PaymentStatus.choices,
    })


@login_required
def invoice_detail(request, pk):
    """Show one invoice with linked receipts, payments, and Record Payment form."""
    from .models import SupplierInvoice, InvoicePayment
    from .forms import InvoicePaymentForm

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may view supplier invoices.")

    invoice = get_object_or_404(
        SupplierInvoice.objects
        .select_related("supplier", "created_by")
        .prefetch_related("receipts__product", "receipts__operating_point",
                          "payments__paid_by"),
        pk=pk,
    )
    return render(request, "sales/invoice_detail.html", {
        "invoice": invoice,
        "form": InvoicePaymentForm(),
        "payment_methods": InvoicePayment.Method.choices,
    })


@login_required
def invoice_record_payment(request, pk):
    """POST-only: record a payment against an invoice."""
    from .models import SupplierInvoice
    from .services import record_invoice_payment
    from .forms import InvoicePaymentForm

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may record payments.")
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    invoice = get_object_or_404(SupplierInvoice, pk=pk)
    form = InvoicePaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Payment could not be recorded. " + str(form.errors.as_text()))
        return redirect("sales:invoice_detail", pk=invoice.pk)

    try:
        record_invoice_payment(
            user=request.user,
            invoice=invoice,
            amount=form.cleaned_data["amount"],
            method=form.cleaned_data["method"],
            paid_at=form.cleaned_data.get("paid_at"),
            reference=form.cleaned_data.get("reference", ""),
            notes=form.cleaned_data.get("notes", ""),
        )
        messages.success(
            request,
            f"Payment recorded against {invoice.internal_reference}.")
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, str(e))
    return redirect("sales:invoice_detail", pk=invoice.pk)


@login_required
def outstanding_payables(request):
    """Director-only report: every invoice with balance_due > 0."""
    from .models import SupplierInvoice

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may view this report.")

    qs = (SupplierInvoice.objects
          .select_related("supplier")
          .filter(balance_due__gt=0)
          .order_by("created_at"))
    total = sum((inv.balance_due for inv in qs), Decimal("0.00"))
    return render(request, "sales/outstanding_payables.html", {
        "invoices": qs,
        "total_due": total.quantize(Decimal("0.01")),
    })


@login_required
def payments_made(request):
    """Director-only report: payments in a date range."""
    from .models import InvoicePayment
    from datetime import datetime

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may view this report.")

    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""
    qs = (InvoicePayment.objects
          .select_related("invoice__supplier", "paid_by")
          .order_by("-paid_at"))

    try:
        if date_from:
            qs = qs.filter(paid_at__date__gte=datetime.strptime(date_from, "%Y-%m-%d").date())
        if date_to:
            qs = qs.filter(paid_at__date__lte=datetime.strptime(date_to, "%Y-%m-%d").date())
    except ValueError:
        messages.error(request, "Invalid date format.")

    total = sum((p.amount for p in qs), Decimal("0.00"))
    return render(request, "sales/payments_made.html", {
        "payments": qs[:1000],
        "total_paid": total.quantize(Decimal("0.01")),
        "date_from": date_from,
        "date_to": date_to,
    })

@login_required
def pnl_report(request):
    """Director-only: profit-and-loss report for a date range."""
    from .costing import pnl_for_range
    from datetime import date, datetime, timedelta

    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Only the Director may view P&L reports.")

    today = timezone.localdate()
    default_from = today - timedelta(days=30)

    date_from = request.GET.get("from") or default_from.isoformat()
    date_to = request.GET.get("to") or today.isoformat()

    try:
        since = datetime.strptime(date_from, "%Y-%m-%d").date()
        until = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        messages.error(request, "Invalid date format.")
        since, until = default_from, today

    data = pnl_for_range(since=since, until=until)

    return render(request, "sales/pnl.html", {
        "data": data,
        "date_from": date_from,
        "date_to": date_to,
    })

# ---------------------------------------------------------------------------
# Reports hub
# ---------------------------------------------------------------------------
@login_required
def reports_home(request):
    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Director authorization required.")
    return render(request, "sales/reports_home.html")


# ---------------------------------------------------------------------------
# Daily sales by point
# ---------------------------------------------------------------------------
from datetime import timedelta as _ds_td


@login_required
def daily_sales_report(request):
    if not getattr(request.user, "is_director", False):
        raise PermissionDenied("Director authorization required.")

    try:
        days = int(request.GET.get("days", 7) or 7)
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 90))
    point_filter = (request.GET.get("point") or "").strip()

    today = timezone.localdate()
    start = today - _ds_td(days=days - 1)

    qs = (Sale.objects
          .filter(payment_status=Sale.PaymentStatus.PAID,
                  business_day__business_date__gte=start,
                  business_day__business_date__lte=today)
          .select_related("operating_point", "business_day"))

    if point_filter:
        qs = qs.filter(operating_point__code__iexact=point_filter)

    agg = {}
    for s in qs:
        key = (s.business_day.business_date, s.operating_point.code)
        agg[key] = agg.get(key, Decimal("0")) + (s.total or Decimal("0"))

    all_points = list(
        OperatingPoint.objects.filter(is_active=True).order_by("code")
    )
    if point_filter:
        all_points = [p for p in all_points
                      if p.code.lower() == point_filter.lower()]

    points = [p.code for p in all_points]

    total_days = (today - start).days + 1
    dates = [start + _ds_td(days=i) for i in range(total_days)]
    dates.reverse()

    matrix = []
    for d in dates:
        row_vals = []
        row_total = Decimal("0")
        for code in points:
            amt = agg.get((d, code), Decimal("0"))
            row_vals.append(amt)
            row_total += amt
        matrix.append({"date": d, "values": row_vals, "total": row_total})

    point_totals = []
    for i, code in enumerate(points):
        t = sum((row["values"][i] for row in matrix), Decimal("0"))
        point_totals.append(t)
    grand_total = sum(point_totals, Decimal("0"))

    return render(request, "sales/daily_sales_report.html", {
        "matrix": matrix,
        "points": points,
        "point_totals": point_totals,
        "grand_total": grand_total,
        "days": days,
        "point_filter": point_filter,
        "all_points": OperatingPoint.objects.filter(is_active=True).order_by("code"),
        "start": start,
        "end": today,
    })
