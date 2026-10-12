"""SPOTZ Property & Assets module.

Durable business property (furniture, electronics, equipment, fixtures) --
completely separate from consumable inventory. Never touches StockBalance,
StockLedger, or sales.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class PhysicalArea(models.Model):
    CODE_CHOICES = [
        ("BAR", "Bar"),
        ("BAR_COUNTER", "Bar Counter"),
        ("BUTCHERY", "Butchery"),
        ("CHOMA", "Choma"),
        ("KITCHEN", "Kitchen"),
        ("DINING_AREA", "Dining Area"),
        ("OUTDOOR_AREA", "Outdoor Area"),
        ("WASHROOMS", "Washrooms"),
        ("GARDEN_COMPOUND", "Garden / Compound"),
        ("OFFICE", "Office"),
        ("STORE", "Store"),
        ("OTHER", "Other"),
    ]
    code = models.CharField(max_length=30, choices=CODE_CHOICES, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Physical area"
        verbose_name_plural = "Physical areas"

    def __str__(self):
        return self.name or self.code


class StaffAreaAccess(models.Model):
    employee = models.ForeignKey("accounts.Employee", on_delete=models.CASCADE,
                                 related_name="area_access_grants")
    area = models.ForeignKey(PhysicalArea, on_delete=models.PROTECT,
                             related_name="staff_grants")
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.PROTECT,
                                   related_name="area_access_grants_made")
    granted_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "area"],
                                    name="unique_area_access_per_employee"),
        ]
        ordering = ["employee__user__username", "area__code"]
        verbose_name = "Staff area access"
        verbose_name_plural = "Staff area access grants"

    def __str__(self):
        return f"{self.employee} -> {self.area.code}"


class Asset(models.Model):
    class Condition(models.TextChoices):
        WORKING = "WORKING", "Working / Good"
        NEEDS_REPAIR = "NEEDS_REPAIR", "Needs Repair"
        BROKEN = "BROKEN", "Broken"
        DAMAGED = "DAMAGED", "Damaged"
        MISSING_PARTS = "MISSING_PARTS", "Missing Parts"
        NOT_TESTED = "NOT_TESTED", "Not Tested"
        OTHER = "OTHER", "Other"

    class Lifecycle(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        UNDER_MAINTENANCE = "UNDER_MAINTENANCE", "Under Maintenance"
        MISSING = "MISSING", "Missing"
        DISPOSED = "DISPOSED", "Disposed / Removed"
        RETIRED = "RETIRED", "Retired"

    asset_number = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3,
                                   default=Decimal("1"),
                                   validators=[MinValueValidator(Decimal("0"))])
    current_area = models.ForeignKey(PhysicalArea, on_delete=models.PROTECT,
                                     related_name="assets")
    condition = models.CharField(max_length=20, choices=Condition.choices,
                                 default=Condition.WORKING)
    lifecycle_status = models.CharField(max_length=20, choices=Lifecycle.choices,
                                        default=Lifecycle.ACTIVE)
    registration_date = models.DateField(default=timezone.localdate)
    acquired_date = models.DateField(null=True, blank=True)
    supplier = models.CharField(max_length=120, blank=True)
    purchase_cost = models.DecimalField(max_digits=14, decimal_places=2,
                                        null=True, blank=True,
                                        validators=[MinValueValidator(Decimal("0"))])
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.PROTECT,
                                   related_name="assets_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.PROTECT,
                                   related_name="assets_updated",
                                   null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_number"]
        indexes = [
            models.Index(fields=["current_area", "lifecycle_status"]),
            models.Index(fields=["category"]),
        ]
        verbose_name = "Asset"
        verbose_name_plural = "Assets"

    def __str__(self):
        return f"{self.asset_number} - {self.name}"

    @property
    def is_active(self):
        return self.lifecycle_status == self.Lifecycle.ACTIVE


class AssetNewItemReport(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Director Review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    proposed_name = models.CharField(max_length=120)
    proposed_category = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    area = models.ForeignKey(PhysicalArea, on_delete=models.PROTECT,
                             related_name="new_item_reports")
    condition = models.CharField(max_length=20, choices=Asset.Condition.choices,
                                 default=Asset.Condition.WORKING)
    quantity = models.DecimalField(max_digits=14, decimal_places=3,
                                   default=Decimal("1"),
                                   validators=[MinValueValidator(Decimal("0"))])
    asset_identifier = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.PROTECT,
                                    related_name="new_item_reports_submitted")
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.PROTECT,
                                    related_name="new_item_reports_reviewed",
                                    null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    resulting_asset = models.OneToOneField(Asset, on_delete=models.SET_NULL,
                                           related_name="source_new_item_report",
                                           null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [models.Index(fields=["status", "submitted_at"])]
        verbose_name = "New-item report"
        verbose_name_plural = "New-item reports"

    def __str__(self):
        return f"[{self.status}] {self.proposed_name} @ {self.area.code}"


class PropertyCheck(models.Model):
    area = models.ForeignKey(PhysicalArea, on_delete=models.PROTECT,
                             related_name="property_checks")
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.PROTECT,
                                   related_name="property_checks_performed")
    checked_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-checked_at"]
        indexes = [models.Index(fields=["area", "-checked_at"])]
        verbose_name = "Property check"
        verbose_name_plural = "Property checks"

    def __str__(self):
        return f"Check @ {self.area.code} on {self.checked_at:%Y-%m-%d %H:%M}"


class PropertyCheckLine(models.Model):
    property_check = models.ForeignKey(PropertyCheck, on_delete=models.CASCADE,
                              related_name="lines")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT,
                              related_name="check_lines")
    expected_quantity = models.DecimalField(max_digits=14, decimal_places=3,
                                            validators=[MinValueValidator(Decimal("0"))])
    observed_quantity = models.DecimalField(max_digits=14, decimal_places=3,
                                            validators=[MinValueValidator(Decimal("0"))])
    condition = models.CharField(max_length=20, choices=Asset.Condition.choices,
                                 default=Asset.Condition.WORKING)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["asset__asset_number"]
        verbose_name = "Property check line"
        verbose_name_plural = "Property check lines"

    def __str__(self):
        return f"{self.asset.asset_number}: {self.observed_quantity}/{self.expected_quantity}"

    @property
    def has_discrepancy(self):
        return self.observed_quantity != self.expected_quantity


class ConditionReport(models.Model):
    class ConditionReported(models.TextChoices):
        MISSING = "MISSING", "Missing"
        BROKEN = "BROKEN", "Broken"
        DAMAGED = "DAMAGED", "Damaged"
        NEEDS_REPAIR = "NEEDS_REPAIR", "Needs Repair"
        UNDER_MAINTENANCE = "UNDER_MAINTENANCE", "Under Maintenance"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged by Director"
        RESOLVED = "RESOLVED", "Resolved"
        DISMISSED = "DISMISSED", "Dismissed"

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT,
                              related_name="condition_reports")
    area = models.ForeignKey(PhysicalArea, on_delete=models.PROTECT,
                             related_name="condition_reports")
    condition_reported = models.CharField(max_length=24,
                                          choices=ConditionReported.choices)
    quantity_affected = models.DecimalField(max_digits=14, decimal_places=3,
                                            default=Decimal("1"),
                                            validators=[MinValueValidator(Decimal("0"))])
    notes = models.TextField(blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.PROTECT,
                                    related_name="condition_reports_submitted")
    reported_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.OPEN)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.PROTECT,
                                    related_name="condition_reports_resolved",
                                    null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-reported_at"]
        indexes = [
            models.Index(fields=["status", "-reported_at"]),
            models.Index(fields=["asset"]),
        ]
        verbose_name = "Condition report"
        verbose_name_plural = "Condition reports"

    def __str__(self):
        return f"[{self.status}] {self.asset.asset_number}: {self.condition_reported}"


class AssetRelocation(models.Model):
    class Status(models.TextChoices):
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Director Approval"
        DECLINED_BY_DIRECTOR = "DECLINED_BY_DIRECTOR", "Declined by Director"
        APPROVED = "APPROVED", "Approved - Awaiting Recipient"
        AWAITING_RECIPIENT = "AWAITING_RECIPIENT", "Awaiting Recipient"
        DECLINED_BY_RECIPIENT = "DECLINED_BY_RECIPIENT", "Declined by Recipient"
        RECEIVED = "RECEIVED", "Received - Pending Completion"
        COMPLETED = "COMPLETED", "Completed"
        TRANSFER_EXCEPTION = "TRANSFER_EXCEPTION", "Transfer Exception"
        CANCELLED = "CANCELLED", "Cancelled"

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT,
                              related_name="relocations")
    origin_area = models.ForeignKey(PhysicalArea, on_delete=models.PROTECT,
                                    related_name="relocations_from")
    destination_area = models.ForeignKey(PhysicalArea, on_delete=models.PROTECT,
                                         related_name="relocations_to")
    reason = models.TextField()
    condition_before = models.CharField(max_length=20,
                                        choices=Asset.Condition.choices)
    receiving_attendant = models.ForeignKey(settings.AUTH_USER_MODEL,
                                            on_delete=models.PROTECT,
                                            related_name="relocations_to_receive")
    expected_transfer_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                     on_delete=models.PROTECT,
                                     related_name="relocations_requested")
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=24, choices=Status.choices,
                              default=Status.PENDING_APPROVAL)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.PROTECT,
                                    related_name="relocations_approved",
                                    null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    director_decision_notes = models.TextField(blank=True)
    recipient_decision = models.CharField(
        max_length=12,
        choices=[("ACCEPTED", "Accepted"), ("DECLINED", "Declined")],
        blank=True)
    recipient_decided_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.PROTECT,
                                    related_name="relocations_received",
                                    null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    condition_at_receipt = models.CharField(max_length=20,
                                            choices=Asset.Condition.choices,
                                            blank=True)
    receipt_notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["status", "-submitted_at"]),
            models.Index(fields=["receiving_attendant", "status"]),
        ]
        verbose_name = "Asset relocation"
        verbose_name_plural = "Asset relocations"

    def __str__(self):
        return (f"#{self.pk} {self.asset.asset_number}: "
                f"{self.origin_area.code} -> {self.destination_area.code} [{self.status}]")
    # --- State-machine helpers (used by services.py and views) ---
    TERMINAL_STATES = {
        "COMPLETED", "DECLINED_BY_DIRECTOR", "CANCELLED",
        "TRANSFER_EXCEPTION", "DECLINED_BY_RECIPIENT",
    }

    @property
    def is_pending_director(self):
        return self.status == "PENDING_APPROVAL"

    @property
    def is_awaiting_recipient(self):
        return self.status in {"APPROVED", "AWAITING_RECIPIENT"}

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATES
