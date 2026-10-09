from django.contrib import admin

from .models import Purchase, PurchaseItem, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "contact_person",
        "phone",
        "is_active",
        "created_at",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "name",
        "contact_person",
        "phone",
        "tax_number",
    )


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "purchase_number",
        "supplier",
        "operating_point",
        "purchase_date",
        "status",
        "created_by",
    )

    list_filter = (
        "status",
        "operating_point",
        "supplier",
    )

    search_fields = (
        "purchase_number",
        "invoice_number",
        "supplier__name",
    )

    inlines = [
        PurchaseItemInline,
    ]


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = (
        "purchase",
        "product",
        "quantity",
        "unit",
        "unit_cost",
        "line_total",
    )

    search_fields = (
        "purchase__purchase_number",
        "product__name",
    )
