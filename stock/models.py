"""Stock movements, transfers, production batches, and recipes.

This app extends the existing sales.StockLedger and sales.StockBalance
with the specific workflows needed for pork receiving, allocation,
production, and reconciliation. It does NOT create a competing ledger
or balance mechanism.
"""
from __future__ import annotations

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


# ===========================================================================
# StockTransfer — one row per internal allocation/transfer
# ===========================================================================
class StockTransfer(models.Model):
    """An internal stock movement between two operating points.

    On COMPLETED, the service writes two sales.StockLedger rows
    (TRANSFER_OUT at the source, TRANSFER_IN at the destination) sharing
    this transfer's number as `reference`. The StockLedger remains the
    single source of truth for movements; this model is the pairing
    identity that links the two rows and holds approval metadata.
    """

    class TransferType(models.TextChoices):
        ALLOCATION = "ALLOCATION", "Allocation"       # butchery -> kitchen/choma
        RETURN = "RETURN", "Return"                   # kitchen/choma -> butchery
        CORRECTION = "CORRECTION", "Correction"       # any -> any, approved by Director

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending approval"
        APPROVED = "APPROVED", "Approved — ready to complete"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    transfer_number = models.CharField(max_length=40, unique=True)
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT,
        related_name="stock_transfers",
    )
    source_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="stock_transfers_out",
    )
    destination_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="stock_transfers_in",
    )
    quantity = models.DecimalField(
        max_digits=14, decimal_places=3,
        validators=[MinValueValidator(0)],
    )
    unit = models.ForeignKey(
        "catalog.UnitOfMeasure", on_delete=models.PROTECT,
        related_name="stock_transfers",
    )
    transfer_type = models.CharField(
        max_length=12, choices=TransferType.choices,
        default=TransferType.ALLOCATION,
    )
    reason = models.TextField(blank=True)
    reference = models.CharField(max_length=80, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_transfers_requested",
    )
    requested_at = models.DateTimeField(auto_now_add=True)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_transfers_approved", null=True, blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_transfers_completed", null=True, blank=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING,
    )

    class Meta:
        ordering = ["-requested_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-requested_at"]),
            models.Index(fields=["source_point", "status"]),
            models.Index(fields=["destination_point", "status"]),
        ]
        verbose_name = "Stock transfer"
        verbose_name_plural = "Stock transfers"

    def __str__(self):
        return (
            f"{self.transfer_number}: {self.product.name} "
            f"{self.quantity} {self.unit.abbreviation} "
            f"{self.source_point.code}->{self.destination_point.code} "
            f"[{self.status}]"
        )

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED


# ===========================================================================
# Recipe + RecipeItem — configurable ingredient consumption per sellable product
# ===========================================================================
class Recipe(models.Model):
    """Attaches ingredient consumption rules to a sellable catalog product.

    Products with a Recipe are made-to-order: `track_inventory=False`, and
    the ingredients in the recipe are deducted at checkout instead of the
    finished product. Products without a Recipe behave exactly as before.
    """

    product = models.OneToOneField(
        "catalog.Product", on_delete=models.CASCADE,
        related_name="recipe",
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__name"]
        verbose_name = "Recipe"
        verbose_name_plural = "Recipes"

    def __str__(self):
        return self.name or self.product.name


class RecipeItem(models.Model):
    """One ingredient consumed per unit of the recipe's product."""

    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE, related_name="items",
    )
    ingredient = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT,
        related_name="used_in_recipes",
    )
    quantity = models.DecimalField(
        max_digits=14, decimal_places=3,
        validators=[MinValueValidator(0)],
    )
    unit = models.ForeignKey(
        "catalog.UnitOfMeasure", on_delete=models.PROTECT,
        related_name="recipe_items",
    )

    class Meta:
        ordering = ["recipe__name", "ingredient__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipe", "ingredient"],
                name="unique_recipe_ingredient",
            ),
        ]
        verbose_name = "Recipe item"
        verbose_name_plural = "Recipe items"

    def __str__(self):
        return f"{self.quantity} {self.unit.abbreviation} {self.ingredient.name}"


# ===========================================================================
# ProductionBatch + ProductionConsumption — preparation events
# ===========================================================================
class ProductionBatch(models.Model):
    """One preparation event: raw ingredients transformed into finished portions.

    Whether or not finished portions are tracked as inventory (they are
    not, by default, in the hybrid design), each production event is
    recorded for reconciliation. The batch's consumption rows become
    PRODUCTION_OUT ledger movements at the preparation point.
    """

    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    batch_number = models.CharField(max_length=40, unique=True)
    recipe = models.ForeignKey(
        Recipe, on_delete=models.PROTECT,
        related_name="batches",
    )
    source_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="production_batches_sourced",
        help_text="Where the raw ingredients were issued from.",
    )
    preparation_point = models.ForeignKey(
        "accounts.OperatingPoint", on_delete=models.PROTECT,
        related_name="production_batches_prepared",
        help_text="Where the preparation actually happens (kitchen, choma).",
    )
    quantity_produced = models.DecimalField(
        max_digits=14, decimal_places=3,
        validators=[MinValueValidator(0)],
        help_text="Number of plates/portions produced in this batch.",
    )
    output_product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT,
        related_name="produced_in_batches",
    )
    notes = models.TextField(blank=True)

    produced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="production_batches_made",
    )
    produced_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.IN_PROGRESS,
    )

    class Meta:
        ordering = ["-produced_at", "-id"]
        indexes = [
            models.Index(fields=["preparation_point", "status"]),
            models.Index(fields=["recipe", "-produced_at"]),
        ]
        verbose_name = "Production batch"
        verbose_name_plural = "Production batches"

    def __str__(self):
        return (
            f"{self.batch_number}: {self.quantity_produced} x "
            f"{self.output_product.name} @ {self.preparation_point.code} "
            f"[{self.status}]"
        )


class ProductionConsumption(models.Model):
    """One ingredient consumed in a ProductionBatch."""

    batch = models.ForeignKey(
        ProductionBatch, on_delete=models.CASCADE,
        related_name="consumptions",
    )
    ingredient = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT,
        related_name="production_consumptions",
    )
    quantity = models.DecimalField(
        max_digits=14, decimal_places=3,
        validators=[MinValueValidator(0)],
    )
    unit = models.ForeignKey(
        "catalog.UnitOfMeasure", on_delete=models.PROTECT,
        related_name="production_consumptions",
    )

    class Meta:
        ordering = ["batch", "ingredient__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "ingredient"],
                name="unique_batch_ingredient",
            ),
        ]
        verbose_name = "Production consumption"
        verbose_name_plural = "Production consumptions"

    def __str__(self):
        return f"{self.quantity} {self.unit.abbreviation} {self.ingredient.name}"