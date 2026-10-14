from django.urls import path
from . import views

app_name = "property"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # Asset register
    path("assets/", views.asset_list, name="asset_list"),
    path("assets/new/", views.asset_create, name="asset_create"),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("assets/<int:pk>/edit/", views.asset_edit, name="asset_edit"),
    path("assets/<int:pk>/dispose/", views.asset_dispose, name="asset_dispose"),
    path("assets/<int:asset_pk>/condition/", views.condition_create, name="condition_create"),
    path("assets/<int:asset_pk>/relocate/", views.relocation_request, name="relocation_request"),

    # Checks
    path("checks/", views.check_list, name="check_list"),
    path("checks/new/", views.check_create, name="check_create"),

    # New-item reports
    path("reports/new-items/", views.new_item_list, name="new_item_list"),
    path("reports/new-items/new/", views.new_item_create, name="new_item_create"),
    path("reports/new-items/<int:pk>/review/", views.new_item_review, name="new_item_review"),

    # Condition reports
    path("reports/conditions/", views.condition_list, name="condition_list"),
    path("reports/conditions/<int:pk>/resolve/", views.condition_resolve, name="condition_resolve"),

    # Relocation
    path("relocations/", views.relocation_list, name="relocation_list"),
    path("relocations/<int:pk>/", views.relocation_detail, name="relocation_detail"),
    path("relocations/<int:pk>/decide/", views.relocation_decide, name="relocation_decide"),
    path("relocations/<int:pk>/recipient/", views.relocation_recipient_decide, name="relocation_recipient_decide"),
    path("relocations/<int:pk>/receipt/", views.relocation_confirm_receipt, name="relocation_confirm_receipt"),
    path("relocations/<int:pk>/exception/", views.relocation_resolve_exception, name="relocation_resolve_exception"),
    path("relocations/approvals/", views.approval_queue, name="approval_queue"),

    # Reports
    path("director/reports/", views.reports, name="reports"),
    path("director/reports.csv", views.reports_csv, name="reports_csv"),
    path("staff-access/", views.staff_access, name="staff_access"),
    path("staff-access/update/", views.staff_access_update, name="staff_access_update"),    path("director/reports.pdf", views.reports_pdf, name="reports_pdf"),
    path("director/reports.xlsx", views.reports_xlsx, name="reports_xlsx"),    path("api/pending/", views.api_pending_property, name="api_pending"),
    path("api/ack/<str:key>/", views.api_ack_property, name="api_ack"),
    path("director/reports/movements/", views.movement_report, name="movement_report"),
]