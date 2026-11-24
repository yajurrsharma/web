"""One-page quick checkout: email code instead of registration, address inside checkout, PIN autofill."""
import json
import re
from io import BytesIO
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.testing import configure_store, make_product, make_user

from .models import Order


def code_from_outbox():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class QuickCheckoutTests(TestCase):
    def setUp(self):
        cache.clear()
        configure_store()
        self.variant = make_product("Sandal Dhoop", price="225.00", mrp="249.00", stock=10)
        self.client.post(reverse("cart:add"), {"variant_id": self.variant.pk, "quantity": 2})  # guest cart

    def send(self, email):
        return self.client.post(reverse("orders:checkout_start"), {"action": "send", "email": email})

    def verify(self, code):
        return self.client.post(reverse("orders:checkout_start"), {"action": "verify", "code": code})

    def test_new_customer_buys_without_registering(self):
        self.assertRedirects(self.client.get(reverse("orders:checkout")), reverse("orders:checkout_start"))
        self.send("New.Buyer@Example.com")
        self.assertContains(self.client.get(reverse("orders:checkout_start")), "new.buyer@example.com")
        resp = self.verify(code_from_outbox())
        self.assertRedirects(resp, reverse("orders:checkout"), fetch_redirect_response=False)
        user = User.objects.get(email="new.buyer@example.com")
        self.assertTrue(user.is_verified)
        self.assertFalse(user.has_usable_password())  # logs in later by email code or sets a password
        page = self.client.get(reverse("orders:checkout"))
        self.assertContains(page, "Where should we deliver?")  # address step inside checkout
        self.assertContains(page, "Sandal Dhoop")  # guest cart kept after login
        self.client.post(reverse("orders:checkout"), {
            "label": "Home", "full_name": "Ravi Kumar", "phone": "9876543210", "line1": "12 Temple Road",
            "city": "Mohali", "state_code": "03", "pincode": "160055"})
        user.refresh_from_db()
        self.assertEqual(user.full_name, "Ravi Kumar")
        page = self.client.get(reverse("orders:checkout"))
        token = page.context["form"].initial["checkout_token"]
        self.client.post(reverse("orders:checkout"), {"shipping_address": user.addresses.get().pk,
                                                      "billing_same": "on", "payment_method": "cod",
                                                      "checkout_token": token})
        order = Order.objects.get(user=user)
        self.assertEqual(order.items.get().quantity, 2)

    def test_existing_customer_logs_in_with_code_no_duplicate(self):
        existing = make_user(email="asha@example.com")
        self.send("asha@example.com")
        self.verify(code_from_outbox())
        self.assertEqual(int(self.client.session["_auth_user_id"]), existing.pk)
        self.assertEqual(User.objects.filter(email="asha@example.com").count(), 1)

    def test_wrong_code_and_same_message_for_any_email(self):
        resp = self.send("someone@example.com")
        self.assertEqual(resp.status_code, 302)
        resp = self.verify("000000")
        self.assertContains(resp, "incorrect or has expired")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(User.objects.filter(email="someone@example.com").exists())  # only created on success

    def test_closed_account_gets_no_code(self):
        make_user(email="closed@example.com", is_active=False)
        mail.outbox.clear()
        self.send("closed@example.com")
        self.assertEqual(mail.outbox, [])

    def test_empty_cart_goes_back_to_cart(self):
        from apps.cart.models import CartItem

        CartItem.objects.all().delete()
        self.assertRedirects(self.client.get(reverse("orders:checkout_start")), reverse("cart:detail"))

    def test_pincode_lookup_fills_city_and_state(self):
        reply = json.dumps([{"Status": "Success", "PostOffice": [
            {"Name": "Zirakpur", "District": "Mohali", "State": "Punjab"}]}]).encode()
        with mock.patch("apps.orders.pincode.urllib.request.urlopen", return_value=BytesIO(reply)) as fake:
            data = self.client.get(reverse("orders:pincode", args=["140603"])).json()
            self.client.get(reverse("orders:pincode", args=["140603"]))  # second time comes from cache
        self.assertEqual(data, {"found": True, "city": "Mohali", "state_code": "03", "state": "Punjab"})
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(self.client.get(reverse("orders:pincode", args=["12"])).json(), {"found": False})

    def test_pincode_service_down_does_not_break(self):
        with mock.patch("apps.orders.pincode.urllib.request.urlopen", side_effect=TimeoutError):
            self.assertEqual(self.client.get(reverse("orders:pincode", args=["560001"])).json(), {"found": False})
