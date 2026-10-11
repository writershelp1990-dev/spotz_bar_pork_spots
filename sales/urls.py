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

    # ---- M-Pesa STK Push ----
    path("mpesa/pay/<int:sale_id>/", views.mpesa_pay, name="mpesa_pay"),
    path("mpesa/wait/<int:sale_id>/", views.mpesa_wait, name="mpesa_wait"),
    path("mpesa/status/<int:sale_id>/", views.mpesa_status, name="mpesa_status"),
    path("mpesa/callback/", views.mpesa_callback, name="mpesa_callback"),

    # ---- Open sales (unpaid held sales) ----
    path("api/open-sales/", views.api_open_sales, name="api_open_sales"),

    # ---- Batch receipt ----
    path("batch-receipt/<int:payment_id>/", views.batch_receipt, name="batch_receipt"),
]
