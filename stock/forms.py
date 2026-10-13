"""Forms for the stock module."""
from __future__ import annotations

from django import forms

from accounts.models import OperatingPoint, User
from catalog.models import Product, UnitOfMeasure

from .models import (
    ProductionBatch, Recipe, RecipeItem, StockTransfer,
)


class TransferRequestForm(forms.ModelForm):
    """Create a PENDING transfer. Director approves and completes."""

    class Meta:
        model = StockTransfer
        fields = [
            "product", "source_point", "destination_point",
            "quantity", "unit", "transfer_type", "reason", "reference",
        ]
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Only active, inventory-tracked products
        self.fields["product"].queryset = (
            Product.objects.filter(is_active=True, track_inventory=True)
            .order_by("name")
        )
        self.fields["source_point"].queryset = (
            OperatingPoint.objects.filter(is_active=True).order_by("code")
        )
        self.fields["destination_point"].queryset = (
            OperatingPoint.objects.filter(is_active=True).order_by("code")
        )
        self.fields["unit"].queryset = UnitOfMeasure.objects.order_by("abbreviation")

    def clean(self):
        c = super().clean()
        src = c.get("source_point")
        dst = c.get("destination_point")
        if src and dst and src.pk == dst.pk:
            raise forms.ValidationError("Source and destination must differ.")
        qty = c.get("quantity")
        if qty is not None and qty <= 0:
            raise forms.ValidationError("Quantity must be greater than zero.")
        return c


class RecipeForm(forms.Form):
    """Create a recipe with a variable number of ingredient lines.

    The template renders one row per product; only non-empty rows are
    submitted. Ingredient + quantity per line.
    """

    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        help_text="The finished sellable product this recipe applies to.",
    )
    name = forms.CharField(max_length=120, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = (
            Product.objects.filter(is_active=True)
            .exclude(recipe__isnull=False)  # not already attached to a recipe
            .order_by("name")
        )


class RecipeIngredientLineForm(forms.Form):
    ingredient = forms.ModelChoiceField(
        queryset=Product.objects.none(),
    )
    quantity = forms.DecimalField(
        max_digits=14, decimal_places=3, min_value=0,
        required=False,
    )
    unit = forms.ModelChoiceField(
        queryset=UnitOfMeasure.objects.none(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ingredient"].queryset = (
            Product.objects.filter(is_active=True, track_inventory=True)
            .order_by("name")
        )
        self.fields["unit"].queryset = UnitOfMeasure.objects.order_by("abbreviation")


class ProductionBatchForm(forms.ModelForm):
    """Record a preparation event."""

    class Meta:
        model = ProductionBatch
        fields = [
            "recipe", "source_point", "preparation_point",
            "quantity_produced", "output_product", "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipe"].queryset = (
            Recipe.objects.filter(is_active=True).select_related("product").order_by("name")
        )
        self.fields["source_point"].queryset = (
            OperatingPoint.objects.filter(is_active=True).order_by("code")
        )
        self.fields["preparation_point"].queryset = (
            OperatingPoint.objects.filter(is_active=True).order_by("code")
        )
        # output_product is derived from the chosen recipe; leave the field
        # so Django validates it, but hide it in the template. The service
        # will overwrite it from the recipe anyway.
        self.fields["output_product"].queryset = (
            Product.objects.filter(is_active=True).order_by("name")
        )


class WastageForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    operating_point = forms.ModelChoiceField(queryset=OperatingPoint.objects.none())
    quantity = forms.DecimalField(
        max_digits=14, decimal_places=3, min_value=0.001,
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    reference = forms.CharField(max_length=80, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = (
            Product.objects.filter(is_active=True, track_inventory=True)
            .order_by("name")
        )
        self.fields["operating_point"].queryset = (
            OperatingPoint.objects.filter(is_active=True).order_by("code")
        )