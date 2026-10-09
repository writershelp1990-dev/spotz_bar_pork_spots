from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from catalog.models import Product, UnitOfMeasure


class Supplier(models.Model):
    name = models.CharField(
        max_length=150,
        unique=True,
    )

    contact_person = models.CharField(
        max_length=150,
        blank=True,
    )

    phone = models.CharField(
        max_length=30,
        blank=True,
    )

    email = models.EmailField(
        blank=True,
    )

    address = models.TextField(
        blank=True,
    )

    tax_number = models.CharField(
        max_length=100,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Purchase(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchases",
    )

    operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.PROTECT,
        related_name="purchases",
    )

    purchase_number = models.CharField(
        max_length=50,
        unique=True,
    )

    purchase_date = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    invoice_number = models.CharField(
        max_length=100,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_purchases",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-purchase_date", "-id"]

        indexes = [
            models.Index(
                fields=[
                    "operating_point",
                    "purchase_date",
                ],
                name="purchase_point_date_idx",
            ),
            models.Index(
                fields=[
                    "supplier",
                    "purchase_date",
                ],
                name="purchase_supplier_date_idx",
            ),
        ]

    def __str__(self):
        return self.purchase_number


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.PROTECT,
        related_name="items",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="purchase_items",
    )

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
    )

    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="purchase_items",
    )

    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    line_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        editable=False,
        default=0,
    )

    notes = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError(
                {
                    "quantity":
                    "Quantity must be greater than zero."
                }
            )

        if self.unit_cost < 0:
            raise ValidationError(
                {
                    "unit_cost":
                    "Unit cost cannot be negative."
                }
            )

        if self.product_id and self.unit_id:
            allowed_units = {
                self.product.purchase_unit_id,
                self.product.selling_unit_id,
            }

            if self.unit_id not in allowed_units:
                raise ValidationError(
                    {
                        "unit":
                        "Purchase unit must match the product's "
                        "configured purchase or selling unit."
                    }
                )

    def save(self, *args, **kwargs):
        self.line_total = self.quantity * self.unit_cost
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.purchase.purchase_number} - "
            f"{self.product.name}"
        )

# Phase 1 Business-Day models.
from .business_day_models import BusinessDay, BusinessHoliday, StaffDeployment
