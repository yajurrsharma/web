import threading
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cart.models import Cart, CartItem, Coupon, CouponUsage
from apps.core.testing import configure_store, make_address, make_product, make_user, place_test_order
from apps.inventory.models import StockMovement
from apps.payments.models import Payment

from .models import Order, OrderRequest
from .services import (CheckoutError, OrderActionError, cancel_order, change_status, complete_return,
                       customer_cancel, customer_request_return, expire_unpaid_orders, mark_cod_collected,
                       resolve_request)

D = Decimal


class CheckoutViewTests(TestCase):
    def setUp(self):
        configure_store()
        self.user = make_user()
        self.address = make_address(self.user)
        self.variant = make_product(price="105.00", stock=5)
        self.client.force_login(self.user)
        self.client.post(reverse("cart:add"), {"variant_id": self.variant.pk, "quantity": 2})

    def checkout_post(self, token=None, **extra):
        resp = self.client.get(reverse("orders:checkout"))
        token = token or resp.context["form"].initial["checkout_token"]
        data = {"shipping_address": self.address.pk, "billing_same": "on", "payment_method": "cod",
                "checkout_token": token, **extra}
        return self.client.post(reverse("orders:checkout"), data), token

    def test_cod_checkout_creates_order_deducts_stock_and_emails(self):
        with self.captureOnCommitCallbacks(execute=True):
            resp, _ = self.checkout_post()
        order = Order.objects.get()
        self.assertRedirects(resp, reverse("orders:confirmation", args=[order.order_number]))
        self.assertEqual(order.status, Order.CONFIRMED)
        self.assertEqual(order.payment_status, Order.PAY_PENDING)
        self.assertEqual(order.grand_total, D("210.00") + D("50.00"))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)
        self.assertEqual(StockMovement.objects.filter(reason=StockMovement.SALE).get().change, -2)
        self.assertFalse(CartItem.objects.exists())
        self.assertIn(order.order_number, mail.outbox[-1].subject)
        self.assertEqual(order.payments.get().gateway, Payment.GATEWAY_COD)

    def test_duplicate_submission_creates_one_order(self):
        resp1, token = self.checkout_post()
        resp2 = self.client.post(reverse("orders:checkout"), {
            "shipping_address": self.address.pk, "billing_same": "on", "payment_method": "cod",
            "checkout_token": token})
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(resp2.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)

    def test_browser_supplied_prices_are_ignored(self):
        self.checkout_post(grand_total="1.00", price="1.00", subtotal="1.00")
        self.assertEqual(Order.objects.get().grand_total, D("260.00"))

    def test_unverified_user_cannot_checkout(self):
        self.user.is_verified = False
        self.user.save()
        resp = self.client.get(reverse("orders:checkout"))
        self.assertRedirects(resp, reverse("accounts:resend_verification"))

    def test_other_customers_address_rejected(self):
        other = make_user(email="other@example.com")
        foreign = make_address(other)
        resp = self.client.get(reverse("orders:checkout"))
        token = resp.context["form"].initial["checkout_token"]
        resp = self.client.post(reverse("orders:checkout"), {"shipping_address": foreign.pk, "billing_same": "on",
                                                             "payment_method": "cod", "checkout_token": token})
        self.assertEqual(resp.status_code, 200)  # form error, no order
        self.assertFalse(Order.objects.exists())

    def test_online_option_hidden_without_keys(self):
        with override_settings(PAYMENTS_ENABLED=False):
            resp = self.client.get(reverse("orders:checkout"))
        self.assertNotIn("online", [c[0] for c in resp.context["payment_choices"]])


