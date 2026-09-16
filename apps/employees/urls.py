from django.urls import path

from apps.employees import views

app_name = "employees"
urlpatterns = [
    path("", views.employee_list, name="list"),
    path("new/", views.employee_create, name="create"),
    path("unconnected/", views.unconnected_list, name="unconnected"),
    path("telegram-users/", views.telegram_users_list, name="telegram_users"),
    path("telegram-users/<int:pk>/delete/", views.telegram_user_delete, name="telegram_user_delete"),
    path("<int:pk>/", views.employee_detail, name="detail"),
    path("<int:pk>/edit/", views.employee_edit, name="edit"),
    path("<int:pk>/toggle-active/", views.employee_toggle_active, name="toggle_active"),
    path("<int:pk>/delete/", views.employee_delete, name="delete"),
    path("<int:pk>/unlink/", views.unlink_telegram, name="unlink"),
]
