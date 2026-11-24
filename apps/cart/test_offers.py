"""Automatic offers, free gifts and the prepaid benefit in the pricing engine."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.testing import configure_store, make_product, make_user, place_test_order

from .models import Coupon, Promotion
from .pricing import build_quote

D = Decimal


class OfferPricingTests(TestCase):
    def setUp(self):
        self.store = configure_store(free_shipping_threshold=D("10000"), shipping_flat_charge=D("50.00"))
        self.a = make_product("Agarbatti A", price="100.00", mrp="120.00", stock=50)
        self.b = make_product("Agarbatti B", price="80.00", mrp="100.00", stock=50)

    def quote(self, items, **kw):
        return build_quote(items, store=self.store, ship_state_code="03", **kw)

    def test_buy_2_get_1_cheapest_free_and_gst_on_discounted_price(self):
        Promotion.objects.create(name="Buy 2 Get 1 Free", kind=Promotion.BUY_X_GET_Y, buy_qty=2, free_qty=1)
        q = self.quote([(self.a, 2), (self.b, 1)])
        self.assertEqual(q.offer_discount, D("80.00"))  # the ₹80 item is free
        self.assertEqual(q.offers_applied, [("Buy 2 Get 1 Free", D("80.00"))])
        line_b = q.lines[1]
        self.assertEqual((line_b.discount, line_b.taxable, line_b.tax), (D("80.00"), D("0.00"), D("0.00")))
        self.assertEqual(q.grand_total, D("200.00") + D("50.00"))  # items 280 − 80 + delivery 50

    def test_hint_when_one_more_item_needed(self):
        Promotion.objects.create(name="Buy 2 Get 1 Free", kind=Promotion.BUY_X_GET_Y, buy_qty=2, free_qty=1)
        q = self.quote([(self.a, 2)])
        self.assertEqual(q.offer_discount, D("0"))
        self.assertIn("Add 1 more eligible item", q.offer_hints[0])

    def test_bundle_price_only_on_chosen_products(self):
        promo = Promotion.objects.create(name="Any 3 for ₹250", kind=Promotion.BUNDLE, bundle_qty=3,
                                         bundle_price=D("250"))
        promo.products.add(self.a.product)
        q = self.quote([(self.a, 3), (self.b, 3)])
        self.assertEqual(q.offer_discount, D("50.00"))  # 3 × 100 → 250; B not eligible
        self.assertEqual(q.lines[1].discount, D("0"))

    def test_each_unit_used_by_one_offer_only(self):
        Promotion.objects.create(name="Buy 1 Get 1", kind=Promotion.BUY_X_GET_Y, buy_qty=1, free_qty=1,
                                 sort_order=1)
        Promotion.objects.create(name="Any 2 for ₹1", kind=Promotion.BUNDLE, bundle_qty=2, bundle_price=D("1"),
                                 sort_order=2)
        q = self.quote([(self.a, 2)])
        self.assertEqual(q.offers_applied, [("Buy 1 Get 1", D("100.00"))])

    def test_free_gift_above_amount_is_zero_line_and_leaves_stock(self):
        gift = make_product("Chandan sticks", price="40.00", mrp="40.00", stock=5)
        Promotion.objects.create(name="Free chandan sticks above ₹300", kind=Promotion.FREE_GIFT,
                                 min_amount=D("300"), gift_variant=gift)
        q = self.quote([(self.a, 2)])
        self.assertFalse(q.gift_lines)
        self.assertIn("Add ₹100.00 more", q.offer_hints[0])
        q = self.quote([(self.a, 3)])
        self.assertEqual(len(q.gift_lines), 1)
        self.assertEqual((q.gift_lines[0].gross, q.gift_lines[0].tax), (D("0"), D("0")))
        self.assertEqual(q.item_count, 3)  # gift not counted as a bought item
        order = place_test_order(make_user(), self.a, qty=3)
        gift_item = order.items.get(variant=gift)
        self.assertTrue(gift_item.product_name.startswith("FREE GIFT:"))
        self.assertEqual(gift_item.line_total, D("0"))
        gift.refresh_from_db()
        self.assertEqual(gift.stock, 4)

    def test_coupon_applies_after_offers(self):
        Promotion.objects.create(name="Buy 2 Get 1 Free", kind=Promotion.BUY_X_GET_Y, buy_qty=2, free_qty=1)
        coupon = Coupon.objects.create(code="TEN", discount_type=Coupon.PERCENT, value=D("10"))
        q = self.quote([(self.a, 3)], coupon=coupon)
        self.assertEqual(q.offer_discount, D("100.00"))
        self.assertEqual(q.coupon_discount, D("20.00"))  # 10% of the 200 left
        self.assertEqual(q.discount, D("120.00"))

    def test_prepaid_discount_and_free_delivery(self):
        configure_store(prepaid_discount_percent=D("5"), prepaid_free_shipping=True,
                        free_shipping_threshold=D("10000"), shipping_flat_charge=D("50.00"))
        from apps.core.models import SiteSettings

        store = SiteSettings.load()
        cod = build_quote([(self.a, 2)], store=store, ship_state_code="03", payment_method="cod")
        upi = build_quote([(self.a, 2)], store=store, ship_state_code="03", payment_method="upi")
        self.assertEqual(cod.prepaid_discount, D("0"))
        self.assertEqual(upi.prepaid_discount, D("10.00"))
        self.assertEqual((cod.shipping_gross, upi.shipping_gross), (D("50.00"), D("0")))
        self.assertEqual(upi.grand_total, D("190.00"))

    def test_ended_or_switched_off_offer_not_applied(self):
        from datetime import timedelta

        from django.utils import timezone

        Promotion.objects.create(name="Old", kind=Promotion.BUY_X_GET_Y, buy_qty=1, free_qty=1,
                                 ends_at=timezone.now() - timedelta(minutes=1))
        Promotion.objects.create(name="Off", kind=Promotion.BUY_X_GET_Y, buy_qty=1, free_qty=1, is_active=False)
        self.assertEqual(self.quote([(self.a, 2)]).offer_discount, D("0"))

    def test_offer_bar_and_cart_show_offers(self):
        configure_store(announcement_text="Free delivery above ₹499 | Use code DIWALI10")
        Promotion.objects.create(name="Buy 2 Get 1 Free", kind=Promotion.BUY_X_GET_Y, buy_qty=2, free_qty=1)
        page = self.client.get(reverse("core:home")).content.decode()
        for text in ("Free delivery above ₹499", "Use code DIWALI10", "Buy 2 Get 1 Free"):
            self.assertIn(text, page)
        self.client.post(reverse("cart:add"), {"variant_id": self.a.pk, "quantity": 3})
        cart = self.client.get(reverse("cart:detail"))
        self.assertContains(cart, "🎉 Buy 2 Get 1 Free")
        self.assertContains(cart, "You save")
