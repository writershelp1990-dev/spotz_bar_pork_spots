from django.contrib import admin

from .models import (
    Category,
    Product,
    ProductPriceHistory,
    UnitOfMeasure,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "created_at",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "name",
    )


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "abbreviation",
        "unit_type",
        "is_active",
    )

    list_filter = (
        "unit_type",
        "is_active",
    )

    search_fields = (
        "name",
        "abbreviation",
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "category",
        "product_type",
        "purchase_unit",
        "selling_unit",
        "purchase_to_sale_ratio",
        "is_active",
        "track_inventory",
    )

    list_filter = (
        "product_type",
        "category",
        "is_active",
        "track_inventory",
    )

    search_fields = (
        "name",
        "code",
    )


@admin.register(ProductPriceHistory)
class ProductPriceHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "price_type",
        "unit",
        "price",
        "effective_from",
        "effective_to",
    )

    list_filter = (
        "price_type",
        "unit",
    )

    search_fields = (
        "product__name",
        "product__code",
    )
