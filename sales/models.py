from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

class Sale(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        MPESA = "MPESA", "M-Pesa (STK Push)"
        MPESA_MANUAL = "MPESA_MANUAL", "M-Pesa (Customer-Paid)"
        CARD = "CARD", "Card"
        OTHER = "OTHER", "Other"

    class PaymentStatus(models.TextChoices):
        PENDING = "PENDING", "Pending payment"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Payment failed"

    sale_number = models.CharField(max_length=32, unique=True)
    business_day = models.ForeignKey(
        "business.BusinessDay", on_delete=models.PROTECT,
        related_name="sales"
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="sales"
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="pos_sales"
    )
    sold_at = models.DateTimeField(auto_now_add=True)
    payment_method = models.CharField(
        max_length=12, choices=PaymentMethod.choices
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_tendered = models.DecimalField(max_digits=14, decimal_places=2)
    change_due = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    completed = models.BooleanField(default=False)

    class PaymentStatus(models.TextChoices):
        PENDING = "PENDING", "Pending payment"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Payment failed"

    payment_status = models.CharField(
        max_length=12,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
        help_text="PENDING for M-Pesa until confirmed, PAID for cash.",
    )

    customer_phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="Set for M-Pesa payments.",
    )

    class Meta:
        ordering = ["-sold_at", "-id"]

    def __str__(self):
        return self.sale_number

    # require explicit update_fields on existing sales
    def save(self, *args, **kwargs):
        if self.pk:
            update_fields = kwargs.get("update_fields")

            # Any save on an existing sale MUST specify update_fields.
            # Without this, arbitrary edits would bypass the guard.
            if update_fields is None:
                raise ValidationError(
                    "Existing sale records cannot be edited without " 
                    "explicit update_fields."
                )

            allowed = {
                "completed",
                "payment_status",
                "customer_phone",
                "amount_tendered",
                "change_due",
            }

            if not set(update_fields).issubset(allowed):
                raise ValidationError(
                    "Existing sale records cannot be edited."
                )

            # Prevent flipping `completed` back to False
            if "completed" in update_fields:
                previous = type(self).objects.filter(pk=self.pk).values_list(
                    "completed", flat=True
                ).first()
                if previous is True and self.completed is False:
                    raise ValidationError(
                        "A completed sale cannot be reopened."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Sale records cannot be deleted.")

class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    product_name_snapshot = models.CharField(max_length=150)
    product_code_snapshot = models.CharField(max_length=50, blank=True)
    unit_name_snapshot = models.CharField(max_length=60)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="sale_item_quantity_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0), name="sale_item_price_nonnegative"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Sale items cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Sale items cannot be deleted.")

class StockBalance(models.Model):
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_balances"
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="stock_balances"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "operating_point"],
                name="unique_stock_product_point"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=0), name="stock_balance_nonnegative"
            ),
        ]

    def __str__(self):
        return f"{self.product} @ {self.operating_point}: {self.quantity}"

class StockLedger(models.Model):
    class MovementType(models.TextChoices):
        RECEIPT = "RECEIPT", "Stock receipt"
        SALE = "SALE", "Sale"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        # --- Added in Stage 3 of the stock module ---
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer out (source side)"
        TRANSFER_IN = "TRANSFER_IN", "Transfer in (destination side)"
        PRODUCTION_OUT = "PRODUCTION_OUT", "Production consumption"
        PRODUCTION_IN = "PRODUCTION_IN", "Production output"
        WASTAGE = "WASTAGE", "Wastage / spoilage"

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_movements"
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="stock_movements"
    )
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity_delta = models.DecimalField(max_digits=14, decimal_places=3)
    balance_after = models.DecimalField(max_digits=14, decimal_places=3)
    sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, null=True, blank=True,
        related_name="stock_movements"
    )
    reference = models.CharField(max_length=80, blank=True)
    unit_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Cost per unit at the moment of this movement, where known.",
    )
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(quantity_delta=0), name="stock_ledger_delta_nonzero"
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Stock ledger entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Stock ledger entries cannot be deleted.")


