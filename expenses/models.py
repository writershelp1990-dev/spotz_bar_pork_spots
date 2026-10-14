"""Expense tracking: categories, expenses, recurring templates, payroll integration."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class ExpenseCategory(models.Model):
    """Tree-structured expense category.

    Root rows are top-level groupings (Utilities, Personnel, ...).
    Child rows are specific lines (Electricity, Salaries, ...).
    """
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=120)
    code = models.CharField(
        max_length=40, unique=True,
        help_text="Stable short identifier used in reports.",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Lower numbers appear first in lists.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Expense category"
        verbose_name_plural = "Expense categories"
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="expenses_unique_name_per_parent",
            ),
        ]

    def __str__(self):
        if self.parent:
            return self.parent.name + " > " + self.name
        return self.name

    @property
    def full_path(self):
        return str(self)

    @property
    def is_root(self):
        return self.parent_id is None


class Expense(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        MPESA = "MPESA", "M-Pesa"
        BANK = "BANK", "Bank transfer"
        CARD = "CARD", "Card"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    expense_date = models.DateField(
        db_index=True,
        help_text="The date the expense was incurred.",
    )
    payment_method = models.CharField(max_length=12, choices=PaymentMethod.choices)
    payee = models.CharField(
        max_length=150, blank=True,
        help_text="Who was paid (person, business, or description).",
    )
    description = models.TextField(blank=True)
    reference = models.CharField(max_length=80, blank=True)
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="expenses",
        help_text="Optional - some expenses are business-wide.",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices,
        default=Status.PENDING,
    )

    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="expenses_entered",
    )
    entered_at = models.DateTimeField(auto_now_add=True)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="expenses_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"
        indexes = [
            models.Index(fields=["expense_date", "status"],
                         name="expense_date_status_idx"),
            models.Index(fields=["category", "expense_date"],
                         name="expense_cat_date_idx"),
            models.Index(fields=["operating_point", "expense_date"],
                         name="expense_point_date_idx"),
        ]

    def __str__(self):
        return str(self.expense_date) + " " + str(self.category) + " " + str(self.amount)
