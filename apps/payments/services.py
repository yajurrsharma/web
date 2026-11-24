"""
Payment flow:
1. `start_online_payment` creates a Razorpay order on the server for the exact
   order total (from the database) and returns what the checkout widget needs.
2. The browser posts back ids + signature → `confirm_checkout` verifies the
   signature, then fetches the payment from Razorpay to check amount, currency
   and status before marking the order paid.
3. Webhooks (`handle_webhook`) do the same independently — whichever arrives
   first wins; the other is a no-op (idempotent).
4. `reconcile_order` asks Razorpay directly; used before auto-cancelling
   unpaid orders and by the reconciliation command.
"""
import hashlib
import json
import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.utils import audit, money, to_paise
from apps.orders.models import Order

from .gateways import GatewayError, get_gateway
from .models import Payment, Refund, WebhookEvent

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    pass


def start_online_payment(order):
    if not order.awaiting_online_payment:
        raise PaymentError("This order is not awaiting payment.")
    if order.payment_due_by and order.payment_due_by < timezone.now():
        raise PaymentError("The payment window for this order has closed.")
    gateway = get_gateway()
    # Reuse an unpaid gateway order for the same amount (e.g. page refresh).
    payment = order.payments.filter(gateway=Payment.GATEWAY_RAZORPAY, status=Payment.CREATED,
                                    amount=order.grand_total).first()
    if payment is None:
        data = gateway.create_order(amount_paise=to_paise(order.grand_total), receipt=order.order_number,
                                    notes={"order_number": order.order_number})
        if int(data.get("amount", -1)) != to_paise(order.grand_total) or data.get("currency") != "INR":
            raise PaymentError("Payment provider returned an unexpected amount.")
        payment = Payment.objects.create(order=order, gateway=Payment.GATEWAY_RAZORPAY,
                                         gateway_order_id=data["id"], amount=order.grand_total)
    return payment, gateway


def _apply_gateway_payment(order, data):
    """
    Given a Razorpay payment entity (dict) for this order, record it and mark the
    order paid if captured. Validates amount/currency against our own order.
    """
    from apps.orders.services import mark_paid, mark_payment_failed

    gateway_order_id = data.get("order_id")
    payment = Payment.objects.filter(order=order, gateway_order_id=gateway_order_id).first()
    if payment is None:
        logger.warning("Payment for unknown gateway order %s on %s", gateway_order_id, order.order_number)
        return False
    expected = to_paise(order.grand_total)
    if int(data.get("amount", -1)) != expected or data.get("currency") != "INR":
        payment.status = Payment.FAILED
        payment.error_code = "AMOUNT_MISMATCH"
        payment.error_description = f"Expected {expected} paise INR, got {data.get('amount')} {data.get('currency')}"
        payment.save()
        logger.error("Amount mismatch on %s", order.order_number)
        return False
    status = data.get("status")
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status == Payment.CAPTURED:
            if payment.gateway_payment_id != data["id"] and status == "captured":
                # A second captured payment for the same gateway order — record separately for refund.
                Payment.objects.get_or_create(
                    gateway_payment_id=data["id"],
                    defaults={"order": order, "gateway": Payment.GATEWAY_RAZORPAY, "amount": order.grand_total,
                              "status": Payment.CAPTURED, "captured_at": timezone.now(),
                              "error_description": "Duplicate capture — refund required"})
                transaction.on_commit(lambda: _alert_duplicate(order))
            return False  # already captured; later/duplicate events never downgrade it
        payment.gateway_payment_id = data["id"]
        payment.method_detail = (data.get("method") or "")[:30]
        if status == "captured":
            payment.status = Payment.CAPTURED
            payment.captured_at = payment.captured_at or timezone.now()
            payment.error_code = payment.error_description = ""
        elif status == "authorized":
            payment.status = Payment.AUTHORIZED
        elif status == "failed":
            payment.status = Payment.FAILED
            payment.error_code = (data.get("error_code") or "")[:60]
            payment.error_description = (data.get("error_description") or "")[:300]
        try:
            payment.save()
        except IntegrityError:
            return False
    if payment.status == Payment.CAPTURED:
        return mark_paid(order, payment)
    if payment.status == Payment.AUTHORIZED:
        # Auto-capture should be on in the Razorpay dashboard; capture defensively if not.
        try:
            captured = get_gateway().capture(data["id"], expected)
            return _apply_gateway_payment(order, captured)
        except GatewayError:
            logger.warning("Capture failed for %s; webhook/reconcile will retry", order.order_number)
            return False
    if payment.status == Payment.FAILED:
        mark_payment_failed(order, reason=payment.error_description)
    return False


def confirm_checkout(order, *, gateway_order_id, gateway_payment_id, signature):
    """Browser callback. Signature first, then server-side fetch — never trust the redirect alone."""
    gateway = get_gateway()
    if not order.payments.filter(gateway_order_id=gateway_order_id).exists():
        raise PaymentError("Payment does not belong to this order.")
    if not gateway.verify_checkout_signature(gateway_order_id=gateway_order_id,
                                             gateway_payment_id=gateway_payment_id, signature=signature):
        raise PaymentError("Payment verification failed.")
    data = gateway.fetch_payment(gateway_payment_id)
    if data.get("order_id") != gateway_order_id:
        raise PaymentError("Payment does not match this order.")
    _apply_gateway_payment(order, data)
    order.refresh_from_db()
    return order.is_paid


