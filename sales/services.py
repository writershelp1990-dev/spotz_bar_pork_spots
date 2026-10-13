import json
from .low_stock import evaluate_after_stock_change

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
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
def receive_stock(*, user, product, operating_point, quantity, reference="", reason="", unit_cost=None):
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
        reference=reference, reason=reason, created_by=user,
        unit_cost=unit_cost,
    )
    record_audit(
        user=user, action="CREATE", obj=movement,
        operating_point=operating_point,
        reason=reason or "Stock received",
        changes={"product": product.name, "quantity_delta": str(quantity),
                 "balance_after": str(balance.quantity), "reference": reference}
    )
    evaluate_after_stock_change(
        product=product,
        operating_point=operating_point,
        actor=user,
    )

    return movement

@transaction.atomic
def checkout(*, user, operating_point, payment_method, amount_tendered, items_json):
    check_point_permission(user, operating_point)
    if payment_method == Sale.PaymentMethod.MPESA and not settings.MPESA_STK_PUSH_ENABLED:
        raise ValidationError(
            "STK Push is disabled until the till is registered and integration is configured."
        )

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

    # STK-push M-Pesa is not confirmed until Daraja calls back.
    # All other methods are paid at the point of sale.
    initial_payment_status = (
        Sale.PaymentStatus.PENDING
        if payment_method == Sale.PaymentMethod.MPESA
        else Sale.PaymentStatus.PAID
    )

    sale = Sale.objects.create(
        sale_number="SP-" + timezone.localtime(now).strftime("%Y%m%d%H%M%S")
                    + "-" + uuid4().hex[:6].upper(),
        business_day=business_day, operating_point=operating_point,
        cashier=user, payment_method=payment_method,
        subtotal=subtotal, total=subtotal, amount_tendered=tendered,
        change_due=(tendered-subtotal).quantize(CENT), completed=False,
        payment_status=initial_payment_status,
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
            evaluate_after_stock_change(
                product=product,
                operating_point=operating_point,
                actor=user,
            )
        else:
            balance = None
            # Stage 6: if this product has an active Recipe, consume the
            # recipe's ingredients instead of deducting a finished product
            # (which has no stock of its own). Non-recipe products that
            # don't track inventory behave exactly as before — the
            # consume_recipe_ingredients() function returns [] if no recipe.
            from stock.recipes import consume_recipe_ingredients
            consume_recipe_ingredients(
                sale=sale,
                product=product,
                quantity=qty,
                operating_point=operating_point,
                user=user,
            )

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

# =========================================================================
# Receiving, invoice, and payment services (Stage A)
#
# These functions do NOT touch StockBalance or StockLedger for pending
# deliveries. Only verify_delivery() applies the balance change, by
# delegating to the existing receive_stock() function.
# =========================================================================
def _require_active_staff_or_director(user):
    """Any active staff or Director can record a delivery."""
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("An active signed-in user is required.")
    if not (getattr(user, "is_active_staff_user", False) or is_director(user)):
        raise PermissionDenied("Only active staff or the Director may do this.")


def _next_receipt_number():
    from uuid import uuid4
    return (
        "RC-" + timezone.localtime(timezone.now()).strftime("%Y%m%d%H%M%S")
        + "-" + uuid4().hex[:6].upper()
    )


def _next_invoice_reference():
    from uuid import uuid4
    return (
        "INV-" + timezone.localtime(timezone.now()).strftime("%Y%m%d%H%M%S")
        + "-" + uuid4().hex[:6].upper()
    )


@transaction.atomic
def find_or_create_invoice(*, user, supplier, invoice_number, invoice_date=None):
    """Return an existing UNPAID SupplierInvoice for this (supplier, invoice_number),
    or create a new one.

    Matching rule:
      - If invoice_number is empty: always create a new invoice.
      - If invoice_number is non-empty and an UNPAID or PARTIAL invoice
        exists for the same (supplier, invoice_number), reuse it.
      - Otherwise, create a new invoice.

    Concurrency: this function runs inside the caller's transaction, which
    also locks the product StockBalance row via receive_stock(). Two receipts
    against the same invoice must arrive in the same transaction to share it.
    A UNIQUE constraint on (supplier, invoice_number) for non-empty numbers
    prevents accidental duplicates at the database level.
    """
    from .models import SupplierInvoice
    if invoice_number:
        existing = (SupplierInvoice.objects
                    .select_for_update()
                    .filter(supplier=supplier, invoice_number=invoice_number)
                    .exclude(payment_status=SupplierInvoice.PaymentStatus.PAID)
                    .order_by("-created_at")
                    .first())
        if existing is not None:
            return existing

    return SupplierInvoice.objects.create(
        internal_reference=_next_invoice_reference(),
        invoice_number=invoice_number or "",
        supplier=supplier,
        invoice_date=invoice_date,
        created_by=user,
    )


@transaction.atomic
def record_delivery(*, user, product, operating_point, quantity, unit,
                    unit_cost, supplier, invoice_number="", invoice_date=None,
                    notes="", reference=""):
    """Record a physical delivery.

    For Directors: applies immediately via receive_stock(), creates a
    StockReceipt marked VERIFIED with verified_by == received_by.

    For active staff: creates a StockReceipt marked PENDING. No balance
    change, no ledger row, no low-stock evaluation.

    Both paths link the receipt to a SupplierInvoice, auto-created or reused.
    """
    from .models import StockReceipt

    _require_active_staff_or_director(user)
    if not is_director(user) and not _user_deployed_at(user, operating_point):
        raise PermissionDenied(
            f"You are not authorized to record deliveries at "
            f"{operating_point.code}."
        )

    # Validate inputs up front
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active or not product.track_inventory:
        raise ValidationError("Choose an active product that tracks inventory.")
    quantity = Decimal(quantity).quantize(THREE)
    if quantity <= 0:
        raise ValidationError("Received quantity must be greater than zero.")
    unit_cost = Decimal(unit_cost).quantize(CENT)
    if unit_cost < 0:
        raise ValidationError("Unit cost cannot be negative.")
    total_cost = (quantity * unit_cost).quantize(CENT)

    # Link to an invoice row
    invoice = find_or_create_invoice(
        user=user, supplier=supplier,
        invoice_number=invoice_number, invoice_date=invoice_date,
    )

    if is_director(user):
        # Apply immediately via the existing receive_stock service.
        movement = receive_stock(
            user=user, product=product, operating_point=operating_point,
            quantity=quantity,
            reference=reference or invoice.internal_reference,
            reason=notes or f"Director delivery via {supplier.name}",
            unit_cost=unit_cost,
        )

        receipt = StockReceipt.objects.create(
            receipt_number=_next_receipt_number(),
            product=product, operating_point=operating_point,
            quantity=quantity, unit=unit,
            unit_cost=unit_cost, total_cost=total_cost,
            supplier=supplier, invoice_number=invoice_number or "",
            notes=notes,
            received_by=user,
            status=StockReceipt.Status.VERIFIED,
            verified_by=user, verified_at=timezone.now(),
            verification_notes="Self-verified (Director entry).",
            supplier_invoice=invoice,
            resulting_movement=movement,
        )
        recompute_invoice_totals(invoice)

        record_audit(
            user=user, action="CREATE", obj=receipt,
            operating_point=operating_point,
            reason="Director recorded delivery (applied immediately)",
            changes={
                "receipt_number": receipt.receipt_number,
                "product": product.name,
                "quantity": str(quantity),
                "unit_cost": str(unit_cost),
                "total_cost": str(total_cost),
                "supplier": supplier.name,
                "invoice": invoice.internal_reference,
            },
        )
        return receipt

    # Attendant path: pending only.
    receipt = StockReceipt.objects.create(
        receipt_number=_next_receipt_number(),
        product=product, operating_point=operating_point,
        quantity=quantity, unit=unit,
        unit_cost=unit_cost, total_cost=total_cost,
        supplier=supplier, invoice_number=invoice_number or "",
        notes=notes,
        received_by=user,
        status=StockReceipt.Status.PENDING,
        supplier_invoice=invoice,
    )
    recompute_invoice_totals(invoice)

    record_audit(
        user=user, action="CREATE", obj=receipt,
        operating_point=operating_point,
        reason="Delivery recorded (pending Director verification)",
        changes={
            "receipt_number": receipt.receipt_number,
            "product": product.name,
            "quantity": str(quantity),
            "unit_cost": str(unit_cost),
            "total_cost": str(total_cost),
            "supplier": supplier.name,
            "invoice": invoice.internal_reference,
        },
    )
    return receipt


@transaction.atomic
def verify_delivery(*, user, receipt, notes=""):
    """Director-only: apply a pending delivery.

    Calls receive_stock() internally, links the resulting movement, and
    marks the receipt VERIFIED. Recomputes the invoice totals afterwards.
    """
    from .models import StockReceipt

    if not is_director(user):
        raise PermissionDenied("Only the Director may verify deliveries.")

    # Step 1: lock the receipt row only (select_for_update cannot be combined
    # with select_related on nullable FKs in PostgreSQL — the LEFT OUTER JOIN
    # cannot be locked).
    receipt = (StockReceipt.objects
               .select_for_update()
               .get(pk=receipt.pk))
    # Step 2: re-fetch with select_related for the display objects.
    receipt = (StockReceipt.objects
               .select_related("product", "operating_point", "unit",
                               "supplier_invoice", "supplier")
               .get(pk=receipt.pk))
    if receipt.status != StockReceipt.Status.PENDING:
        raise ValidationError(
            f"Cannot verify a receipt in status {receipt.status}."
        )

    movement = receive_stock(
        user=user,
        product=receipt.product,
        operating_point=receipt.operating_point,
        quantity=receipt.quantity,
        reference=receipt.supplier_invoice.internal_reference
                  if receipt.supplier_invoice else receipt.receipt_number,
        reason=notes or f"Verified delivery via {receipt.supplier.name}",
        unit_cost=receipt.unit_cost,
    )

    receipt.status = StockReceipt.Status.VERIFIED
    receipt.verified_by = user
    receipt.verified_at = timezone.now()
    receipt.verification_notes = notes
    receipt.resulting_movement = movement
    receipt.save(update_fields=[
        "status", "verified_by", "verified_at",
        "verification_notes", "resulting_movement",
    ])

    if receipt.supplier_invoice:
        recompute_invoice_totals(receipt.supplier_invoice)

    record_audit(
        user=user, action="UPDATE", obj=receipt,
        operating_point=receipt.operating_point,
        reason="Delivery verified",
        changes={
            "receipt_number": receipt.receipt_number,
            "status": "VERIFIED",
            "notes": notes,
        },
    )
    return receipt


@transaction.atomic
def reject_delivery(*, user, receipt, notes=""):
    """Director-only: reject a pending delivery. No balance change."""
    from .models import StockReceipt

    if not is_director(user):
        raise PermissionDenied("Only the Director may reject deliveries.")
    if not notes:
        raise ValidationError("A rejection reason is required.")

    receipt = StockReceipt.objects.select_for_update().get(pk=receipt.pk)
    if receipt.status != StockReceipt.Status.PENDING:
        raise ValidationError(
            f"Cannot reject a receipt in status {receipt.status}."
        )

    receipt.status = StockReceipt.Status.REJECTED
    receipt.verified_by = user
    receipt.verified_at = timezone.now()
    receipt.verification_notes = notes
    receipt.save(update_fields=[
        "status", "verified_by", "verified_at", "verification_notes",
    ])

    if receipt.supplier_invoice:
        recompute_invoice_totals(receipt.supplier_invoice)

    record_audit(
        user=user, action="UPDATE", obj=receipt,
        operating_point=receipt.operating_point,
        reason=f"Delivery rejected: {notes}",
        changes={"receipt_number": receipt.receipt_number, "status": "REJECTED"},
    )
    return receipt


def recompute_invoice_totals(invoice):
    """Recompute computed_total_amount, amount_paid, balance_due,
    payment_status from the invoice's linked receipts and payments.
    """
    from django.db.models import Sum
    from .models import StockReceipt, SupplierInvoice

    # Sum of VERIFIED receipts only (pending receipts are not yet official).
    computed = (
        StockReceipt.objects
        .filter(supplier_invoice=invoice, status=StockReceipt.Status.VERIFIED)
        .aggregate(s=Sum("total_cost"))["s"]
    ) or Decimal("0")
    computed = computed.quantize(CENT)

    paid = (
        invoice.payments.aggregate(s=Sum("amount"))["s"]
    ) or Decimal("0")
    paid = paid.quantize(CENT)

    invoice.computed_total_amount = computed
    invoice.amount_paid = paid
    invoice.balance_due = (computed - paid).quantize(CENT)

    if paid <= Decimal("0"):
        invoice.payment_status = SupplierInvoice.PaymentStatus.UNPAID
    elif paid < computed:
        invoice.payment_status = SupplierInvoice.PaymentStatus.PARTIAL
    else:
        invoice.payment_status = SupplierInvoice.PaymentStatus.PAID

    invoice.save(update_fields=[
        "computed_total_amount", "amount_paid",
        "balance_due", "payment_status",
    ])
    return invoice


@transaction.atomic
def record_invoice_payment(*, user, invoice, amount, method,
                           paid_at=None, reference="", notes=""):
    """Director-only: record a payment against an invoice."""
    from .models import InvoicePayment, SupplierInvoice

    if not is_director(user):
        raise PermissionDenied("Only the Director may record payments.")

    amount = Decimal(amount).quantize(CENT)
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")
    if method not in InvoicePayment.Method.values:
        raise ValidationError("Select a valid payment method.")

    invoice = SupplierInvoice.objects.select_for_update().get(pk=invoice.pk)

    payment = InvoicePayment.objects.create(
        invoice=invoice,
        amount=amount,
        paid_at=paid_at or timezone.now(),
        paid_by=user,
        method=method,
        reference=reference,
        notes=notes,
    )

    recompute_invoice_totals(invoice)

    record_audit(
        user=user, action="CREATE", obj=payment,
        reason=f"Payment recorded against {invoice.internal_reference}",
        changes={
            "invoice": invoice.internal_reference,
            "amount": str(amount),
            "method": method,
            "reference": reference,
        },
    )
    return payment


def _user_deployed_at(user, point):
    """Return True if the user's Employee profile is deployed to the point."""
    emp = getattr(user, "employee_profile", None)
    if emp is None or emp.operating_point_id is None:
        return False
    return emp.operating_point_id == point.pk
