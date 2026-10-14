"""Forms for the expenses app."""
from django import forms

from accounts.models import OperatingPoint
from .models import Expense, ExpenseCategory


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            "category", "amount", "expense_date", "payment_method",
            "payee", "description", "reference", "operating_point",
        ]
        widgets = {
            "expense_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = (
            ExpenseCategory.objects.filter(is_active=True)
            .select_related("parent")
            .order_by("parent__sort_order", "sort_order", "name")
        )
        self.fields["category"].empty_label = "Select a category"

        if user and not getattr(user, "is_director", False):
            point = getattr(getattr(user, "employee_profile", None),
                            "operating_point", None)
            if point:
                self.fields["operating_point"].queryset = (
                    OperatingPoint.objects.filter(pk=point.pk))
                self.fields["operating_point"].initial = point
            else:
                self.fields["operating_point"].queryset = (
                    OperatingPoint.objects.none())
        else:
            self.fields["operating_point"].queryset = (
                OperatingPoint.objects.filter(is_active=True).order_by("code"))
        self.fields["operating_point"].required = False
        self.fields["operating_point"].empty_label = "Business-wide (no specific point)"


class ExpenseApproveForm(forms.Form):
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Approval notes (optional)",
    )


class ExpenseRejectForm(forms.Form):
    reason = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Rejection reason",
    )