class OrderServiceTests(TestCase):
    def setUp(self):
        self.store = configure_store()
        self.user = make_user()
        self.variant = make_product(price="105.00", stock=3)

    def test_oversell_rejected(self):
        place_test_order(self.user, self.variant, qty=3)
        other = make_user(email="late@example.com")
        with self.assertRaises(CheckoutError):
            place_test_order(other, self.variant, qty=1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)

    def test_stock_changed_after_adding_to_cart(self):
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=2)
        self.variant.stock = 1
        self.variant.save()
        with self.assertRaises(CheckoutError):
            place_test_order(self.user, self.variant, qty=2)

    def test_cod_unavailable_above_limit(self):
        configure_store(cod_max_order=D("100.00"))
        with self.assertRaises(CheckoutError):
            place_test_order(self.user, self.variant, qty=1, method="cod")

    def test_unserviceable_pincode(self):
        configure_store(serviceable_pincodes="1600")
        with self.assertRaises(CheckoutError):
            place_test_order(self.user, self.variant)

    def test_cancel_restores_stock_once(self):
        order = place_test_order(self.user, self.variant, qty=2)
        cancel_order(order, reason="test")
        cancel_order(order, reason="again")  # idempotent
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)
        self.assertEqual(StockMovement.objects.filter(reason=StockMovement.CANCELLATION).count(), 1)

    def test_cancel_releases_coupon_usage(self):
        coupon = Coupon.objects.create(code="ONCE", discount_type=Coupon.FLAT, value=D("5"), per_user_limit=1)
        order = place_test_order(self.user, self.variant, coupon=coupon)
        self.assertEqual(CouponUsage.objects.count(), 1)
        cancel_order(order, reason="x")
        self.assertEqual(CouponUsage.objects.count(), 0)

    def test_status_transitions_enforced(self):
        order = place_test_order(self.user, self.variant)
        with self.assertRaises(OrderActionError):
            change_status(order, Order.DELIVERED)
        change_status(order, Order.PROCESSING)
        change_status(order, Order.SHIPPED)
        change_status(order, Order.DELIVERED)
        with self.assertRaises(OrderActionError):
            cancel_order(order, reason="too late")

    def test_unpaid_online_order_cannot_ship(self):
        order = place_test_order(self.user, self.variant, method="online")
        self.assertEqual(order.status, Order.PENDING)
        with self.assertRaises(OrderActionError):
            change_status(order, Order.CONFIRMED)

    def test_cod_collection_marks_paid(self):
        order = place_test_order(self.user, self.variant)
        staff = make_user(email="staff@example.com", is_staff=True)
        with self.assertRaises(OrderActionError):
            mark_cod_collected(order, by=staff)  # not shipped yet
        change_status(order, Order.SHIPPED)
        mark_cod_collected(order, by=staff)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PAY_PAID)
        self.assertEqual(order.payment_method, Order.METHOD_COD)  # never relabelled as prepaid
        self.assertEqual(order.payments.get().status, Payment.CAPTURED)

    def test_customer_cancel_unpaid_is_immediate(self):
        order = place_test_order(self.user, self.variant)
        customer_cancel(order, self.user, "changed my mind")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.CANCELLED)

    def test_customer_cancel_paid_creates_request(self):
        order = place_test_order(self.user, self.variant)
        Order.objects.filter(pk=order.pk).update(payment_status=Order.PAY_PAID)
        order.refresh_from_db()
        req = customer_cancel(order, self.user, "ordered twice")
        self.assertIsInstance(req, OrderRequest)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.CONFIRMED)

    def test_return_flow_without_restock(self):
        order = place_test_order(self.user, self.variant, qty=1)
        for s in (Order.SHIPPED, Order.DELIVERED):
            change_status(order, s)
        order.refresh_from_db()
        req = customer_request_return(order, self.user, "damaged")
        staff = make_user(email="staff@example.com", is_staff=True)
        resolve_request(req, approve=True, by=staff)
        complete_return(req, by=staff, restock=False, refund_amount=D("0"))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 2)  # damaged goods not restocked
        order.refresh_from_db()
        self.assertEqual(order.status, Order.RETURNED)

    def test_return_window(self):
        order = place_test_order(self.user, self.variant, qty=1)
        for s in (Order.SHIPPED, Order.DELIVERED):
            change_status(order, s)
        Order.objects.filter(pk=order.pk).update(delivered_at=timezone.now() - timedelta(days=30))
        order.refresh_from_db()
        with self.assertRaises(OrderActionError):
            customer_request_return(order, self.user, "late")

    @override_settings(PAYMENTS_ENABLED=True)
    def test_unpaid_online_orders_expire(self):
        order = place_test_order(self.user, self.variant, qty=2, method="online")
        Order.objects.filter(pk=order.pk).update(payment_due_by=timezone.now() - timedelta(minutes=1))
        with mock.patch("apps.payments.services.reconcile_order", return_value=False):
            self.assertEqual(expire_unpaid_orders(), 1)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.CANCELLED)
        self.assertEqual(self.variant.stock, 3)

    @override_settings(PAYMENTS_ENABLED=True)
    def test_expiry_skipped_when_gateway_unreachable(self):
        order = place_test_order(self.user, self.variant, method="online")
        Order.objects.filter(pk=order.pk).update(payment_due_by=timezone.now() - timedelta(minutes=1))
        with mock.patch("apps.payments.services.reconcile_order", side_effect=ConnectionError):
            self.assertEqual(expire_unpaid_orders(), 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PENDING)