def handle_webhook(raw_body: bytes, signature: str, event_id: str):
    """
    Returns (http_status, note). Signature verified against the raw body. Each
    event id is processed at most once.
    """
    gateway = get_gateway()
    if not gateway.verify_webhook_signature(raw_body=raw_body, signature=signature):
        return 400, "invalid signature"
    try:
        payload = json.loads(raw_body)
    except ValueError:
        return 400, "invalid json"
    event_type = payload.get("event", "")
    event_id = event_id or "sha256:" + hashlib.sha256(raw_body).hexdigest()
    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(event_id=event_id[:80], event_type=event_type[:60])
    except IntegrityError:
        return 200, "duplicate"

    try:
        note = _dispatch(event_type, payload)
        event.status = "processed" if note != "ignored" else "ignored"
        event.note = note[:300]
    except Exception as exc:  # record and let Razorpay retry later
        logger.exception("Webhook %s failed", event_id)
        event.delete()  # allow the retry to be processed
        return 500, f"error: {type(exc).__name__}"
    event.processed_at = timezone.now()
    event.save()
    return 200, event.note


def _dispatch(event_type, payload):
    entity = payload.get("payload", {})
    if event_type in ("payment.captured", "payment.authorized", "payment.failed", "order.paid"):
        pay = entity.get("payment", {}).get("entity", {})
        gateway_order_id = pay.get("order_id")
        payment = Payment.objects.select_related("order").filter(gateway_order_id=gateway_order_id).first()
        if not payment:
            return "ignored"
        _apply_gateway_payment(payment.order, pay)
        return f"{event_type} applied to {payment.order.order_number}"
    if event_type in ("refund.processed", "refund.failed"):
        ref = entity.get("refund", {}).get("entity", {})
        refund = Refund.objects.filter(gateway_refund_id=ref.get("id")).first()
        if not refund:
            return "ignored"
        _set_refund_status(refund, Refund.PROCESSED if event_type == "refund.processed" else Refund.FAILED)
        return f"{event_type} {refund.pk}"
    return "ignored"


def reconcile_order(order):
    """Pull the truth from Razorpay for every gateway order attached to this order."""
    gateway = get_gateway()
    changed = False
    for payment in order.payments.filter(gateway=Payment.GATEWAY_RAZORPAY).exclude(gateway_order_id=None):
        for data in gateway.fetch_order_payments(payment.gateway_order_id):
            if data.get("status") in ("captured", "authorized", "failed"):
                changed |= bool(_apply_gateway_payment(order, data))
    return changed


# ---------------------------------------------------------------- refunds
def _set_refund_status(refund, status):
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund.pk)
        if refund.status == status:
            return refund
        refund.status = status
        if status == Refund.PROCESSED:
            refund.processed_at = timezone.now()
        refund.save()
        _update_order_refund_totals(refund.order)
    if status == Refund.PROCESSED:
        from apps.orders.services import email_customer

        email_customer(refund.order, "refund_update", refund=refund)
    return refund


def _update_order_refund_totals(order):
    from django.db.models import Sum

    order = Order.objects.select_for_update().get(pk=order.pk)
    total = order.refunds.filter(status=Refund.PROCESSED).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    order.refunded_total = money(total)
    if order.refunded_total >= order.grand_total and order.refunded_total > 0:
        order.payment_status = Order.PAY_REFUNDED
    elif order.refunded_total > 0:
        order.payment_status = Order.PAY_PARTIALLY_REFUNDED
    order.save(update_fields=["refunded_total", "payment_status", "updated_at"])


def refund_order(order, *, amount, reason, by=None, manual_reference="", request=None):
    """
    Online orders are refunded through Razorpay. COD orders (already collected)
    are refunded manually — record the bank/UPI reference.
    """
    amount = money(amount)
    if amount <= 0:
        raise PaymentError("Refund amount must be positive.")
    pending_or_done = sum((r.amount for r in order.refunds.exclude(status=Refund.FAILED)), Decimal("0"))
    if amount + pending_or_done > order.grand_total:
        raise PaymentError(f"Refund would exceed the amount paid (₹{order.grand_total - pending_or_done} left).")
    if not order.is_paid:
        raise PaymentError("Nothing has been paid on this order.")

    captured = order.payments.filter(gateway=Payment.GATEWAY_RAZORPAY, status=Payment.CAPTURED).first()
    if captured and not manual_reference:
        data = get_gateway().refund(captured.gateway_payment_id, to_paise(amount),
                                    {"order_number": order.order_number, "reason": reason[:100]})
        refund = Refund.objects.create(order=order, payment=captured, amount=amount, reason=reason[:300],
                                       method=Refund.METHOD_GATEWAY, gateway_refund_id=data.get("id"),
                                       created_by=by)
        if data.get("status") == "processed":
            _set_refund_status(refund, Refund.PROCESSED)
    else:
        if not manual_reference:
            raise PaymentError("Enter the bank/UPI transfer reference for this manual refund.")
        refund = Refund.objects.create(order=order, payment=order.payments.filter(status=Payment.CAPTURED).first(),
                                       amount=amount, reason=reason[:300], method=Refund.METHOD_MANUAL,
                                       reference=manual_reference[:100], created_by=by)
        _set_refund_status(refund, Refund.PROCESSED)
    audit("refund.create", f"Refund ₹{amount} for {order.order_number} ({refund.method})", actor=by, obj=order,
          request=request)
    return refund


def _alert_duplicate(order):
    from apps.core.emails import notify_admins

    notify_admins("late_payment_alert", {"order": order, "duplicate": True}, related=f"order:{order.order_number}")
