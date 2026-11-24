"""All gateway calls are mocked. No real Razorpay credentials are used in tests."""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.testing import configure_store, make_product, make_user, place_test_order
from apps.orders.models import Order

from .gateways import RazorpayGateway
from .models import Payment, Refund, WebhookEvent
from .services import PaymentError, confirm_checkout, handle_webhook, refund_order, start_online_payment


def sign(message: bytes, secret: str):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def payment_entity(order, pay_id="pay_TEST1", status="captured", amount=None, gw_order="order_TEST1"):
    return {"id": pay_id, "order_id": gw_order, "amount": amount if amount is not None else int(order.grand_total * 100),
            "currency": "INR", "status": status, "method": "upi"}


class FakeGateway(RazorpayGateway):
    """Real signature logic; API calls answered from memory."""

    def __init__(self):
        super().__init__(key_id="rzp_test_dummy", key_secret=settings.RAZORPAY_KEY_SECRET,
                         webhook_secret=settings.RAZORPAY_WEBHOOK_SECRET)
        self.payments = {}
        self.order_payments = {}
        self.created = []
        self.refunds = []

    def create_order(self, *, amount_paise, receipt, notes):
        gid = f"order_TEST{len(self.created) + 1}"
        self.created.append((gid, amount_paise))
        return {"id": gid, "amount": amount_paise, "currency": "INR", "status": "created"}

    def fetch_payment(self, payment_id):
        return self.payments[payment_id]

    def fetch_order_payments(self, gateway_order_id):
        return self.order_payments.get(gateway_order_id, [])

    def capture(self, payment_id, amount_paise):
        p = dict(self.payments[payment_id], status="captured")
        self.payments[payment_id] = p
        return p

    def refund(self, payment_id, amount_paise, notes):
        rid = f"rfnd_TEST{len(self.refunds) + 1}"
        self.refunds.append((payment_id, amount_paise))
        return {"id": rid, "amount": amount_paise, "status": "pending"}


