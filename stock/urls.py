from django.urls import path
from . import views

app_name = "stock"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # Transfers
    path("transfers/", views.transfer_list, name="transfer_list"),
    path("transfers/new/", views.transfer_request, name="transfer_request"),
    path("transfers/<int:pk>/", views.transfer_detail, name="transfer_detail"),
    path("transfers/<int:pk>/approve/", views.transfer_approve, name="transfer_approve"),
    path("transfers/<int:pk>/complete/", views.transfer_complete, name="transfer_complete"),
    path("transfers/<int:pk>/cancel/", views.transfer_cancel, name="transfer_cancel"),

    # Recipes
    path("recipes/", views.recipe_list, name="recipe_list"),
    path("recipes/new/", views.recipe_create, name="recipe_create"),

    # Production
    path("production/", views.batch_list, name="batch_list"),
    path("production/new/", views.batch_create, name="batch_create"),

    # Wastage
    path("wastage/new/", views.wastage_create, name="wastage_create"),

    # Reconciliation
    path("reconciliation/", views.reconciliation, name="reconciliation"),
]