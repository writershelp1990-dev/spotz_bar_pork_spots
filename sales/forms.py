from django.conf import settings
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
            "operating_points",
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
        self.fields["operating_points"].queryset = OperatingPoint.objects.filter(
            is_active=True
        ).order_by("code")
        self.fields["operating_points"].required = False
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
    """Receive stock.

    Directors apply immediately; attendants create a PENDING StockReceipt
    that the Director must verify before the balance changes.

    The `user` kwarg tailors help text and the supplier dropdown; pass it
    from the view so the form can advertise the correct behaviour.
    """

    product = forms.ModelChoiceField(queryset=Product.objects.none())
    operating_point = forms.ModelChoiceField(
        queryset=OperatingPoint.objects.none())
    quantity = forms.DecimalField(
        min_value=0.001, max_digits=14, decimal_places=3)
    unit_cost = forms.DecimalField(
        min_value=0, max_digits=14, decimal_places=2,
        help_text="Price paid per unit on this delivery.",
    )
    supplier = forms.ModelChoiceField(
        queryset=None,  # set in __init__
        help_text="Add new suppliers first.",
    )
    invoice_number = forms.CharField(
        max_length=80, required=False,
        help_text="The supplier's invoice or delivery note number.",
    )
    invoice_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}),
    )
    reference = forms.CharField(max_length=80, required=False)
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False,
        help_text="Any additional detail (previous field name: reason).",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        self.fields["product"].queryset = (
            Product.objects.filter(is_active=True, track_inventory=True)
            .select_related("selling_unit").order_by("name")
        )

        op_qs = OperatingPoint.objects.filter(is_active=True).order_by("code")
        if user is not None and not getattr(user, "is_director", False):
            # Attendant: only their own deployed operating point.
            emp = getattr(user, "employee_profile", None)
            if emp is not None and emp.operating_point_id:
                op_qs = op_qs.filter(pk=emp.operating_point_id)
            else:
                op_qs = op_qs.none()
        self.fields["operating_point"].queryset = op_qs

        from business.models import Supplier
        self.fields["supplier"].queryset = (
            Supplier.objects.filter(is_active=True).order_by("name")
        )

    def clean(self):
        c = super().clean()
        qty = c.get("quantity")
        unit_cost = c.get("unit_cost")
        if qty is not None and unit_cost is not None:
            c["total_cost"] = (qty * unit_cost).quantize(Decimal("0.01"))
        return c
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
        if not settings.MPESA_STK_PUSH_ENABLED:
            self.fields['payment_method'].choices = [
                (value, label)
                for value, label in Sale.PaymentMethod.choices
                if value != Sale.PaymentMethod.MPESA.value
            ]
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

class InvoicePaymentForm(forms.Form):
    """Director-only: record a payment against a supplier invoice."""

    amount = forms.DecimalField(
        min_value=0.01, max_digits=14, decimal_places=2,
    )
    method = forms.ChoiceField(choices=None)
    paid_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"],
    )
    reference = forms.CharField(max_length=80, required=False)
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import InvoicePayment
        self.fields["method"].choices = InvoicePayment.Method.choices