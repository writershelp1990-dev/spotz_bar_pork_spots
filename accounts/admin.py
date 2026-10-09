from django.contrib import admin

from .models import Department, Employee, OperatingPoint, RoleDefinition, PermissionDefinition, RolePermission


@admin.register(OperatingPoint)
class OperatingPointAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "is_active",
        "created_at",
    )

    list_filter = ("code", "is_active")

    search_fields = (
        "name",
        "description",
    )


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "created_at",
    )

    list_filter = ("is_active",)

    search_fields = (
        "name",
        "description",
    )


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "job_title",
        "operating_point",
        "department",
        "phone",
        "is_active",
    )

    list_filter = (
        "operating_point",
        "department",
        "is_active",
    )

    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "phone",
        "national_id",
    )

@admin.register(RoleDefinition)
class RoleDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_system_role", "is_active", "created_at")
    search_fields = ("name", "code")
    list_filter = ("is_system_role", "is_active")


@admin.register(PermissionDefinition)
class PermissionDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission", "granted_at")
    search_fields = ("role__name", "permission__code", "permission__name")
    list_filter = ("role", "permission")