class MpesaTransaction(models.Model):
    """One row per STK Push attempt. A sale may have multiple attempts."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled by user"
        TIMEOUT = "TIMEOUT", "Timed out"

    sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, related_name="mpesa_transactions"
    )
    phone_number = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    merchant_request_id = models.CharField(max_length=64, blank=True)
    checkout_request_id = models.CharField(max_length=64, blank=True, db_index=True)
    mpesa_receipt_number = models.CharField(max_length=40, blank=True)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    result_code = models.CharField(max_length=12, blank=True)
    result_description = models.TextField(blank=True)

    raw_request = models.JSONField(default=dict, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"M-Pesa {self.phone_number} for {self.sale_id} [{self.status}]"


# ===========================================================================
# M-PESA — Payments and Allocations
# ===========================================================================
class MpesaPayment(models.Model):
    """
    A single M-Pesa transaction.

    One code = one row, globally unique across all sales.
    A payment may be allocated across multiple sales (e.g. a customer
    paying for the whole table), and each allocation records how much
    of this code was applied to that specific sale.
    """

    receipt_code = models.CharField(
        max_length=40,
        unique=True,
        db_index=True,
        help_text="M-Pesa code from the customer SMS, e.g. SLK7X2M9AB.",
    )
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Total amount of this M-Pesa transaction.",
    )
    customer_phone = models.CharField(max_length=20, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_mpesa_payments",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.receipt_code} — KES {self.total_amount}"

    @property
    def allocated_amount(self):
        """Sum of all allocations for this payment."""
        from django.db.models import Sum
        result = self.allocations.aggregate(total=Sum("amount"))["total"]
        return result or Decimal("0.00")

    @property
    def unallocated_amount(self):
        return self.total_amount - self.allocated_amount


class MpesaPaymentAllocation(models.Model):
    """
    How much of a specific MpesaPayment was applied to a specific Sale.

    A single payment can have many allocations (across sales), and a
    single sale can have many allocations (from different payments).
    """

    payment = models.ForeignKey(
        MpesaPayment,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    sale = models.ForeignKey(
        "Sale",
        on_delete=models.PROTECT,
        related_name="mpesa_allocations",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("payment", "sale")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.payment.receipt_code} -> sale {self.sale_id}: KES {self.amount}"

    def clean(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError("Allocation amount must be greater than zero.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)



# ===========================================================================
# SMART LOW-STOCK NOTIFICATIONS
# ===========================================================================
class StockAlertRule(models.Model):
    """Per-product, per-operating-point low-stock thresholds."""
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_alert_rules",
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT, related_name="stock_alert_rules",
    )
    minimum_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    target_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_alert_rules_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "operating_point"],
                name="unique_alert_rule_product_point",
            ),
            models.CheckConstraint(
                condition=Q(minimum_quantity__gte=0),
                name="alert_rule_minimum_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(target_quantity__gt=models.F("minimum_quantity")),
                name="alert_rule_target_above_minimum",
            ),
        ]

    def __str__(self):
        return f"{self.product} @ {self.operating_point} (min {self.minimum_quantity})"


class StockAlert(models.Model):
    """One row per active shortage. Resolved rows kept for history."""
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RESOLVED = "RESOLVED", "Resolved"

    rule = models.ForeignKey(
        StockAlertRule, on_delete=models.PROTECT, related_name="alerts",
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_alerts",
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT, related_name="stock_alerts",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    triggered_at = models.DateTimeField(auto_now_add=True)
    triggered_balance = models.DecimalField(max_digits=14, decimal_places=3)
    minimum_at_trigger = models.DecimalField(max_digits=14, decimal_places=3)
    target_at_trigger = models.DecimalField(max_digits=14, decimal_places=3)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    next_reminder_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_balance = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "operating_point"],
                condition=Q(status="ACTIVE"),
                name="unique_active_alert_per_product_point",
            ),
        ]
        ordering = ["-triggered_at", "-id"]
        indexes = [
            models.Index(fields=["status", "next_reminder_at"], name="alert_status_next_idx"),
        ]

    def __str__(self):
        return f"[{self.status}] {self.product} @ {self.operating_point}"


class StockAlertNotification(models.Model):
    """Transient per-user pop-up. Deleted when acknowledged."""
    alert = models.ForeignKey(
        StockAlert, on_delete=models.CASCADE, related_name="notifications",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="stock_alert_notifications",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["alert", "recipient"],
                name="unique_alert_notification_per_user",
            ),
        ]
        ordering = ["-created_at"]

# =========================================================================
# Receiving, invoice, and payment workflow
#
# StockReceipt captures what was physically delivered. Attendant entries
# are PENDING until a Director verifies them; Director entries self-verify.
# Verification calls sales.services.receive_stock() to update StockBalance
# and write the StockLedger row.
#
# SupplierInvoice is one row per supplier document. Multiple receipts may
# link to the same invoice (e.g. beer + soda + pork on one invoice).
#
# InvoicePayment is one row per payment made against an invoice. Payment
# fields are Director-only: no view or service exposes them to staff.
# =========================================================================
class StockReceipt(models.Model):
    """A physical delivery of stock, pending or verified."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending verification"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    receipt_number = models.CharField(max_length=40, unique=True)
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT,
        related_name="stock_receipts",
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="stock_receipts",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.ForeignKey(
        "catalog.UnitOfMeasure", on_delete=models.PROTECT,
        related_name="stock_receipts",
    )

    # Purchase cost captured at receipt time (per delivery, not per product).
    unit_cost = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Price paid per unit at receipt.",
    )
    total_cost = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="quantity * unit_cost, stored for reporting speed.",
    )

    supplier = models.ForeignKey(
        "business.Supplier", on_delete=models.PROTECT,
        related_name="stock_receipts",
    )
    invoice_number = models.CharField(
        max_length=80, blank=True,
        help_text="The supplier's invoice or delivery note number.",
    )
    notes = models.TextField(blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_receipts_recorded",
    )
    received_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_receipts_verified", null=True, blank=True,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_notes = models.TextField(blank=True)

    supplier_invoice = models.ForeignKey(
        "sales.SupplierInvoice", on_delete=models.SET_NULL,
        related_name="receipts", null=True, blank=True,
    )
    resulting_movement = models.OneToOneField(
        StockLedger, on_delete=models.SET_NULL,
        related_name="source_receipt", null=True, blank=True,
    )

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-received_at"]),
            models.Index(fields=["operating_point", "status"]),
            models.Index(fields=["supplier", "-received_at"]),
        ]
        verbose_name = "Stock receipt"
        verbose_name_plural = "Stock receipts"

    def __str__(self):
        return (
            f"{self.receipt_number}: {self.quantity} x {self.product.name} "
            f"@ {self.operating_point.code} [{self.status}]"
        )


