from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models




class RoleDefinition(models.Model):
    """
    Database-backed staff role.

    The existing User.Role choices remain intact as stable role codes.
    These records allow the Director to manage additional roles such as
    Manager without removing compatibility with existing users.
    """

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_system_role = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PermissionDefinition(models.Model):
    """
    Business-level authorization permission.

    Permissions describe application actions rather than Django's generic
    model CRUD permissions.
    """

    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(
        RoleDefinition,
        on_delete=models.CASCADE,
        related_name="role_permissions",
    )
    permission = models.ForeignKey(
        PermissionDefinition,
        on_delete=models.CASCADE,
        related_name="role_permissions",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"],
                name="unique_role_permission",
            )
        ]

    def __str__(self):
        return f"{self.role} -> {self.permission}"



class User(AbstractUser):
    class Role(models.TextChoices):
        DIRECTOR = "DIRECTOR", "Director"
        BAR_ATTENDANT = "BAR_ATTENDANT", "Bar Attendant"
        BUTCHERY_ATTENDANT = "BUTCHERY_ATTENDANT", "Butchery Attendant"
        CHOMA_ATTENDANT = "CHOMA_ATTENDANT", "Choma Attendant"
        KITCHEN_ATTENDANT = "KITCHEN_ATTENDANT", "Kitchen Attendant"
        SECURITY_FACILITIES_ATTENDANT = "SECURITY_FACILITIES_ATTENDANT", "Security & Facilities Attendant"


    role_definition = models.ForeignKey(
        RoleDefinition,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )

    role = models.CharField(
        max_length=30,
        choices=Role.choices,
        default=Role.BAR_ATTENDANT,
    )

    phone = models.CharField(
        max_length=30,
        blank=True,
    )

    is_active_staff = models.BooleanField(
        default=True,
        help_text="Whether this user is currently active as SPOTZ staff.",
    )

    def __str__(self):
        full_name = self.get_full_name().strip()
        return full_name or self.username


    @property
    def is_active_staff_user(self):
        return bool(self.is_active and self.is_active_staff)

    @property
    def is_director(self):
        if self.is_superuser:
            return True

        if self.role_definition_id:
            return self.role_definition.code == self.Role.DIRECTOR

        return self.role == self.Role.DIRECTOR

    def user_has_permission(self, permission_code):
        if not self.is_active_staff_user:
            return False

        if self.is_superuser:
            return True

        if not self.role_definition_id:
            return False

        if not self.role_definition.is_active:
            return False

        return self.role_definition.role_permissions.filter(
            permission__code=permission_code,
            permission__is_active=True,
        ).exists()


class OperatingPoint(models.Model):
    code = models.CharField(
        max_length=30,
        unique=True,
        help_text="Unique operating-point code, for example BAR or ROOFTOP.",
    )

    name = models.CharField(max_length=100)

    description = models.TextField(blank=True)

    is_system_point = models.BooleanField(
        default=False,
        help_text="System-defined operating points cannot be removed.",
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name

class Department(models.Model):
    name = models.CharField(
        max_length=100,
        unique=True,
    )

    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_profile",
    )


    operating_point = models.ForeignKey(
        OperatingPoint,
        on_delete=models.PROTECT,
        related_name="employees",
        null=True,
        blank=True,
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="employees",
        null=True,
        blank=True,
    )

    job_title = models.CharField(
        max_length=100,
        blank=True,
    )

    phone = models.CharField(
        max_length=30,
        blank=True,
    )
    whatsapp_number = models.CharField(max_length=30, blank=True)
    mpesa_number = models.CharField(max_length=30, blank=True)


    national_id = models.CharField(
        max_length=50,
        blank=True,
    )

    hire_date = models.DateField(
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__last_name", "user__first_name", "user__username"]
    class EmploymentStatus(models.TextChoices):
        INVITED = "INVITED", "Invited"
        ACTIVE = "ACTIVE", "Active"
        OFF_DUTY = "OFF_DUTY", "Off Duty"
        ON_LEAVE = "ON_LEAVE", "On Leave"
        SUSPENDED = "SUSPENDED", "Suspended"
        TERMINATED = "TERMINATED", "Terminated"

    employment_status = models.CharField(
        max_length=20,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.INVITED,
    )
    date_joined = models.DateField(
        null=True,
        blank=True,
        help_text="Official employment start date entered by the Director.",
    )

    def __str__(self):
        name = self.user.get_full_name().strip()
        return name or self.user.username


class EmployeeWarning(models.Model):
    class WarningType(models.TextChoices):
        ATTENDANCE = "ATTENDANCE", "Attendance"
        CONDUCT = "CONDUCT", "Conduct"
        PERFORMANCE = "PERFORMANCE", "Performance"
        POLICY = "POLICY", "Policy"
        SAFETY = "SAFETY", "Safety"
        OTHER = "OTHER", "Other"

    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        FINAL = "FINAL", "Final"

    employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="warnings",
    )
    warning_type = models.CharField(max_length=30, choices=WarningType.choices)
    severity = models.CharField(max_length=20, choices=Severity.choices)
    incident_date = models.DateField()
    reason = models.TextField()
    director_comment = models.TextField(blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_warnings_issued",
    )
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Employee warnings are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Employee warnings cannot be deleted.")


