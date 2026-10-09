from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

class Sale(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        MPESA = "MPESA", "M-Pesa"
        CARD = "CARD", "Card"
        OTHER = "OTHER", "Other"

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

    class Meta:
        ordering = ["-sold_at", "-id"]

    def __str__(self):
        return self.sale_number

    def save(self, *args, **kwargs):
        if self.pk:
            update_fields = kwargs.get("update_fields")
            if set(update_fields or ()) != {"completed"}:
                raise ValidationError(
                    "Existing sale records cannot be edited."
                )
            previous = type(self).objects.filter(pk=self.pk).values_list(
                "completed", flat=True
            ).first()
            if previous is not False or self.completed is not True:
                raise ValidationError(
                    "Only an unfinished sale may be marked completed."
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

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="stock_movements"
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="stock_movements"
    )
    movement_type = models.CharField(max_length=12, choices=MovementType.choices)
    quantity_delta = models.DecimalField(max_digits=14, decimal_places=3)
    balance_after = models.DecimalField(max_digits=14, decimal_places=3)
    sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, null=True, blank=True,
        related_name="stock_movements"
    )
    reference = models.CharField(max_length=80, blank=True)
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
