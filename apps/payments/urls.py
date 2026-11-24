from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("pay/<str:order_number>/", views.pay, name="pay"),
    path("callback/<str:order_number>/", views.callback, name="callback"),
    path("failed/<str:order_number>/", views.failed, name="failed"),
    path("webhooks/razorpay/", views.razorpay_webhook, name="razorpay_webhook"),
]
