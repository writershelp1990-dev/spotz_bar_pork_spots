from django.urls import path

from . import business_day_views

app_name = "business_day"

urlpatterns = [
    path("", business_day_views.business_day_dashboard, name="dashboard"),
    path("open/", business_day_views.open_business_day_view, name="open"),
    path(
        "<int:pk>/close/",
        business_day_views.close_business_day_view,
        name="close",
    ),
    path(
        "<int:pk>/reopen/",
        business_day_views.reopen_business_day_view,
        name="reopen",
    ),
    path(
        "holidays/",
        business_day_views.holiday_list,
        name="holidays",
    ),
    path(
        "holidays/add/",
        business_day_views.holiday_create,
        name="holiday_add",
    ),
    path(
        "deployments/add/",
        business_day_views.deployment_create,
        name="deployment_add",
    ),
    path(
        "deployments/<int:pk>/cancel/",
        business_day_views.deployment_cancel,
        name="deployment_cancel",
    ),
    path("holidays/<int:pk>/edit/", business_day_views.holiday_edit, name="holiday_edit"),
    path("holidays/<int:pk>/toggle/", business_day_views.holiday_toggle, name="holiday_toggle"),
]