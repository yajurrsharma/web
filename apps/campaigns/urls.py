from django.urls import path

from . import views

app_name = "campaigns"

urlpatterns = [
    path("unsubscribe/<str:token>/", views.unsubscribe, name="unsubscribe"),
]