class SupplierInvoice(models.Model):
    """One row per supplier invoice/document. Payment fields are Director-only."""

    class PaymentStatus(models.TextChoices):
        UNPAID = "UNPAID", "Unpaid"
        PARTIAL = "PARTIAL", "Partially paid"
        PAID = "PAID", "Paid"

    internal_reference = models.CharField(
        max_length=40, unique=True,
        help_text="Our own identifier, e.g. INV-20261013-0001.",
    )
    invoice_number = models.CharField(
        max_length=80, blank=True,
        help_text="The supplier's own invoice number.",
    )
    supplier = models.ForeignKey(
        "business.Supplier", on_delete=models.PROTECT,
        related_name="invoices",
    )
    invoice_date = models.DateField(
        null=True, blank=True,
        help_text="Date on the supplier's document.",
    )

    computed_total_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Sum of linked receipts' total_cost.",
    )
    stated_total_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Optional: what the supplier's paper document says.",
    )
    amount_paid = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
    )
    balance_due = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="computed_total_amount - amount_paid. May be negative on "
                  "overpayment (a credit toward future deliveries).",
    )
    payment_status = models.CharField(
        max_length=12, choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="supplier_invoices_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["supplier", "-created_at"]),
            models.Index(fields=["payment_status", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "invoice_number"],
                condition=models.Q(invoice_number__gt=""),
                name="unique_supplier_invoice_number_when_set",
            ),
        ]
        verbose_name = "Supplier invoice"
        verbose_name_plural = "Supplier invoices"

    def __str__(self):
        return f"{self.internal_reference} ({self.supplier}) [{self.payment_status}]"


class InvoicePayment(models.Model):
    """A payment made against a SupplierInvoice. Director-only."""

    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        MPESA = "MPESA", "M-Pesa"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        CHEQUE = "CHEQUE", "Cheque"
        OTHER = "OTHER", "Other"

    invoice = models.ForeignKey(
        SupplierInvoice, on_delete=models.CASCADE,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_at = models.DateTimeField()
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="invoice_payments_made",
    )
    method = models.CharField(max_length=20, choices=Method.choices)
    reference = models.CharField(
        max_length=80, blank=True,
        help_text="M-Pesa code, cheque number, bank reference, etc.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at", "-id"]
        indexes = [
            models.Index(fields=["invoice", "-paid_at"]),
        ]
        verbose_name = "Invoice payment"
        verbose_name_plural = "Invoice payments"

    def __str__(self):
        return f"{self.amount} to {self.invoice.internal_reference} on {self.paid_at}"


# ---------------------------------------------------------------------------
# Sales projections
# ---------------------------------------------------------------------------
class SalesProjection(models.Model):
    """A computed or manually-set expectation for one point on one date.

    Rows with is_override=False are (re)computed by the
    compute_daily_projections management command. Rows with
    is_override=True are manually set by a Director and are never
    overwritten by the command.
    """
    date = models.DateField(db_index=True)
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.CASCADE,
        related_name="sales_projections",
    )
    projected_amount = models.DecimalField(
        max_digits=14, decimal_places=2,
    )
    is_override = models.BooleanField(default=False)
    computed_at = models.DateTimeField(auto_now=True)
    source_days = models.PositiveIntegerField(default=0)
    source_data = models.JSONField(default=list, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="projections_entered",
    )

    class Meta:
        unique_together = ("date", "operating_point")
        ordering = ["-date", "operating_point__code"]
        indexes = [
            models.Index(fields=["date", "operating_point"],
                         name="proj_date_point_idx"),
            models.Index(fields=["operating_point", "-date"],
                         name="proj_point_date_idx"),
        ]
        verbose_name = "Sales projection"
        verbose_name_plural = "Sales projections"

    def __str__(self):
        marker = " (override)" if self.is_override else ""
        return f"{self.date} {self.operating_point.code}: {self.projected_amount}{marker}"


class ProjectionConfig(models.Model):
    """Singleton config for the projection algorithm. One row only.

    The compute command reads from this row. Edit via Django admin
    or a future Director-facing settings page.
    """
    history_days = models.PositiveIntegerField(default=6)
    outlier_trim = models.BooleanField(default=True)
    holiday_adjustment = models.BooleanField(default=True)
    trend_adjustment = models.BooleanField(default=False)
    cold_start_fallback = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Projection config"
        verbose_name_plural = "Projection config"

    def __str__(self):
        return f"ProjectionConfig (history={self.history_days}d, cold_start={self.cold_start_fallback})"

    def save(self, *args, **kwargs):
        # Enforce singleton: force pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
