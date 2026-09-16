from django.urls import path

from apps.salaries import views

app_name = "salaries"
urlpatterns = [
    path("", views.salary_list, name="list"),
    path("export/csv/", views.salary_export_csv, name="export_csv"),
]
