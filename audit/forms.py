from django import forms
from django.contrib.auth import get_user_model

from accounts.models import OperatingPoint

from .models import AuditLog


class AuditLogFilterForm(forms.Form):
    start_datetime = forms.DateTimeField(
        required=False,
        label="From",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "spotz-input"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M",
        ],
    )
    end_datetime = forms.DateTimeField(
        required=False,
        label="To",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "spotz-input"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M",
        ],
    )
    user = forms.ModelChoiceField(
        required=False,
        queryset=get_user_model().objects.all().order_by(
            "first_name", "last_name", "username"
        ),
        empty_label="All users",
        widget=forms.Select(attrs={"class": "spotz-input"}),
    )
    action = forms.ChoiceField(
        required=False,
        choices=[("", "All actions"), *AuditLog.Action.choices],
        widget=forms.Select(attrs={"class": "spotz-input"}),
    )
    model_name = forms.CharField(
        required=False,
        label="Model",
        widget=forms.TextInput(
            attrs={
                "class": "spotz-input",
                "placeholder": "e.g. Employee",
            }
        ),
    )
    operating_point = forms.ModelChoiceField(
        required=False,
        queryset=OperatingPoint.objects.all().order_by("code"),
        empty_label="All operating points",
        widget=forms.Select(attrs={"class": "spotz-input"}),
    )
    search = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(
            attrs={
                "class": "spotz-input",
                "placeholder": "Object, reason, username...",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()

        start = cleaned.get("start_datetime")
        end = cleaned.get("end_datetime")

        if start and end and start > end:
            self.add_error(
                "end_datetime",
                "The end date/time cannot be earlier than the start date/time.",
            )

        return cleaned