class EmployeeSuspension(models.Model):
    employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="suspensions",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    director_comment = models.TextField(blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_suspensions_issued",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    activated_early = models.BooleanField(default=False)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employee_suspensions_activated",
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="suspension_end_not_before_start",
            ),
        ]


    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)

            immutable_fields = [
                "employee_id",
                "start_date",
                "end_date",
                "reason",
                "director_comment",
                "issued_by_id",
                "issued_at",
            ]

            for field_name in immutable_fields:
                if getattr(self, field_name) != getattr(original, field_name):
                    raise PermissionError(
                        "Historical suspension data cannot be edited."
                    )

            if original.activated_early:
                if (
                    self.activated_early != original.activated_early
                    or self.activated_by_id != original.activated_by_id
                    or self.activated_at != original.activated_at
                ):
                    raise PermissionError(
                        "Suspension activation history cannot be edited."
                    )

            activation_requested = (
                self.activated_early
                or self.activated_by_id is not None
                or self.activated_at is not None
            )

            original_activation = (
                original.activated_early
                or original.activated_by_id is not None
                or original.activated_at is not None
            )

            if activation_requested and not original_activation:
                if not (
                    self.activated_early
                    and self.activated_by_id is not None
                    and self.activated_at is not None
                ):
                    raise PermissionError(
                        "Suspension early activation must record actor and timestamp together."
                    )

        return super().save(*args, **kwargs)


    def delete(self, *args, **kwargs):
        raise PermissionError("Historical suspension records cannot be deleted.")


class EmployeeLeave(models.Model):
    class LeaveType(models.TextChoices):
        ANNUAL = "ANNUAL", "Annual Leave"
        SICK = "SICK", "Sick Leave"
        EMERGENCY = "EMERGENCY", "Emergency Leave"
        PERSONAL = "PERSONAL", "Personal Leave"
        OTHER = "OTHER", "Other"

    employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="leave_records",
    )
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_leave_approved",
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="leave_end_not_before_start",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Historical leave records cannot be edited.")
        return super().save(*args, **kwargs)


    def delete(self, *args, **kwargs):
        raise PermissionError("Historical leave records cannot be deleted.")


class DeploymentHistory(models.Model):
    employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="deployment_history",
    )
    from_operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deployment_from_history",
    )
    to_operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.PROTECT,
        related_name="deployment_to_history",
    )
    reason = models.TextField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deployment_changes",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Deployment history cannot be edited.")
        return super().save(*args, **kwargs)


    def delete(self, *args, **kwargs):
        raise PermissionError("Deployment history cannot be deleted.")


class EmployeeTermination(models.Model):
    employee = models.OneToOneField(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="termination",
    )
    termination_date = models.DateField()
    reason = models.TextField()
    director_comment = models.TextField(blank=True)
    terminated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_terminations",
    )
    terminated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-termination_date", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Termination records cannot be edited.")
        return super().save(*args, **kwargs)


    def delete(self, *args, **kwargs):
        raise PermissionError("Termination records cannot be deleted.")

