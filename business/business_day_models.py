from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class BusinessDay(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    business_date = models.DateField(unique=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )

    scheduled_open_time = models.TimeField(default=time(8, 0))
    scheduled_close_time = models.TimeField(default=time(23, 0))

    actual_opened_at = models.DateTimeField(null=True, blank=True)
    actual_closed_at = models.DateTimeField(null=True, blank=True)

    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="business_days_opened",
        null=True,
        blank=True,
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="business_days_closed",
        null=True,
        blank=True,
    )

    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="business_days_reopened",
        null=True,
        blank=True,
    )
    reopen_reason = models.TextField(blank=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-business_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business_date"],
                name="unique_business_day_date",
            ),
            models.UniqueConstraint(
                fields=["status"],
                condition=Q(status="OPEN"),
                name="one_open_business_day",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "business_date"], name="businessday_status_date_idx"),
            models.Index(fields=["actual_opened_at"], name="businessday_opened_idx"),
            models.Index(fields=["actual_closed_at"], name="businessday_closed_idx"),
        ]

    def clean(self):
        if self.actual_closed_at and self.actual_opened_at:
            if self.actual_closed_at < self.actual_opened_at:
                raise ValidationError(
                    {"actual_closed_at": "Closing time cannot be earlier than opening time."}
                )

        if self.status == self.Status.CLOSED and not self.actual_closed_at:
            raise ValidationError(
                {"actual_closed_at": "A closed business day must have an actual closing timestamp."}
            )

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    @property
    def is_closed(self):
        return self.status == self.Status.CLOSED

    def __str__(self):
        return f"{self.business_date} ({self.get_status_display()})"


class BusinessHoliday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date"]
        indexes = [
            models.Index(fields=["date", "is_active"], name="holiday_date_active_idx"),
        ]

    def __str__(self):
        return f"{self.date} - {self.name}"


class StaffDeployment(models.Model):
    class DeploymentType(models.TextChoices):
        NORMAL = "NORMAL", "Normal deployment"
        REPLACEMENT = "REPLACEMENT", "Replacement coverage"
        ADDITIONAL = "ADDITIONAL", "Additional coverage"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CANCELLED = "CANCELLED", "Cancelled"

    employee = models.ForeignKey(
        "accounts.Employee",
        on_delete=models.PROTECT,
        related_name="business_day_deployments",
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.PROTECT,
        related_name="business_day_deployments",
    )
    business_day = models.ForeignKey(
        BusinessDay,
        on_delete=models.PROTECT,
        related_name="staff_deployments",
    )

    start_time = models.TimeField()
    end_time = models.TimeField()

    deployment_type = models.CharField(
        max_length=20,
        choices=DeploymentType.choices,
        default=DeploymentType.NORMAL,
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="staff_deployments_assigned",
    )

    reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["business_day__business_date", "start_time", "employee_id"]
        indexes = [
            models.Index(
                fields=["employee", "business_day"],
                name="deployment_employee_day_idx",
            ),
            models.Index(
                fields=["operating_point", "business_day"],
                name="deployment_point_day_idx",
            ),
            models.Index(
                fields=["business_day", "start_time"],
                name="deployment_day_start_idx",
            ),
            models.Index(
                fields=["business_day", "end_time"],
                name="deployment_day_end_idx",
            ),
        ]

    def clean(self):
        if self.end_time <= self.start_time:
            raise ValidationError(
                {"end_time": "Deployment end time must be later than start time."}
            )

        if self.business_day_id and self.business_day.is_closed:
            raise ValidationError(
                {"business_day": "Deployments cannot be changed on a closed business day."}
            )

        if self.operating_point_id and not self.operating_point.is_active:
            raise ValidationError(
                {"operating_point": "Employees cannot be deployed to an inactive operating point."}
            )

    def __str__(self):
        return (
            f"{self.employee} - {self.operating_point} - "
            f"{self.business_day.business_date} "
            f"{self.start_time:%H:%M}-{self.end_time:%H:%M}"
        )

class PointDay(models.Model):
    """Per-point open/close state within a global BusinessDay.

    A BusinessDay is the container; each PointDay is one point's open/close
    state inside it. A point opens when its staff arrive, closes when they
    leave. When the last open PointDay for a business day closes, the global
    BusinessDay auto-closes.
    """

    business_day = models.ForeignKey(
        BusinessDay,
        on_delete=models.PROTECT,
        related_name="point_days",
    )
    operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.PROTECT,
        related_name="point_days",
    )

    opened_at = models.DateTimeField()
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="point_days_opened",
    )

    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="point_days_closed",
        null=True,
        blank=True,
    )

    reopened_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-opened_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business_day", "operating_point"],
                name="unique_point_day_per_day",
            ),
        ]
        indexes = [
            models.Index(
                fields=["operating_point", "business_day"],
                name="pointday_point_day_idx",
            ),
            models.Index(
                fields=["business_day", "closed_at"],
                name="pointday_day_closed_idx",
            ),
        ]

    @property
    def is_open(self):
        return self.closed_at is None

    def __str__(self):
        state = "OPEN" if self.is_open else "CLOSED"
        return f"{self.operating_point.code} {self.business_day.business_date} ({state})"