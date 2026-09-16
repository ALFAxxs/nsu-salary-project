from django.urls import path

from apps.organizations import views

app_name = "organizations"
urlpatterns = [
    path("", views.unit_list, name="list"),
    path("new/", views.unit_create, name="create"),
    path("<int:pk>/edit/", views.unit_edit, name="edit"),
    path("<int:pk>/toggle-active/", views.unit_toggle_active, name="toggle_active"),
    path("<int:pk>/delete/", views.unit_delete, name="delete"),
]
