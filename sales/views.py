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
from .models import Sale, StockBalance, StockLedger
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
            sale = checkout(
                user=request.user,
                operating_point=form.cleaned_data["operating_point"],
                payment_method=form.cleaned_data["payment_method"],
                amount_tendered=form.cleaned_data["amount_tendered"],
                items_json=form.cleaned_data["items_json"],
            )

            # M-Pesa: redirect to the STK-push screen instead of completing
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
    sale = get_object_or_404(Sale.objects.prefetch_related("items"), pk=sale_id, completed=True)
    if not is_director(request.user) and sale.cashier_id != request.user.pk:
        raise PermissionDenied("You cannot view another cashier's receipt.")
    return render(request, "sales/receipt.html", {"sale": sale})

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

@login_required
def stock_home(request):
    director_required(request.user)
    balances = StockBalance.objects.select_related("product", "operating_point").order_by("operating_point__name", "product__name")
    return render(request, "sales/stock.html", {"balances": balances})

@login_required
def stock_receive(request):
    director_required(request.user)
    form = ReceiptForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            movement = receive_stock(
                user=request.user,
                product=form.cleaned_data["product"],
                operating_point=form.cleaned_data["operating_point"],
                quantity=form.cleaned_data["quantity"],
                reference=form.cleaned_data["reference"],
                reason=form.cleaned_data["reason"]
            )
            messages.success(request, f"Received {movement.quantity_delta} {movement.product.selling_unit.abbreviation} of {movement.product.display_name}.")
            return redirect("sales:stock")
        except (ValidationError, PermissionDenied) as exc:
            form.add_error(None, getattr(exc, "message", str(exc)))
    return render(request, "sales/form.html", {"form": form, "heading": "Receive stock"})

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
    """Cashier submits customer phone. Creates STK push, redirects to wait page."""
    require_active_user(request.user)
    sale = get_object_or_404(Sale, pk=sale_id, completed=False)

    if not is_director(request.user) and sale.cashier_id != request.user.pk:
        raise PermissionDenied("You cannot pay for another cashier's sale.")

    if request.method != "POST":
        return redirect("sales:pos")

    phone = (request.POST.get("phone_number") or "").strip()

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

