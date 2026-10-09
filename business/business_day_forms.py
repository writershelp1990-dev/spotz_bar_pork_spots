from django import forms

from accounts.models import OperatingPoint, Employee

from .business_day_models import BusinessDay, BusinessHoliday, StaffDeployment


class BusinessDayOpenForm(forms.Form):
    business_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class BusinessDayCloseForm(forms.Form):
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class BusinessDayReopenForm(forms.Form):
    reason = forms.CharField(
        required=True,
        min_length=5,
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class BusinessHolidayForm(forms.ModelForm):
    class Meta:
        model = BusinessHoliday
        fields = ["date", "name", "is_active", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class StaffDeploymentForm(forms.ModelForm):
    class Meta:
        model = StaffDeployment
        fields = [
            "employee",
            "operating_point",
            "start_time",
            "end_time",
            "deployment_type",
            "reason",
            "notes",
        ]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, business_day=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.business_day = business_day

        self.fields["employee"].queryset = (
            Employee.objects
            .select_related("user", "operating_point")
            .filter(
                employment_status=Employee.EmploymentStatus.ACTIVE,
                user__is_active=True,
                user__is_active_staff=True,
            )
            .order_by(
                "user__last_name",
                "user__first_name",
                "user__username",
            )
        )

        self.fields["operating_point"].queryset = (
            OperatingPoint.objects
            .filter(is_active=True)
            .order_by("code")
        )