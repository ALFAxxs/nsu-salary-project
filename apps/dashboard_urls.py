from django.urls import path

from apps import dashboard_views

app_name = "dashboard"
urlpatterns = [
    path("", dashboard_views.index, name="index"),
    path("raqam-soz/", dashboard_views.number_to_words, name="number_to_words"),
]
