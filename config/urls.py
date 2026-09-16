"""Root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("apps.dashboard_urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("employees/", include("apps.employees.urls")),
    path("salaries/", include("apps.salaries.urls")),
    path("imports/", include("apps.imports.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("reports/", include("apps.reports.urls")),
    path("organizations/", include("apps.organizations.urls")),
    path("audit/", include("apps.audit.urls")),
    path("api/", include("apps.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