@override_settings(PAYMENTS_ENABLED=True)
class PaymentFlowTests(TestCase):
    def setUp(self):
        configure_store()
        self.user = make_user()
        self.variant = make_product(price="105.00", stock=5)
        self.order = place_test_order(self.user, self.variant, method="online")
        self.gw = FakeGateway()
        patcher = mock.patch("apps.payments.services.get_gateway", return_value=self.gw)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self):
        payment, _ = start_online_payment(self.order)
        return payment

    def test_gateway_order_created_for_server_amount(self):
        payment = self.start()
        self.assertEqual(self.gw.created[0][1], 15500)  # ₹105 + ₹50 delivery, in paise
        self.assertEqual(payment.amount, self.order.grand_total)
        self.start()  # reuse, not a second gateway order
        self.assertEqual(len(self.gw.created), 1)

    def test_valid_signature_marks_paid(self):
        payment = self.start()
        self.gw.payments["pay_TEST1"] = payment_entity(self.order, gw_order=payment.gateway_order_id)
        sig = sign(f"{payment.gateway_order_id}|pay_TEST1".encode(), settings.RAZORPAY_KEY_SECRET)
        self.assertTrue(confirm_checkout(self.order, gateway_order_id=payment.gateway_order_id,
                                         gateway_payment_id="pay_TEST1", signature=sig))
        self.order.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.order.status), (Order.PAY_PAID, Order.CONFIRMED))

    def test_bad_signature_rejected(self):
        payment = self.start()
        self.gw.payments["pay_TEST1"] = payment_entity(self.order, gw_order=payment.gateway_order_id)
        with self.assertRaises(PaymentError):
            confirm_checkout(self.order, gateway_order_id=payment.gateway_order_id, gateway_payment_id="pay_TEST1",
                             signature="0" * 64)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PAY_PENDING)

    def test_amount_mismatch_not_marked_paid(self):
        payment = self.start()
        self.gw.payments["pay_TEST1"] = payment_entity(self.order, amount=100, gw_order=payment.gateway_order_id)
        sig = sign(f"{payment.gateway_order_id}|pay_TEST1".encode(), settings.RAZORPAY_KEY_SECRET)
        confirm_checkout(self.order, gateway_order_id=payment.gateway_order_id, gateway_payment_id="pay_TEST1",
                         signature=sig)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)
        self.assertEqual(Payment.objects.get(pk=payment.pk).error_code, "AMOUNT_MISMATCH")

    def test_payment_for_another_order_rejected(self):
        payment = self.start()
        other = place_test_order(make_user(email="o@example.com"), self.variant, method="online")
        with self.assertRaises(PaymentError):
            confirm_checkout(other, gateway_order_id=payment.gateway_order_id, gateway_payment_id="pay_X",
                             signature="x")

    def test_browser_redirect_alone_does_not_mark_paid(self):
        self.client.force_login(self.user)
        self.start()
        resp = self.client.post(reverse("payments:callback", args=[self.order.order_number]), {})
        self.assertRedirects(resp, reverse("payments:failed", args=[self.order.order_number]))
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)

    def test_failed_then_retry(self):
        payment = self.start()
        self.gw.order_payments[payment.gateway_order_id] = [
            payment_entity(self.order, pay_id="pay_F", status="failed", gw_order=payment.gateway_order_id)]
        from .services import reconcile_order

        reconcile_order(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PAY_FAILED)
        self.assertTrue(self.order.awaiting_online_payment)  # can retry

    def webhook(self, event, entity, event_id="evt_1", secret=None):
        body = json.dumps({"event": event, "payload": {"payment": {"entity": entity}}, "created_at": 1}).encode()
        return handle_webhook(body, sign(body, secret or settings.RAZORPAY_WEBHOOK_SECRET), event_id)

    def test_webhook_marks_paid_and_is_idempotent(self):
        payment = self.start()
        entity = payment_entity(self.order, gw_order=payment.gateway_order_id)
        self.assertEqual(self.webhook("payment.captured", entity)[0], 200)
        self.assertEqual(self.webhook("payment.captured", entity), (200, "duplicate"))
        self.assertEqual(self.webhook("order.paid", entity, event_id="evt_2")[0], 200)  # different event, same payment
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PAY_PAID)
        self.assertEqual(self.order.history.filter(field="payment", to_value="paid").count(), 1)
        self.assertEqual(WebhookEvent.objects.count(), 2)

    def test_webhook_bad_signature_rejected(self):
        payment = self.start()
        status, _ = self.webhook("payment.captured", payment_entity(self.order, gw_order=payment.gateway_order_id),
                                 secret="wrong-secret")
        self.assertEqual(status, 400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)

    def test_webhook_view_uses_raw_body_and_skips_csrf(self):
        payment = self.start()
        body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": payment_entity(
            self.order, gw_order=payment.gateway_order_id)}}}).encode()
        client = self.client_class(enforce_csrf_checks=True)
        resp = client.post(reverse("payments:razorpay_webhook"), body, content_type="application/json",
                           HTTP_X_RAZORPAY_SIGNATURE=sign(body, settings.RAZORPAY_WEBHOOK_SECRET),
                           HTTP_X_RAZORPAY_EVENT_ID="evt_view")
        self.assertEqual(resp.status_code, 200)
        resp = client.post(reverse("payments:razorpay_webhook"), body, content_type="application/json",
                           HTTP_X_RAZORPAY_SIGNATURE="bad")
        self.assertEqual(resp.status_code, 400)

    def test_failed_event_after_capture_does_not_downgrade(self):
        payment = self.start()
        self.webhook("payment.captured", payment_entity(self.order, gw_order=payment.gateway_order_id))
        self.webhook("payment.failed", payment_entity(self.order, pay_id="pay_OTHER", status="failed",
                                                      gw_order=payment.gateway_order_id), event_id="evt_9")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PAY_PAID)

    def test_late_payment_on_cancelled_order_flags_refund(self):
        payment = self.start()
        from apps.orders.services import cancel_order

        cancel_order(self.order, reason="timeout")
        self.webhook("payment.captured", payment_entity(self.order, gw_order=payment.gateway_order_id))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.CANCELLED)
        self.assertEqual(self.order.payment_status, Order.PAY_PAID)
        self.assertIn("refund required", self.order.admin_notes)

    def test_refund_via_gateway_and_webhook(self):
        payment = self.start()
        self.webhook("payment.captured", payment_entity(self.order, gw_order=payment.gateway_order_id))
        self.order.refresh_from_db()
        refund = refund_order(self.order, amount=Decimal("55.00"), reason="partial")
        self.assertEqual((refund.status, self.gw.refunds[0][1]), (Refund.PENDING, 5500))
        with self.assertRaises(PaymentError):
            refund_order(self.order, amount=Decimal("101.00"), reason="too much")
        body = json.dumps({"event": "refund.processed", "payload": {"refund": {"entity": {
            "id": refund.gateway_refund_id}}}}).encode()
        handle_webhook(body, sign(body, settings.RAZORPAY_WEBHOOK_SECRET), "evt_r")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PAY_PARTIALLY_REFUNDED)
        self.assertEqual(self.order.refunded_total, Decimal("55.00"))


class SignatureHelperTests(TestCase):
    def test_constant_time_signature_checks(self):
        gw = RazorpayGateway(key_id="rzp_test_x", key_secret="sek", webhook_secret="whs")
        good = sign(b"order_1|pay_1", "sek")
        self.assertTrue(gw.verify_checkout_signature(gateway_order_id="order_1", gateway_payment_id="pay_1",
                                                     signature=good))
        self.assertFalse(gw.verify_checkout_signature(gateway_order_id="order_1", gateway_payment_id="pay_2",
                                                      signature=good))
        self.assertFalse(gw.verify_checkout_signature(gateway_order_id="", gateway_payment_id="", signature=""))
        self.assertTrue(gw.verify_webhook_signature(raw_body=b"{}", signature=sign(b"{}", "whs")))
        self.assertFalse(gw.verify_webhook_signature(raw_body=b"{ }", signature=sign(b"{}", "whs")))

    def test_secret_key_never_rendered(self):
        configure_store()
        user = make_user()
        order = place_test_order(user, make_product(stock=2), method="online")
        gw = FakeGateway()
        self.client.force_login(user)
        with mock.patch("apps.payments.services.get_gateway", return_value=gw):
            resp = self.client.get(reverse("payments:pay", args=[order.order_number]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "rzp_test_dummy")
        self.assertNotContains(resp, settings.RAZORPAY_KEY_SECRET)
        self.assertNotContains(resp, settings.RAZORPAY_WEBHOOK_SECRET)


class CODIndependenceTests(TestCase):
    @override_settings(PAYMENTS_ENABLED=False, RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
    def test_cod_works_without_gateway(self):
        configure_store()
        order = place_test_order(make_user(), make_product(stock=2), method="cod")
        self.assertEqual(order.status, Order.CONFIRMED)
