from django.urls import path

from apps.reports import views

app_name = "reports"
urlpatterns = [
    path("", views.report_index, name="index"),
]
