from django.urls import path
from . import views

app_name = "expenses"

urlpatterns = [
    path("", views.expense_list, name="expense_list"),
    path("new/", views.expense_create, name="expense_create"),
    path("<int:pk>/", views.expense_detail, name="expense_detail"),
    path("<int:pk>/edit/", views.expense_edit, name="expense_edit"),
    path("<int:pk>/approve/", views.expense_approve, name="expense_approve"),
    path("<int:pk>/reject/", views.expense_reject, name="expense_reject"),
    path("<int:pk>/delete/", views.expense_delete, name="expense_delete"),
]
