from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("checkout/", views.checkout, name="checkout"),
    path("checkout/start/", views.checkout_start, name="checkout_start"),
    path("checkout/pincode/<str:pin>/", views.pincode_lookup, name="pincode"),
    path("delivery-check/<str:pin>/", views.delivery_check_view, name="delivery_check"),
    path("track-order/", views.track, name="track"),
    path("account/orders/", views.order_list, name="list"),
    path("account/orders/<str:order_number>/", views.order_detail, name="detail"),
    path("account/orders/<str:order_number>/thank-you/", views.confirmation, name="confirmation"),
    path("account/orders/<str:order_number>/payment-reference/", views.payment_reference,
         name="payment_reference"),
    path("account/orders/<str:order_number>/cancel/", views.cancel, name="cancel"),
    path("account/orders/<str:order_number>/return/", views.request_return, name="request_return"),
]
