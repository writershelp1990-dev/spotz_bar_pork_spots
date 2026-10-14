from django.urls import path
from . import views
from business import views as business_views

app_name = "stock"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # Transfers
    path("transfers/", views.transfer_list, name="transfer_list"),
    path("transfers/new/", views.transfer_request, name="transfer_request"),
    path("transfers/<int:pk>/", views.transfer_detail, name="transfer_detail"),
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
    path("transfers/<int:pk>/accept/", views.transfer_accept, name="transfer_accept"),
    path("transfers/<int:pk>/sender-reject/", views.transfer_sender_reject, name="transfer_sender_reject"),
    path("transfers/<int:pk>/acknowledge/", views.transfer_recipient_acknowledge, name="transfer_recipient_acknowledge"),
    path("transfers/<int:pk>/recipient-reject/", views.transfer_recipient_reject, name="transfer_recipient_reject"),
    path("api/pending-transfers/", views.api_pending_transfers, name="api_pending_transfers"),
    path("points/<str:code>/", views.point_detail, name="point_detail"),
    path("points/<str:code>/open/",  business_views.point_open,  name="point_open"),
    path("points/<str:code>/close/", business_views.point_close, name="point_close"),
    path("wastage/", views.wastage_list, name="wastage_list"),
]