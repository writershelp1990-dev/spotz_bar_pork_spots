import re
from django.utils.text import slugify
from django import forms
from .models import Employee, OperatingPoint, RoleDefinition, EmployeeWarning, EmployeeSuspension, EmployeeLeave, EmployeeTermination

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import ValidationError

from .models import Employee, OperatingPoint, PermissionDefinition, RoleDefinition


User = get_user_model()


class SpotzAuthenticationForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": (
            "Please enter a correct email address and password. "
            "Note that both fields may be case-sensitive."
        ),
    }

    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={
            "autocomplete": "email",
            "placeholder": "name@example.com",
        }),
    )

    def clean_username(self):
        email = self.cleaned_data["username"].strip().lower()
        return email


class StaffInviteForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role_definition"].queryset = (
            RoleDefinition.objects
            .filter(is_active=True)
            .order_by("name")
        )
    full_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Staff full name"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"placeholder": "staff@example.com"}),
    )
    role_definition = forms.ModelChoiceField(
        queryset=RoleDefinition.objects.none(),
        empty_label="Select role",
    )
    operating_point = forms.ModelChoiceField(
        queryset=OperatingPoint.objects.filter(is_active=True).order_by("code"),
        empty_label="Select operating point",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("A user with this email address already exists.")

        if User.objects.filter(username__iexact=email).exists():
            raise ValidationError("A login account with this email address already exists.")

        return email


class CompleteInvitationForm(forms.Form):
    password1 = forms.CharField(
        label="Create password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        min_length=8,
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        min_length=8,
    )
    phone = forms.CharField(max_length=30, required=True)
    whatsapp_number = forms.CharField(max_length=30, required=True)
    mpesa_number = forms.CharField(max_length=30, required=True)

    def clean(self):
        cleaned = super().clean()

        if cleaned.get("password1") != cleaned.get("password2"):
            raise ValidationError("The passwords do not match.")

        return cleaned


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta:
        model = Employee
        fields = [
            "phone",
            "whatsapp_number",
            "mpesa_number",
        ]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

        self.fields["first_name"].initial = self.user.first_name
        self.fields["last_name"].initial = self.user.last_name

    def save(self, commit=True):
        employee = super().save(commit=commit)

        self.user.first_name = self.cleaned_data["first_name"].strip()
        self.user.last_name = self.cleaned_data["last_name"].strip()

        if commit:
            self.user.save(update_fields=["first_name", "last_name"])

        return employee



class RoleDefinitionForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=PermissionDefinition.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="New roles receive no permissions unless you explicitly select them.",
    )

    class Meta:
        model = RoleDefinition
        fields = [
            "name",
            "description",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Manager",
                    "autocomplete": "off",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Describe what this role is intended to do.",
                }
            ),
        }

    def __init__(self, *args, permissions=None, **kwargs):
        super().__init__(*args, **kwargs)

        if permissions is None:
            permissions = PermissionDefinition.objects.filter(
                is_active=True
            ).order_by("code")

        self.fields["permissions"].queryset = permissions

        if self.instance and self.instance.pk:
            self.fields["permissions"].initial = self.instance.role_permissions.values_list(
                "permission_id",
                flat=True,
            )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()

        if not name:
            raise forms.ValidationError("Role name is required.")

        code = slugify(name).replace("-", "_").upper()

        if len(code) > 30:
            raise forms.ValidationError(
                "This role name produces a code longer than 30 characters. "
                "Please use a shorter role name."
            )

        return name


class OperatingPointForm(forms.ModelForm):
    class Meta:
        model = OperatingPoint
        fields = ["code", "name", "description", "is_active"]
        widgets = {
            "code": forms.TextInput(
                attrs={"placeholder": "e.g. ROOFTOP"}
            ),
            "name": forms.TextInput(
                attrs={"placeholder": "Operating point name"}
            ),
            "description": forms.Textarea(
                attrs={
                    "placeholder": "Optional description",
                    "rows": 4,
                }
            ),
        }

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()

        if not code:
            raise ValidationError("Operating point code is required.")

        if not re.fullmatch(r"[A-Z0-9_]+", code):
            raise ValidationError(
                "Code may contain only uppercase letters, numbers and underscores."
            )

        return code


class EmployeeLifecycleForm(forms.Form):
    role_definition = forms.ModelChoiceField(
        queryset=RoleDefinition.objects.filter(is_active=True),
        required=True,
        widget=forms.Select(attrs={"class": "spotz-input"}),
    )
    employment_status = forms.ChoiceField(
        choices=Employee.EmploymentStatus.choices,
        widget=forms.Select(attrs={"class": "spotz-input"}),
    )
    date_joined = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "spotz-input", "type": "date"}),
    )
    operating_point = forms.ModelChoiceField(
        queryset=OperatingPoint.objects.filter(is_active=True),
        required=False,
        widget=forms.Select(attrs={"class": "spotz-input"}),
    )

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("employment_status")
        point = cleaned.get("operating_point")

        if status == Employee.EmploymentStatus.ACTIVE and point is None:
            raise forms.ValidationError(
                "An active employee must have an operating point."
            )

        return cleaned


class EmployeeWarningForm(forms.ModelForm):
    class Meta:
        model = EmployeeWarning
        fields = [
            "warning_type",
            "severity",
            "incident_date",
            "reason",
            "director_comment",
        ]
        widgets = {
            "warning_type": forms.Select(attrs={"class": "spotz-input"}),
            "severity": forms.Select(attrs={"class": "spotz-input"}),
            "incident_date": forms.DateInput(
                attrs={"class": "spotz-input", "type": "date"}
            ),
            "reason": forms.Textarea(attrs={"class": "spotz-input", "rows": 4}),
            "director_comment": forms.Textarea(
                attrs={"class": "spotz-input", "rows": 3}
            ),
        }


class EmployeeSuspensionForm(forms.ModelForm):
    class Meta:
        model = EmployeeSuspension
        fields = ["start_date", "end_date", "reason", "director_comment"]
        widgets = {
            "start_date": forms.DateInput(
                attrs={"class": "spotz-input", "type": "date"}
            ),
            "end_date": forms.DateInput(
                attrs={"class": "spotz-input", "type": "date"}
            ),
            "reason": forms.Textarea(attrs={"class": "spotz-input", "rows": 4}),
            "director_comment": forms.Textarea(
                attrs={"class": "spotz-input", "rows": 3}
            ),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError(
                "Suspension end date cannot be before start date."
            )
        return cleaned


class EmployeeLeaveForm(forms.ModelForm):
    class Meta:
        model = EmployeeLeave
        fields = ["leave_type", "start_date", "end_date", "reason"]
        widgets = {
            "leave_type": forms.Select(attrs={"class": "spotz-input"}),
            "start_date": forms.DateInput(
                attrs={"class": "spotz-input", "type": "date"}
            ),
            "end_date": forms.DateInput(
                attrs={"class": "spotz-input", "type": "date"}
            ),
            "reason": forms.Textarea(attrs={"class": "spotz-input", "rows": 4}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError(
                "Leave end date cannot be before start date."
            )
        return cleaned


class EmployeeTerminationForm(forms.ModelForm):
    class Meta:
        model = EmployeeTermination
        fields = ["termination_date", "reason", "director_comment"]
        widgets = {
            "termination_date": forms.DateInput(
                attrs={"class": "spotz-input", "type": "date"}
            ),
            "reason": forms.Textarea(attrs={"class": "spotz-input", "rows": 4}),
            "director_comment": forms.Textarea(
                attrs={"class": "spotz-input", "rows": 3}
            ),
        }
