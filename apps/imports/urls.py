from django.urls import path

from apps.imports import views

app_name = "imports"
urlpatterns = [
    path("", views.import_list, name="list"),
    path("upload/", views.import_upload, name="upload"),
    path("template/", views.download_template, name="template"),
    path("<int:pk>/preview/", views.import_preview, name="preview"),
    path("<int:pk>/confirm/", views.import_confirm, name="confirm"),
    path("<int:pk>/cancel/", views.import_cancel, name="cancel"),
    path("<int:pk>/errors/", views.download_error_report, name="error_report"),
]
