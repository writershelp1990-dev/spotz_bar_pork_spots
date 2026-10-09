from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    path("", views.pos, name="pos"),
    path("receipt/<int:sale_id>/", views.receipt, name="receipt"),
    path("history/", views.sales_history, name="history"),
    path("catalog/", views.catalog_home, name="catalog"),
    path("products/add/", views.product_add, name="product_add"),
    path("products/<int:product_id>/edit/", views.product_edit, name="product_edit"),
    path("categories/add/", views.category_add, name="category_add"),
    path("units/add/", views.unit_add, name="unit_add"),
    path("prices/add/", views.price_add, name="price_add"),
    path("stock/", views.stock_home, name="stock"),
    path("stock/receive/", views.stock_receive, name="stock_receive"),
]
