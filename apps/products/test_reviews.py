"""Verified-buyer reviews, product details, badges and the category menu."""
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import configure_store, make_product, make_user, place_test_order
from apps.orders.models import Order

from .models import Review


class ReviewTests(TestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        configure_store()
        self.variant = make_product("Sandal Dhoop", price="225.00", mrp="249.00", stock=10)
        self.product = self.variant.product
        self.buyer = make_user(email="buyer@example.com", full_name="Asha Devi Sharma")
        self.order = place_test_order(self.buyer, self.variant)
        self.manager = make_user(email="mgr@example.com", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Store Manager"))

    def review_url(self):
        return reverse("products:review", args=[self.product.slug])

    def test_only_delivered_buyers_can_review(self):
        self.client.force_login(make_user(email="stranger@example.com"))
        self.client.post(self.review_url(), {"rating": 1, "title": "Fake"})
        self.assertFalse(Review.objects.exists())
        self.client.force_login(self.buyer)  # ordered but not delivered yet
        self.client.post(self.review_url(), {"rating": 5})
        self.assertFalse(Review.objects.exists())
        Order.objects.filter(pk=self.order.pk).update(status=Order.DELIVERED)
        self.assertContains(self.client.get(self.product.get_absolute_url()), "Write a review")
        self.client.post(self.review_url(), {"rating": 5, "title": "Lovely", "body": "Lasts long"})
        review = Review.objects.get()
        self.assertEqual((review.status, review.order_item.order, review.rating), (Review.PENDING, self.order, 5))
        self.client.post(self.review_url(), {"rating": 1})  # only one review per product
        self.assertEqual(Review.objects.count(), 1)

    def test_pending_review_hidden_until_published_then_stars_everywhere(self):
        Order.objects.filter(pk=self.order.pk).update(status=Order.DELIVERED)
        self.client.force_login(self.buyer)
        self.client.post(self.review_url(), {"rating": 4, "title": "Good smell"})
        review = Review.objects.get()
        page = self.client.get(self.product.get_absolute_url())
        self.assertNotContains(page, "Good smell")
        self.assertNotContains(page, "aggregateRating")
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("dashboard:reviews")), "Good smell")
        self.client.post(reverse("dashboard:review_action", args=[review.pk]), {"action": "approve"})
        self.product.refresh_from_db()
        self.assertEqual((self.product.rating_count, str(self.product.rating_avg)), (1, "4.00"))
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, "Good smell")
        self.assertContains(page, "Asha S.")  # first name + last initial only
        self.assertContains(page, "Verified buyer")
        self.assertContains(page, "aggregateRating")
        self.assertContains(self.client.get(reverse("products:shop")), "★★★★☆")
        self.client.post(reverse("dashboard:review_action", args=[review.pk]), {"action": "reject"})
        self.product.refresh_from_db()
        self.assertEqual(self.product.rating_count, 0)

    def test_staff_without_permission_cannot_moderate(self):
        staff = make_user(email="ops@example.com", is_staff=True)
        staff.groups.add(Group.objects.get(name="Order Staff"))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("dashboard:reviews")).status_code, 403)

    def test_product_details_badge_saving_and_menu_tagline(self):
        p = self.product
        p.badge = "bestseller"
        p.key_features = "Low smoke\nLong lasting"
        p.what_you_get = "24 sticks\n1 stand"
        p.manufacturer_name = "Life Line Dynamics"
        p.burn_time = "35 minutes"
        p.save()
        self.variant.net_quantity = "24 sticks (60 g)"
        self.variant.save()
        cat = p.category
        cat.tagline = "Slow, calming aroma"
        cat.save()
        page = self.client.get(p.get_absolute_url())
        for text in ("Bestseller", "Low smoke", "24 sticks", "Manufactured by", "35 minutes", "24 sticks (60 g)",
                     "You save ₹24", "Country of origin", "Slow, calming aroma"):
            self.assertContains(page, text)
        self.assertContains(self.client.get(reverse("products:shop")), "Save ₹24 (10%)")


class DashboardOfferPageTests(TestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.manager = make_user(email="mgr@example.com", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Store Manager"))
        self.gift = make_product("Chandan sticks", price="40.00", mrp="40.00", stock=5)

    def test_create_toggle_and_validate_offers(self):
        from apps.cart.models import Promotion

        self.client.force_login(self.manager)
        url = reverse("dashboard:offers")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"name": "Buy 2 Get 1 Free", "kind": "bxgy", "buy_qty": 2, "free_qty": 1,
                               "bundle_qty": 5, "min_amount": 0, "is_active": "on", "show_in_bar": "on",
                               "sort_order": 0})
        offer = Promotion.objects.get(name="Buy 2 Get 1 Free")
        self.assertContains(self.client.get(url), "Live")
        self.client.post(reverse("dashboard:offer_toggle", args=[offer.pk]))
        offer.refresh_from_db()
        self.assertFalse(offer.is_active)
        resp = self.client.post(url, {"name": "Gift", "kind": "gift", "buy_qty": 2, "free_qty": 1, "bundle_qty": 5,
                                      "min_amount": 0, "sort_order": 0})
        self.assertContains(resp, "Choose the free gift")
        self.client.post(url, {"name": "Free sticks above ₹900", "kind": "gift", "gift_variant": self.gift.pk,
                               "buy_qty": 2, "free_qty": 1, "bundle_qty": 5, "min_amount": 900, "is_active": "on",
                               "sort_order": 0})
        self.assertTrue(Promotion.objects.filter(kind="gift", gift_variant=self.gift).exists())
