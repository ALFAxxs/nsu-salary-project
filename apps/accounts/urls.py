from django.urls import path

from apps.accounts import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.AppLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("admins/", views.admin_list, name="admin_list"),
    path("admins/new/", views.admin_create, name="admin_create"),
    path("admins/<int:pk>/edit/", views.admin_edit, name="admin_edit"),
]
