from django.urls import path

from . import views
from . import employee_management as employee_views
from . import role_management
from . import operating_point_management


urlpatterns = [
    path("staff/directory/", views.staff_directory, name="staff_directory"),
    path("staff/<int:pk>/", views.staff_profile, name="staff_profile"),
    path("employees/", employee_views.employee_directory, name="employee_directory"),
    path("employees/<int:pk>/", employee_views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/edit/", employee_views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/warning/", employee_views.employee_warning, name="employee_warning"),
    path("employees/<int:pk>/suspend/", employee_views.employee_suspend, name="employee_suspend"),
    path("employees/<int:pk>/activate/", employee_views.employee_activate, name="employee_activate"),
    path("employees/<int:pk>/leave/", employee_views.employee_leave, name="employee_leave"),
    path("employees/<int:pk>/terminate/", employee_views.employee_terminate, name="employee_terminate"),

    path("staff/roles/", role_management.manage_roles, name="manage_roles"),
    path("staff/roles/create/", role_management.create_role, name="create_role"),
    path(
        "staff/roles/<int:role_id>/edit/",
        role_management.edit_role,
        name="edit_role",
    ),
    path(
        "staff/roles/<int:role_id>/toggle/",
        role_management.toggle_role,
        name="toggle_role",
    ),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
    path(
    "password/reset/",
    views.SpotzPasswordResetView.as_view(),
    name="password_reset",
),
path(
    "password/reset/done/",
    views.SpotzPasswordResetDoneView.as_view(),
    name="password_reset_done",
),
path(
    "password/reset/<uidb64>/<token>/",
    views.SpotzPasswordResetConfirmView.as_view(),
    name="password_reset_confirm",
),
path(
    "password/reset/complete/",
    views.SpotzPasswordResetCompleteView.as_view(),
    name="password_reset_complete",
),path("profile/password/", views.password_change_view, name="password_change"),
    path("staff/", views.manage_staff, name="manage_staff"),
    path(
        "operating-points/",
        operating_point_management.manage_operating_points,
        name="manage_operating_points",
    ),
    path(
        "operating-points/add/",
        operating_point_management.create_operating_point,
        name="create_operating_point",
    ),
    path(
        "operating-points/<int:pk>/edit/",
        operating_point_management.edit_operating_point,
        name="edit_operating_point",
    ),
    path(
        "operating-points/<int:pk>/toggle/",
        operating_point_management.toggle_operating_point,
        name="toggle_operating_point",
    ),
    path("staff/invite/", views.invite_staff, name="invite_staff"),
path(
    "staff/invite/success/",
    views.invitation_success,
    name="invitation_success",
),
    path(
        "staff/invitation/<str:token>/",
        views.complete_invitation,
        name="complete_invitation",
    ),
]






