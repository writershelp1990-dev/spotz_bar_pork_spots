import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Employee
from audit.services import record_audit
from business.business_day_services import assert_business_day_open
from business.business_day_models import BusinessDay
from catalog.models import Product, ProductPriceHistory
from .models import Sale, SaleItem, StockBalance, StockLedger

CENT = Decimal("0.01")
THREE = Decimal("0.001")

def is_director(user):
    return bool(user and user.is_authenticated and getattr(user, "is_director", False))

def require_active_user(user):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("An active signed-in user is required.")
    if not getattr(user, "is_active_staff_user", False) and not is_director(user):
        raise PermissionDenied("This account is not active staff.")

def employee_point(user):
    try:
        employee = Employee.objects.get(user=user, is_active=True)
        return employee.operating_point
    except Employee.DoesNotExist:
        return None

def check_point_permission(user, point):
    require_active_user(user)
    if not point or not point.is_active:
        raise ValidationError("Choose an active operating point.")
    if is_director(user):
        return
    assigned = employee_point(user)
    if not assigned or assigned.pk != point.pk:
        raise PermissionDenied("You can operate only at your assigned operating point.")

def current_selling_price(product, moment):
    prices = ProductPriceHistory.objects.filter(
        product=product,
        price_type=ProductPriceHistory.PriceType.SELLING,
        effective_from__lte=moment,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=moment)).order_by(
        "-effective_from", "-id"
    )
    price = prices.first()
    if not price:
        raise ValidationError(f"No effective selling price is configured for {product.display_name}.")
    if price.price < 0:
        raise ValidationError(f"Invalid selling price for {product.display_name}.")
    return price.price

@transaction.atomic
def receive_stock(*, user, product, operating_point, quantity, reference="", reason=""):
    check_point_permission(user, operating_point)
    if not is_director(user):
        raise PermissionDenied("Only a director can receive or adjust stock.")
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active or not product.track_inventory:
        raise ValidationError("Choose an active product that tracks inventory.")
    quantity = Decimal(quantity).quantize(THREE)
    if quantity <= 0:
        raise ValidationError("Received quantity must be greater than zero.")

    balance, _ = StockBalance.objects.get_or_create(
        product=product, operating_point=operating_point, defaults={"quantity": 0}
    )
    balance = StockBalance.objects.select_for_update().get(pk=balance.pk)
    balance.quantity += quantity
    balance.save(update_fields=["quantity", "updated_at"])
    movement = StockLedger.objects.create(
        product=product, operating_point=operating_point,
        movement_type=StockLedger.MovementType.RECEIPT,
        quantity_delta=quantity, balance_after=balance.quantity,
        reference=reference, reason=reason, created_by=user
    )
    record_audit(
        user=user, action="CREATE", obj=movement,
        operating_point=operating_point,
        reason=reason or "Stock received",
        changes={"product": product.name, "quantity_delta": str(quantity),
                 "balance_after": str(balance.quantity), "reference": reference}
    )
    return movement