class OrderAccessTests(TestCase):
    def test_customers_only_see_their_own_orders(self):
        configure_store()
        owner = make_user(email="owner@example.com")
        intruder = make_user(email="intruder@example.com")
        order = place_test_order(owner, make_product(stock=2))
        self.client.force_login(intruder)
        for name in ("orders:detail", "orders:confirmation"):
            self.assertEqual(self.client.get(reverse(name, args=[order.order_number])).status_code, 404)
        self.assertEqual(self.client.post(reverse("orders:cancel", args=[order.order_number]),
                                          {"reason": "x"}).status_code, 404)
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("orders:detail", args=[order.order_number])).status_code, 200)

    def test_public_tracking_needs_matching_email(self):
        configure_store()
        owner = make_user(email="owner@example.com")
        order = place_test_order(owner, make_product(stock=2))
        resp = self.client.post(reverse("orders:track"), {"order_number": order.order_number, "email": "x@example.com"})
        self.assertIsNone(resp.context["order"])
        resp = self.client.post(reverse("orders:track"), {"order_number": order.order_number, "email": "OWNER@example.com"})
        self.assertEqual(resp.context["order"], order)

    def test_order_numbers_are_not_sequential(self):
        configure_store()
        user = make_user()
        v = make_product(stock=5)
        a = place_test_order(user, v, token="a")
        b = place_test_order(user, v, token="b")
        self.assertRegex(a.order_number, r"^LLD-\d{6}-[A-Z2-9]{5}$")
        self.assertNotEqual(a.order_number, b.order_number)
        self.assertNotIn(str(a.pk), a.order_number[-5:])


class ConcurrentCheckoutTests(TransactionTestCase):
    """Many buyers race for the last units. Whatever the interleaving, stock never goes negative
    and no more units are sold than existed."""

    def test_no_oversell_under_concurrency(self):
        configure_store()
        variant = make_product(stock=3)
        users = [make_user(email=f"u{i}@example.com") for i in range(6)]
        results = []

        def buy(user):
            try:
                place_test_order(user, variant, qty=1, token=f"race-{user.pk}")
                results.append("ok")
            except CheckoutError:
                results.append("rejected")
            except Exception as exc:  # SQLite may report "database is locked"; that's a rejection too
                results.append(type(exc).__name__)
            finally:
                connection.close()

        threads = [threading.Thread(target=buy, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        variant.refresh_from_db()
        sold = Order.objects.count()
        self.assertGreaterEqual(variant.stock, 0)
        self.assertLessEqual(sold, 3)
        self.assertEqual(variant.stock, 3 - sold)
