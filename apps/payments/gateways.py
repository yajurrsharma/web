"""
Payment gateway abstraction. The rest of the app talks to `get_gateway()` only,
so another provider can be added by writing one more class.
"""
import hashlib
import hmac
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    pass


class PaymentGateway:
    name = "base"

    def create_order(self, *, amount_paise, receipt, notes):  # pragma: no cover - interface
        raise NotImplementedError

    def verify_checkout_signature(self, *, gateway_order_id, gateway_payment_id, signature):  # pragma: no cover
        raise NotImplementedError

    def verify_webhook_signature(self, *, raw_body, signature):  # pragma: no cover
        raise NotImplementedError

    def fetch_payment(self, payment_id):  # pragma: no cover
        raise NotImplementedError

    def fetch_order_payments(self, gateway_order_id):  # pragma: no cover
        raise NotImplementedError

    def capture(self, payment_id, amount_paise):  # pragma: no cover
        raise NotImplementedError

    def refund(self, payment_id, amount_paise, notes):  # pragma: no cover
        raise NotImplementedError


def _hmac_sha256(secret, message: bytes):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class RazorpayGateway(PaymentGateway):
    """
    Razorpay via the official SDK for API calls. Signature checks use HMAC-SHA256
    exactly as Razorpay documents them, with constant-time comparison:
      checkout: HMAC(key_secret, "<razorpay_order_id>|<razorpay_payment_id>")
      webhook:  HMAC(webhook_secret, <raw request body>)
    """

    name = "razorpay"

    def __init__(self, key_id=None, key_secret=None, webhook_secret=None):
        self.key_id = key_id or settings.RAZORPAY_KEY_ID
        self.key_secret = key_secret or settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = webhook_secret or settings.RAZORPAY_WEBHOOK_SECRET
        self._client = None

    @property
    def is_test_mode(self):
        return self.key_id.startswith("rzp_test_")

    @property
    def client(self):
        if self._client is None:
            if not (self.key_id and self.key_secret):
                raise GatewayError("Razorpay keys are not configured.")
            import razorpay

            self._client = razorpay.Client(auth=(self.key_id, self.key_secret))
            self._client.set_app_details({"title": "LifeLineDynamics-Django", "version": "1.0"})
        return self._client

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # SDK raises several error classes; normalise them
            logger.warning("Razorpay API error: %s", type(exc).__name__)
            raise GatewayError(str(exc)[:200]) from exc

    def create_order(self, *, amount_paise, receipt, notes):
        return self._call(self.client.order.create, {
            "amount": amount_paise, "currency": "INR", "receipt": receipt[:40], "notes": notes,
            "payment_capture": 1,
        })

    def verify_checkout_signature(self, *, gateway_order_id, gateway_payment_id, signature):
        if not (gateway_order_id and gateway_payment_id and signature and self.key_secret):
            return False
        expected = _hmac_sha256(self.key_secret, f"{gateway_order_id}|{gateway_payment_id}".encode())
        return hmac.compare_digest(expected, signature)

    def verify_webhook_signature(self, *, raw_body, signature):
        if not (raw_body and signature and self.webhook_secret):
            return False
        return hmac.compare_digest(_hmac_sha256(self.webhook_secret, raw_body), signature)

    def fetch_payment(self, payment_id):
        return self._call(self.client.payment.fetch, payment_id)

    def fetch_order_payments(self, gateway_order_id):
        data = self._call(self.client.order.payments, gateway_order_id)
        return data.get("items", [])

    def capture(self, payment_id, amount_paise):
        return self._call(self.client.payment.capture, payment_id, amount_paise, {"currency": "INR"})

    def refund(self, payment_id, amount_paise, notes):
        return self._call(self.client.payment.refund, payment_id, {"amount": amount_paise, "notes": notes,
                                                                    "speed": "normal"})


def get_gateway():
    return RazorpayGateway()
