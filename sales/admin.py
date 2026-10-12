from django.contrib import admin
from .models import Sale, SaleItem, StockBalance, StockLedger

class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in SaleItem._meta.fields]
    def has_add_permission(self, request, obj=None):
        return False

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("sale_number", "sold_at", "operating_point", "cashier", "total", "payment_method", "completed")
    list_filter = ("operating_point", "payment_method", "completed")
    search_fields = ("sale_number", "cashier__username")
    readonly_fields = [f.name for f in Sale._meta.fields]
    inlines = [SaleItemInline]
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ("product", "operating_point", "quantity", "updated_at")
    readonly_fields = [f.name for f in StockBalance._meta.fields]
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(StockLedger)
class StockLedgerAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "operating_point", "movement_type", "quantity_delta", "balance_after")
    readonly_fields = [f.name for f in StockLedger._meta.fields]
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


from .models import StockAlertRule, StockAlert, StockAlertNotification

@admin.register(StockAlertRule)
class StockAlertRuleAdmin(admin.ModelAdmin):
    list_display = ("product", "operating_point", "minimum_quantity",
                    "target_quantity", "is_active", "updated_at")
    list_filter = ("is_active", "operating_point")
    search_fields = ("product__name", "product__code", "product__brand")


@admin.register(StockAlert)
class StockAlertAdmin(admin.ModelAdmin):
    list_display = ("product", "operating_point", "status",
                    "triggered_at", "resolved_at", "next_reminder_at")
    list_filter = ("status", "operating_point")
    readonly_fields = [f.name for f in StockAlert._meta.fields]


@admin.register(StockAlertNotification)
class StockAlertNotificationAdmin(admin.ModelAdmin):
    list_display = ("alert", "recipient", "created_at")
