from django.urls import path

from . import views

app_name = "shipping"

# Neutral path on purpose (no brand names in it), e.g. https://your-domain/hooks/courier-updates/
urlpatterns = [
    path("courier-updates/", views.tracking_webhook, name="webhook"),
]
