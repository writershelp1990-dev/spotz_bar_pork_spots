from django.contrib import admin
from django.urls import include, path
from .views import dashboard

urlpatterns = [
    path("sales/", include("sales.urls")),
    path('accounts/', include('accounts.urls')),
    path("audit/", include("audit.urls")),
    path("admin/", admin.site.urls),
    path("", dashboard, name="dashboard"),
    # Phase 1 Business-Day management.
    path("business-day/", include("business.business_day_urls")),
    path("property/", include("property.urls")),
    path("stock/", include("stock.urls")),
]