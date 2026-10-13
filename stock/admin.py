from django.contrib import admin

from .models import (
    ProductionBatch, ProductionConsumption, Recipe, RecipeItem, StockTransfer,
)


class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 0


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "product", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "product__name", "product__code")
    inlines = [RecipeItemInline]


class ProductionConsumptionInline(admin.TabularInline):
    model = ProductionConsumption
    extra = 0
    readonly_fields = ("ingredient", "quantity", "unit")


@admin.register(ProductionBatch)
class ProductionBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_number", "recipe", "preparation_point",
        "quantity_produced", "status", "produced_by", "produced_at",
    )
    list_filter = ("status", "preparation_point")
    search_fields = ("batch_number", "recipe__name", "output_product__name")
    readonly_fields = ("produced_at",)
    inlines = [ProductionConsumptionInline]


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = (
        "transfer_number", "product", "source_point", "destination_point",
        "quantity", "status", "requested_by", "requested_at",
    )
    list_filter = ("status", "source_point", "destination_point", "transfer_type")
    search_fields = ("transfer_number", "product__name", "reference")
    readonly_fields = ("requested_at",)