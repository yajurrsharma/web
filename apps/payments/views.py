import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.core.utils import client_ip, rate_limited, to_paise
from apps.orders.models import Order

from .gateways import GatewayError
from .services import PaymentError, confirm_checkout, handle_webhook, start_online_payment

logger = logging.getLogger(__name__)


def _own_order(request, order_number):
    return get_object_or_404(Order, order_number=order_number, user=request.user)


@login_required
@require_GET
def pay(request, order_number):
    order = _own_order(request, order_number)
    if order.is_paid:
        return redirect("orders:confirmation", order_number=order.order_number)
    try:
        payment, gateway = start_online_payment(order)
    except (PaymentError, GatewayError) as exc:
        messages.error(request, f"We couldn't start the payment: {exc}")
        return redirect("payments:failed", order_number=order.order_number)
    return render(request, "payments/pay.html", {
        "order": order, "payment": payment,
        # Only the public key id is ever sent to the browser.
        "razorpay_key_id": gateway.key_id, "is_test_mode": gateway.is_test_mode,
        "amount_paise": to_paise(order.grand_total),
    })


@login_required
@require_POST
def callback(request, order_number):
    order = _own_order(request, order_number)
    if rate_limited(f"paycb:{client_ip(request)}", limit=30, window_seconds=600):
        return HttpResponse(status=429)
    error = request.POST.get("error_description")
    if error:
        logger.info("Checkout reported failure for %s", order.order_number)
        messages.error(request, "The payment was not completed. You can try again below.")
        return redirect("payments:failed", order_number=order.order_number)
    try:
        paid = confirm_checkout(
            order,
            gateway_order_id=request.POST.get("razorpay_order_id", ""),
            gateway_payment_id=request.POST.get("razorpay_payment_id", ""),
            signature=request.POST.get("razorpay_signature", ""),
        )
    except (PaymentError, GatewayError) as exc:
        logger.warning("Payment confirmation failed for %s: %s", order.order_number, exc)
        messages.error(request, "We couldn't verify the payment. If money was deducted, it will be "
                                "confirmed automatically within a few minutes or refunded by your bank.")
        return redirect("payments:failed", order_number=order.order_number)
    if paid:
        return redirect("orders:confirmation", order_number=order.order_number)
    messages.info(request, "Your payment is being processed. We'll email you as soon as it's confirmed.")
    return redirect("orders:detail", order_number=order.order_number)


@login_required
@require_GET
def failed(request, order_number):
    order = _own_order(request, order_number)
    if order.is_paid:
        return redirect("orders:confirmation", order_number=order.order_number)
    return render(request, "payments/failed.html", {"order": order,
                                                     "can_retry": order.awaiting_online_payment})


@csrf_exempt  # authenticated by HMAC signature instead
@require_POST
def razorpay_webhook(request):
    status, note = handle_webhook(
        raw_body=request.body,
        signature=request.headers.get("X-Razorpay-Signature", ""),
        event_id=request.headers.get("X-Razorpay-Event-Id", ""),
    )
    if status != 200:
        logger.warning("Webhook rejected (%s): %s", status, note)
    return HttpResponse(status=status)
