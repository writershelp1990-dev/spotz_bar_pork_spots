from decimal import Decimal, InvalidOperation
import re
import json
from django import forms
from catalog.models import Category, Product, ProductPriceHistory, UnitOfMeasure
from accounts.models import OperatingPoint
from .models import Sale

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "parent", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = Category.objects.filter(is_active=True)
        self.fields["parent"].required = False
        self.fields["parent"].empty_label = "(Top-level category)"

class UnitForm(forms.ModelForm):
    class Meta:
        model = UnitOfMeasure
        fields = ["name", "abbreviation", "unit_type", "is_active"]

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "brand",
            "variety",
            "packaging",
            "size_value",
            "size_unit",
            "category",
            "product_type",
            "purchase_unit",
            "selling_unit",
            "purchase_to_sale_ratio",
            "description",
            "is_active",
            "track_inventory",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(
            is_active=True
        ).select_related("parent")
        self.fields["category"].label_from_instance = lambda obj: obj.full_path
        self.fields["purchase_unit"].queryset = UnitOfMeasure.objects.filter(
            is_active=True
        )
        self.fields["selling_unit"].queryset = UnitOfMeasure.objects.filter(
            is_active=True
        )
        self.fields["size_unit"].queryset = UnitOfMeasure.objects.filter(
            is_active=True
        )
        self.fields["size_value"].required = False
        self.fields["size_unit"].required = False

    def clean(self):
        cleaned = super().clean()
        size = cleaned.get("size_value")
        unit = cleaned.get("size_unit")

        if (size is None) != (unit is None):
            self.add_error(
                "size_value",
                "Enter both the product size and its unit, or leave both blank.",
            )
        elif size is not None and size <= 0:
            self.add_error("size_value", "Product size must be greater than zero.")

        return cleaned

class SellingPriceForm(forms.ModelForm):
    class Meta:
        model = ProductPriceHistory
        fields = ["product", "price", "effective_from", "notes"]
        widgets = {"effective_from": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["effective_from"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S%z"]

    def clean_effective_from(self):
        """Interpret a naive datetime as the current local timezone.

        The HTML `datetime-local` input submits a string with no timezone
        (e.g. "2026-10-10T02:48"). Django parses it as a naive datetime.
        With USE_TZ=True, saving a naive datetime gets interpreted as UTC,
        which is wrong — the user's input means local time.

        This makes the value timezone-aware using the current timezone
        (Africa/Nairobi in production) so "2:48 a.m." means "2:48 a.m.
        Nairobi", not "2:48 a.m. UTC".
        """
        from django.utils import timezone

        value = self.cleaned_data.get("effective_from")
        if value is not None and timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean_price(self):
        price = self.cleaned_data["price"]
        if price <= 0:
            raise forms.ValidationError("Selling price must be greater than zero.")
        return price

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.price_type = ProductPriceHistory.PriceType.SELLING
        obj.unit = obj.product.selling_unit
        if commit:
            obj.full_clean()
            obj.save()
        return obj

class ReceiptForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    operating_point = forms.ModelChoiceField(queryset=OperatingPoint.objects.none())
    quantity = forms.DecimalField(min_value=0.001, max_digits=14, decimal_places=3)
    reference = forms.CharField(max_length=80, required=False)
    reason = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(
            is_active=True, track_inventory=True
        ).select_related("selling_unit")
        self.fields["operating_point"].queryset = OperatingPoint.objects.filter(is_active=True)

class CheckoutForm(forms.Form):
    operating_point = forms.ModelChoiceField(queryset=OperatingPoint.objects.none())
    payment_method = forms.ChoiceField(choices=Sale.PaymentMethod.choices)
    amount_tendered = forms.DecimalField(
        min_value=0, max_digits=14, decimal_places=2, required=False,
    )
    items_json = forms.CharField(widget=forms.HiddenInput())

    # --- Manual M-Pesa: JSON list of {code, amount} pairs ---
    mpesa_codes_json = forms.CharField(
        widget=forms.HiddenInput(), required=False,
    )
    mpesa_cash_amount = forms.DecimalField(
        min_value=0, max_digits=12, decimal_places=2, required=False,
    )

    # --- Batch: other open sales covered by the same code ---
    batch_sale_ids_json = forms.CharField(
        widget=forms.HiddenInput(), required=False,
    )

    # --- Hold as unpaid ---
    hold_as_unpaid = forms.BooleanField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operating_point'].queryset = OperatingPoint.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get('payment_method')
        if method == 'MPESA_MANUAL':
            self._clean_manual_mpesa(cleaned)
        return cleaned

    def _clean_manual_mpesa(self, cleaned):
        raw = (cleaned.get('mpesa_codes_json') or '').strip()
        if not raw or raw == '[]':
            self.add_error(None, 'Enter at least one M-Pesa receipt code.')
            return
        try:
            rows = json.loads(raw)
        except (ValueError, TypeError):
            self.add_error(None, 'Invalid M-Pesa entries.')
            return
        if not isinstance(rows, list) or not rows:
            self.add_error(None, 'Enter at least one M-Pesa receipt code.')
            return
        normalized = []
        seen = set()
        total = Decimal('0.00')
        for i, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                self.add_error(None, f'Invalid M-Pesa row {i}.')
                return
            code = str(row.get('code') or '').strip().upper()
            amount_raw = row.get('amount')
            if not code:
                self.add_error(None, f'M-Pesa code for row {i} is empty.')
                return
            if not re.match(r'^[A-Z0-9]{8,12}$', code):
                self.add_error(None, f'Invalid M-Pesa code format: {code}')
                return
            if code in seen:
                self.add_error(None, f'Duplicate M-Pesa code in this sale: {code}')
                return
            seen.add(code)
            try:
                amount = Decimal(str(amount_raw)).quantize(Decimal('0.01'))
            except (InvalidOperation, TypeError, ValueError):
                self.add_error(None, f'Invalid amount for M-Pesa code {code}.')
                return
            if amount <= 0:
                self.add_error(None, f'Amount for M-Pesa code {code} must be greater than zero.')
                return
            normalized.append({'code': code, 'amount': amount})
            total += amount
        cleaned['mpesa_codes'] = normalized
        cleaned['mpesa_total'] = total.quantize(Decimal('0.01'))
        cash = cleaned.get('mpesa_cash_amount') or Decimal('0.00')
        cleaned['mpesa_cash_amount'] = cash.quantize(Decimal('0.01'))
        batch_raw = (cleaned.get('batch_sale_ids_json') or '').strip()
        batch_ids = []
        if batch_raw and batch_raw != '[]':
            try:
                batch_ids = [int(x) for x in json.loads(batch_raw)]
            except (ValueError, TypeError):
                self.add_error(None, 'Invalid batch sale selection.')
                return
        cleaned['batch_sale_ids'] = batch_ids
