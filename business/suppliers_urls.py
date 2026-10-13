from django.urls import path
from . import suppliers_views as v

app_name = "business"

urlpatterns = [
    path("suppliers/", v.supplier_list, name="supplier_list"),
    path("suppliers/new/", v.supplier_create, name="supplier_create"),
    path("suppliers/<int:pk>/", v.supplier_detail, name="supplier_detail"),
    path("suppliers/<int:pk>/edit/", v.supplier_edit, name="supplier_edit"),
    path("suppliers/<int:pk>/toggle/", v.supplier_toggle, name="supplier_toggle"),
]