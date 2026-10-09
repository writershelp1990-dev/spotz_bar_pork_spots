from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "user",
        "action",
        "model_name",
        "object_id",
        "operating_point",
    )

    list_filter = (
        "action",
        "model_name",
        "operating_point",
    )

    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "object_id",
        "object_repr",
        "reason",
    )

    readonly_fields = (
        "user",
        "action",
        "model_name",
        "object_id",
        "object_repr",
        "operating_point",
        "reason",
        "changes",
        "ip_address",
        "user_agent",
        "timestamp",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
