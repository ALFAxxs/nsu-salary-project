from django.urls import path

from apps.notifications import views

app_name = "notifications"
urlpatterns = [
    path("", views.notification_list, name="list"),
    path("import/<int:pk>/", views.import_detail, name="import_detail"),
    path("import/<int:pk>/send/", views.send_notifications, name="send"),
    path("message/<int:pk>/resend/", views.resend_message, name="resend"),
    path("broadcast/", views.broadcast_list, name="broadcast_list"),
    path("broadcast/new/", views.broadcast_create, name="broadcast_create"),
    path("broadcast/<int:pk>/", views.broadcast_detail, name="broadcast_detail"),
]
