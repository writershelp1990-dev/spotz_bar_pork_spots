from django.contrib import admin

from .models import (
    Asset, AssetNewItemReport, AssetRelocation, ConditionReport,
    PhysicalArea, PropertyCheck, PropertyCheckLine, StaffAreaAccess,
)


@admin.register(PhysicalArea)
class PhysicalAreaAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(StaffAreaAccess)
class StaffAreaAccessAdmin(admin.ModelAdmin):
    list_display = ("employee", "area", "is_active", "granted_by", "granted_at")
    list_filter = ("is_active", "area")
    search_fields = ("employee__user__username", "employee__user__email", "area__code")


class PropertyCheckLineInline(admin.TabularInline):
    model = PropertyCheckLine
    extra = 0


@admin.register(PropertyCheck)
class PropertyCheckAdmin(admin.ModelAdmin):
    list_display = ("area", "checked_by", "checked_at")
    list_filter = ("area",)
    date_hierarchy = "checked_at"
    inlines = [PropertyCheckLineInline]


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("asset_number", "name", "category", "current_area",
                    "condition", "lifecycle_status", "updated_at")
    list_filter = ("current_area", "condition", "lifecycle_status", "category")
    search_fields = ("asset_number", "name", "category", "description")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(AssetNewItemReport)
class AssetNewItemReportAdmin(admin.ModelAdmin):
    list_display = ("proposed_name", "area", "reported_by", "status",
                    "submitted_at", "reviewed_by")
    list_filter = ("status", "area")
    search_fields = ("proposed_name", "description", "asset_identifier")
    readonly_fields = ("submitted_at",)


@admin.register(ConditionReport)
class ConditionReportAdmin(admin.ModelAdmin):
    list_display = ("asset", "area", "condition_reported", "status",
                    "reported_by", "reported_at", "resolved_by")
    list_filter = ("status", "condition_reported", "area")
    search_fields = ("asset__asset_number", "asset__name", "notes")
    readonly_fields = ("reported_at",)


@admin.register(AssetRelocation)
class AssetRelocationAdmin(admin.ModelAdmin):
    list_display = ("asset", "origin_area", "destination_area", "status",
                    "requested_by", "receiving_attendant", "submitted_at")
    list_filter = ("status", "origin_area", "destination_area")
    search_fields = ("asset__asset_number", "asset__name", "reason")
    readonly_fields = ("submitted_at",)