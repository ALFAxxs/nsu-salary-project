from rest_framework.routers import DefaultRouter

from apps.api.views import (
    EmployeeViewSet,
    OrganizationUnitViewSet,
    SalaryImportViewSet,
    SalaryViewSet,
    TelegramMessageViewSet,
)

router = DefaultRouter()
router.register("branches", OrganizationUnitViewSet, basename="api-branches")
router.register("employees", EmployeeViewSet, basename="api-employees")
router.register("salaries", SalaryViewSet, basename="api-salaries")
router.register("imports", SalaryImportViewSet, basename="api-imports")
router.register("notifications", TelegramMessageViewSet, basename="api-notifications")

urlpatterns = router.urls
