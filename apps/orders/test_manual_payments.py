"""Direct UPI / bank transfer orders: placed → customer enters UTR → staff confirm → paid."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteSettings
from apps.core.testing import configure_store, make_product, make_user, place_test_order
from apps.invoicing.models import Invoice
from apps.payments.models import Payment

from .models import Order, OrderRequest
from .services import (CheckoutError, OrderActionError, change_status, confirm_manual_payment,
                       expire_unpaid_orders, submit_payment_reference)

UPI = dict(upi_enabled=True, upi_id="lifeline@okhdfcbank", upi_payee_name="Life Line Dynamics")
BANK = dict(bank_transfer_enabled=True, bank_account_name="Life Line Dynamics", bank_account_number="1234567890",
            bank_ifsc="HDFC0001234", bank_name="HDFC Bank")


@override_settings(ADMIN_ALERT_EMAILS=["owner@example.com"])
class ManualPaymentTests(TestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        configure_store(**UPI, **BANK, invoice_trigger=SiteSettings.INVOICE_ON_PAYMENT)
        self.buyer = make_user()
        self.variant = make_product(price="250.00", mrp="300.00", stock=5)
        self.staff = make_user(email="mgr@example.com", is_staff=True)
        self.staff.groups.add(Group.objects.get(name="Store Manager"))

    def test_upi_order_full_flow(self):
        with self.captureOnCommitCallbacks(execute=True):
            order = place_test_order(self.buyer, self.variant, method="upi")
        self.assertEqual((order.status, order.payment_status), (Order.PENDING, Order.PAY_PENDING))
        self.assertTrue(order.payment_due_by > timezone.now() + timedelta(hours=23))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 4)  # reserved while waiting for money
        self.assertIn("lifeline@okhdfcbank", mail.outbox[-1].body)

        self.client.force_login(self.buyer)
        page = self.client.get(reverse("orders:confirmation", args=[order.order_number]))
        self.assertContains(page, "upi://pay?pa=lifeline@okhdfcbank")
        self.assertContains(page, "<svg")  # QR code
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("orders:payment_reference", args=[order.order_number]),
                             {"reference": " 4321 9876 5432 "})
        order.refresh_from_db()
        self.assertEqual(order.payment_reference, "432198765432")
        self.assertTrue(any("432198765432" in m.body for m in mail.outbox if "owner@example.com" in m.to))

        # Staff cannot ship before confirming the money.
        with self.assertRaises(OrderActionError):
            change_status(order, Order.PROCESSING, by=self.staff)
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("dashboard:order_action", args=[order.order_number]),
                             {"action": "manual_paid", "reference": "432198765432"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), (Order.CONFIRMED, Order.PAY_PAID))
        p = Payment.objects.get(order=order)
        self.assertEqual((p.gateway, p.status, p.recorded_by), (Payment.GATEWAY_UPI, Payment.CAPTURED, self.staff))
        self.assertTrue(Invoice.objects.filter(order=order).exists())  # trigger = on payment
        confirm_manual_payment(order, by=self.staff)  # idempotent: no second payment row
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)

    def test_bank_order_shows_account_details(self):
        order = place_test_order(self.buyer, self.variant, method="bank")
        self.client.force_login(self.buyer)
        page = self.client.get(order.get_absolute_url())
        self.assertContains(page, "HDFC0001234")
        self.assertContains(page, "1234567890")

    def test_disabled_method_rejected(self):
        configure_store(upi_enabled=False)
        with self.assertRaises(CheckoutError):
            place_test_order(self.buyer, self.variant, method="upi")

    def test_expiry_cancels_only_orders_without_reference(self):
        silent = place_test_order(self.buyer, self.variant, method="upi", token="a")
        reported = place_test_order(make_user(email="b2@example.com"), self.variant, method="bank", token="b")
        submit_payment_reference(reported, reported.user, "UTR123456789")
        Order.objects.update(payment_due_by=timezone.now() - timedelta(minutes=1))
        expire_unpaid_orders()
        silent.refresh_from_db()
        reported.refresh_from_db()
        self.assertEqual(silent.status, Order.CANCELLED)
        self.assertEqual(reported.status, Order.PENDING)  # staff must check the bank statement
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 4)  # the cancelled order's unit came back

    def test_reference_rules_and_ownership(self):
        order = place_test_order(self.buyer, self.variant, method="upi")
        with self.assertRaises(OrderActionError):
            submit_payment_reference(order, self.buyer, "12")  # too short
        with self.assertRaises(OrderActionError):
            submit_payment_reference(order, self.buyer, "<script>x</script>")
        with self.assertRaises(OrderActionError):
            submit_payment_reference(order, make_user(email="other@example.com"), "UTR123456789")
        self.client.force_login(make_user(email="spy@example.com"))
        resp = self.client.post(reverse("orders:payment_reference", args=[order.order_number]),
                                {"reference": "UTR123456789"})
        self.assertEqual(resp.status_code, 404)

    def test_customer_cancel_after_reference_needs_staff(self):
        order = place_test_order(self.buyer, self.variant, method="upi")
        submit_payment_reference(order, self.buyer, "UTR123456789")
        self.client.force_login(self.buyer)
        self.client.post(reverse("orders:cancel", args=[order.order_number]), {"reason": "Changed my mind"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PENDING)  # money may have been sent: not cancelled automatically
        self.assertTrue(OrderRequest.objects.filter(order=order, kind=OrderRequest.KIND_CANCEL).exists())

    def test_checkout_lists_only_enabled_methods(self):
        configure_store(bank_transfer_enabled=False, cod_enabled=False)
        from .views import _payment_choices

        keys = [k for k, _ in _payment_choices(SiteSettings.load())]
        self.assertIn(Order.METHOD_UPI, keys)
        self.assertNotIn(Order.METHOD_BANK, keys)
        self.assertNotIn(Order.METHOD_COD, keys)


class StoreSettingsValidationTests(TestCase):
    def test_upi_needs_details_and_header_colour_must_be_dark(self):
        s = SiteSettings.load()
        s.upi_enabled = True
        with self.assertRaises(ValidationError) as cm:
            s.full_clean()
        self.assertIn("upi_id", cm.exception.message_dict)
        s.upi_enabled = False
        s.theme_header_color = "#ffee99"  # white text on pale yellow is unreadable
        with self.assertRaises(ValidationError) as cm:
            s.full_clean()
        self.assertIn("theme_header_color", cm.exception.message_dict)
        s.theme_header_color = "#102030"
        s.full_clean()