@transaction.atomic
def checkout(*, user, operating_point, payment_method, amount_tendered, items_json):
    check_point_permission(user, operating_point)
    now = timezone.now()
    business_day = BusinessDay.objects.select_for_update().filter(
        status=BusinessDay.Status.OPEN
    ).first()
    if not business_day:
        raise ValidationError("No business day is open. Open today's business day first.")
    assert_business_day_open(business_day)

    try:
        raw_items = json.loads(items_json)
    except (TypeError, json.JSONDecodeError):
        raise ValidationError("Sale items are invalid.")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("Add at least one product to the sale.")

    aggregated = {}
    for row in raw_items:
        if not isinstance(row, dict):
            raise ValidationError("Invalid sale item.")
        try:
            product_id = int(row.get("product_id"))
            qty = Decimal(str(row.get("quantity"))).quantize(THREE)
        except (ValueError, TypeError, InvalidOperation):
            raise ValidationError("Each item needs a valid product and quantity.")
        if qty <= 0 or qty > Decimal("1000000"):
            raise ValidationError("Quantity must be greater than zero and within limits.")
        aggregated[product_id] = aggregated.get(product_id, Decimal("0")) + qty

    # Lock product rows in stable order so concurrent checkouts serialize.
    products = {
        p.pk: p for p in Product.objects.select_for_update().filter(
            pk__in=sorted(aggregated)
        ).select_related("category", "selling_unit").order_by("pk")
    }
    if len(products) != len(aggregated):
        raise ValidationError("One or more selected products no longer exist.")

    calculated = []
    subtotal = Decimal("0.00")
    for product_id in sorted(aggregated):
        product = products[product_id]
        if not product.is_active:
            raise ValidationError(f"{product.display_name} is inactive.")
        qty = aggregated[product_id]
        price = current_selling_price(product, now).quantize(CENT)
        line_total = (qty * price).quantize(CENT, rounding=ROUND_HALF_UP)
        calculated.append((product, qty, price, line_total))
        subtotal += line_total

    subtotal = subtotal.quantize(CENT)
    # Manual M-Pesa (and STK-push M-Pesa) do not need an amount tendered;
    # the customer pays exactly the total.
    if amount_tendered is None and payment_method in (
        Sale.PaymentMethod.MPESA,
        Sale.PaymentMethod.MPESA_MANUAL,
    ):
        tendered = subtotal
    else:
        try:
            tendered = Decimal(str(amount_tendered)).quantize(CENT)
        except (InvalidOperation, TypeError):
            raise ValidationError("Enter a valid amount tendered.")
        if tendered < subtotal:
            raise ValidationError("Amount tendered cannot be less than the sale total.")
    if payment_method not in Sale.PaymentMethod.values:
        raise ValidationError("Select a valid payment method.")

    sale = Sale.objects.create(
        sale_number="SP-" + timezone.localtime(now).strftime("%Y%m%d%H%M%S")
                    + "-" + uuid4().hex[:6].upper(),
        business_day=business_day, operating_point=operating_point,
        cashier=user, payment_method=payment_method,
        subtotal=subtotal, total=subtotal, amount_tendered=tendered,
        change_due=(tendered-subtotal).quantize(CENT), completed=False
    )

    for product, qty, price, line_total in calculated:
        if product.track_inventory:
            balance, _ = StockBalance.objects.get_or_create(
                product=product, operating_point=operating_point,
                defaults={"quantity": 0}
            )
            balance = StockBalance.objects.select_for_update().get(pk=balance.pk)
            if balance.quantity < qty:
                raise ValidationError(
                    f"Insufficient stock for {product.display_name} at {operating_point.name}. "
                    f"Available: {balance.quantity}; requested: {qty}."
                )
            balance.quantity -= qty
            balance.save(update_fields=["quantity", "updated_at"])
        else:
            balance = None

        item = SaleItem.objects.create(
            sale=sale, product=product,
            product_name_snapshot=product.display_name,
            product_code_snapshot=product.code or "",
            unit_name_snapshot=product.selling_unit.abbreviation,
            quantity=qty, unit_price=price, line_total=line_total
        )
        if balance is not None:
            movement = StockLedger.objects.create(
                product=product, operating_point=operating_point,
                movement_type=StockLedger.MovementType.SALE,
                quantity_delta=-qty, balance_after=balance.quantity,
                sale=sale, reference=sale.sale_number, created_by=user,
                reason="POS checkout"
            )
            record_audit(
                user=user, action="CREATE", obj=movement,
                operating_point=operating_point,
                reason="Stock deducted for sale",
                changes={"sale_number": sale.sale_number,
                         "product": product.name, "quantity_delta": str(-qty),
                         "balance_after": str(balance.quantity)}
            )

    # NOTE: The sale is intentionally NOT marked completed here.
    # Each payment path completes the sale at the right moment:
    #   - Cash: completed immediately in the pos view
    #   - Manual M-Pesa: completed immediately in the pos view
    #   - STK-push M-Pesa: completed by the callback when payment succeeds
    record_audit(
        user=user, action="CREATE", obj=sale,
        operating_point=operating_point, reason="POS checkout completed",
        changes={"sale_number": sale.sale_number, "total": str(sale.total),
                 "payment_method": sale.payment_method,
                 "items": [{"product": p.name, "quantity": str(q),
                            "unit_price": str(price), "line_total": str(line)}
                           for p, q, price, line in calculated]}
    )
    return sale
