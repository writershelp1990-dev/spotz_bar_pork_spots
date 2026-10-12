"""Forms for Property & Assets."""
from decimal import Decimal

from django import forms

from .models import (
    Asset, AssetNewItemReport, AssetRelocation, ConditionReport,
    PhysicalArea, PropertyCheck, PropertyCheckLine,
)
from .permissions import is_director


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "asset_number", "name", "category", "description", "quantity",
            "current_area", "condition", "lifecycle_status",
            "registration_date", "acquired_date", "supplier",
            "purchase_cost", "notes",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "registration_date": forms.DateInput(attrs={"type": "date"}),
            "acquired_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset_number"].required = False
        self.fields["asset_number"].help_text = (
            "Leave blank to auto-assign (SPZ-<AREA>-####)."
        )
        self.fields["asset_number"].widget.attrs.update({
            "placeholder": "auto (leave blank)",
            "autocomplete": "off",
        })
        self.fields["current_area"].queryset = (
            PhysicalArea.objects.filter(is_active=True).order_by("code")
        )


class CheckLineForm(forms.Form):
    asset_id = forms.IntegerField(widget=forms.HiddenInput)
    expected_quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0)
    observed_quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0)
    condition = forms.ChoiceField(choices=Asset.Condition.choices)
    notes = forms.CharField(max_length=200, required=False)


class ConditionReportForm(forms.ModelForm):
    class Meta:
        model = ConditionReport
        fields = ["condition_reported", "quantity_affected", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class NewItemReportForm(forms.ModelForm):
    class Meta:
        model = AssetNewItemReport
        fields = [
            "proposed_name", "proposed_category", "description",
            "area", "condition", "quantity", "asset_identifier", "notes",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, allowed_areas=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = PhysicalArea.objects.filter(is_active=True)
        if allowed_areas is not None:
            qs = qs.filter(code__in=allowed_areas)
        self.fields["area"].queryset = qs.order_by("code")


class NewItemApprovalForm(forms.Form):
    ACTION_CHOICES = [("approve", "Approve"), ("reject", "Reject")]
    action = forms.ChoiceField(choices=ACTION_CHOICES, widget=forms.RadioSelect)
    review_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def clean(self):
        c = super().clean()
        if c.get("action") == "reject" and not c.get("review_notes"):
            raise forms.ValidationError("Reason is required when rejecting.")
        return c


class RelocationRequestForm(forms.ModelForm):
    class Meta:
        model = AssetRelocation
        fields = [
            "destination_area", "reason", "condition_before",
            "receiving_attendant", "expected_transfer_date", "notes",
        ]
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "expected_transfer_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, user=None, asset=None, allowed_areas=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = PhysicalArea.objects.filter(is_active=True)
        if allowed_areas is not None:
            qs = qs.filter(code__in=allowed_areas)
        self.fields["destination_area"].queryset = qs.order_by("code")
        # Receiving attendant: any active staff user
        from accounts.models import User
        self.fields["receiving_attendant"].queryset = (
            User.objects.filter(is_active=True, is_active_staff=True).order_by("username")
        )

    def clean_destination_area(self):
        dest = self.cleaned_data["destination_area"]
        if self.instance.pk and self.instance.asset_id:
            if dest == self.instance.asset.current_area:
                raise forms.ValidationError("Destination must differ from current area.")
        return dest


class RelocationApprovalForm(forms.Form):
    ACTION_CHOICES = [("approve", "Approve"), ("decline", "Decline")]
    action = forms.ChoiceField(choices=ACTION_CHOICES, widget=forms.RadioSelect)
    director_decision_notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def clean(self):
        c = super().clean()
        if c.get("action") == "decline" and not c.get("director_decision_notes"):
            raise forms.ValidationError("Reason is required when declining.")
        return c


class RecipientDecisionForm(forms.Form):
    ACTION_CHOICES = [("accept", "Accept & Receive"), ("decline", "Decline Receipt")]
    action = forms.ChoiceField(choices=ACTION_CHOICES, widget=forms.RadioSelect)
    decline_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False)


class ReceiptConditionForm(forms.Form):
    condition_at_receipt = forms.ChoiceField(choices=Asset.Condition.choices)
    receipt_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


class RelocationExceptionResolveForm(forms.Form):
    ACTION_CHOICES = [
        ("cancel", "Cancel transfer"),
        ("new_recipient", "Send to a new recipient"),
    ]
    action = forms.ChoiceField(choices=ACTION_CHOICES, widget=forms.RadioSelect)
    new_recipient = forms.ModelChoiceField(
        queryset=None, required=False,
        help_text="Required if action is 'Send to a new recipient'",
    )
    cancellation_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        self.fields["new_recipient"].queryset = (
            User.objects.filter(is_active=True, is_active_staff=True)
            .order_by("username")
        )


class ReportFilterForm(forms.Form):
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.ChoiceField(
        choices=[("", "All")] + list(AssetRelocation.Status.choices),
        required=False,
    )
    origin_area = forms.ModelChoiceField(
        queryset=PhysicalArea.objects.all().order_by("code"),
        required=False, empty_label="All origin areas",
    )
    destination_area = forms.ModelChoiceField(
        queryset=PhysicalArea.objects.all().order_by("code"),
        required=False, empty_label="All destinations",
    )