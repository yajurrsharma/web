from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteSettings
from apps.core.testing import configure_store, make_product, make_user

from .models import Cart, Coupon, CouponError
from .pricing import build_quote

D = Decimal


class PricingEngineTests(TestCase):
    def setUp(self):
        self.store = configure_store()
        self.v5 = make_product("Five", price="105.00", mrp="120.00", rate="5.00")
        self.v18 = make_product("Eighteen", price="118.00", mrp="118.00", rate="18.00")

    def test_inclusive_intra_state_split(self):
        q = build_quote([(self.v5, 1)], store=self.store, ship_state_code="03")
        line = q.lines[0]
        self.assertEqual((line.taxable, line.tax, line.cgst, line.sgst, line.igst),
                         (D("100.00"), D("5.00"), D("2.50"), D("2.50"), D("0.00")))
        # ₹50 delivery, taxed at the item rate (5%), inclusive.
        self.assertEqual((q.shipping_taxable, q.shipping_tax), (D("47.62"), D("2.38")))
        self.assertEqual(q.grand_total, D("155.00"))
        self.assertTrue(q.is_intra_state)

    def test_inter_state_uses_igst(self):
        q = build_quote([(self.v5, 1)], store=self.store, ship_state_code="07")
        self.assertEqual(q.lines[0].igst, D("5.00"))
        self.assertEqual(q.cgst_total + q.sgst_total, D("0.00"))
        self.assertFalse(q.is_intra_state)

    def test_unknown_state_leaves_split_undetermined(self):
        q = build_quote([(self.v5, 1)], store=self.store, ship_state_code=None)
        self.assertIsNone(q.is_intra_state)
        self.assertEqual(q.tax_total, D("5.00") + D("2.38"))

    def test_mixed_rates_and_shipping_at_highest_rate(self):
        q = build_quote([(self.v5, 1), (self.v18, 1)], store=self.store, ship_state_code="03")
        self.assertEqual(q.shipping_gst_rate, D("18.00"))
        self.assertEqual(q.lines[1].taxable, D("100.00"))
        self.assertEqual(q.lines[1].tax, D("18.00"))
        self.assertEqual(q.grand_total, D("105.00") + D("118.00") + D("50.00"))

    def test_free_shipping_threshold(self):
        q = build_quote([(self.v5, 5)], store=self.store, ship_state_code="03")  # ₹525
        self.assertEqual(q.shipping_gross, D("0.00"))
        q = build_quote([(self.v5, 4)], store=self.store, ship_state_code="03")  # ₹420
        self.assertEqual(q.free_shipping_gap, D("80.00"))

    def test_exclusive_pricing_adds_gst(self):
        store = configure_store(prices_include_gst=False, shipping_flat_charge=D("0"))
        v = make_product("Excl", price="100.00", mrp="100.00", rate="18.00")
        q = build_quote([(v, 1)], store=store, ship_state_code="03")
        self.assertEqual((q.lines[0].taxable, q.lines[0].tax, q.grand_total), (D("100.00"), D("18.00"), D("118.00")))

    def test_coupon_discount_reduces_taxable_value(self):
        coupon = Coupon.objects.create(code="TEN", discount_type=Coupon.PERCENT, value=D("10"))
        q = build_quote([(self.v5, 2)], store=self.store, coupon=coupon, ship_state_code="03")
        self.assertEqual(q.discount, D("21.00"))
        self.assertEqual((q.lines[0].taxable, q.lines[0].tax), (D("180.00"), D("9.00")))
        self.assertEqual(q.grand_total, D("189.00") + D("50.00"))

    def test_discount_allocation_sums_exactly(self):
        coupon = Coupon.objects.create(code="FLAT", discount_type=Coupon.FLAT, value=D("10.00"))
        q = build_quote([(self.v5, 1), (self.v18, 1), (self.v5, 1)], store=self.store, coupon=coupon,
                        ship_state_code="03")
        self.assertEqual(sum(line.discount for line in q.lines), D("10.00"))

    def test_cod_fee_and_round_off(self):
        store = configure_store(cod_fee=D("29.00"), round_off_invoice_total=True)
        v = make_product("Odd", price="99.99", mrp="99.99", rate="5.00")
        q = build_quote([(v, 1)], store=store, ship_state_code="03", payment_method="cod")
        self.assertEqual(q.cod_fee, D("29.00"))
        self.assertEqual(q.grand_total, D("179.00"))  # 99.99 + 50 + 29 = 178.99 → 179
        self.assertEqual(q.round_off, D("0.01"))

    def test_totals_always_reconcile(self):
        for qty in range(1, 8):
            q = build_quote([(self.v5, qty), (self.v18, 1)], store=self.store, ship_state_code="03")
            self.assertEqual(q.taxable_total + q.tax_total + q.round_off, q.grand_total)
            self.assertEqual(q.cgst_total + q.sgst_total + q.igst_total, q.tax_total)


class CouponRulesTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_rules(self):
        c = Coupon.objects.create(code="diwali", discount_type=Coupon.PERCENT, value=D("20"),
                                  min_order_amount=D("300"), max_discount=D("50"))
        self.assertEqual(c.code, "DIWALI")
        with self.assertRaises(CouponError):
            c.check_eligible(self.user, D("299"))
        c.check_eligible(self.user, D("300"))
        self.assertEqual(c.discount_for(D("1000")), D("50.00"))  # capped

    def test_expired_and_inactive(self):
        c = Coupon.objects.create(code="OLD", discount_type=Coupon.FLAT, value=D("10"),
                                  ends_at=timezone.now() - timedelta(days=1))
        with self.assertRaises(CouponError):
            c.check_eligible(self.user, D("100"))
        c2 = Coupon.objects.create(code="OFF", discount_type=Coupon.FLAT, value=D("10"), is_active=False)
        with self.assertRaises(CouponError):
            c2.check_eligible(self.user, D("100"))

    def test_flat_never_exceeds_subtotal(self):
        c = Coupon.objects.create(code="BIG", discount_type=Coupon.FLAT, value=D("500"))
        self.assertEqual(c.discount_for(D("100")), D("100.00"))


class CartViewTests(TestCase):
    def setUp(self):
        configure_store()
        self.variant = make_product(price="105.00", stock=3)

    def add(self, qty=1, variant=None):
        return self.client.post(reverse("cart:add"), {"variant_id": (variant or self.variant).pk, "quantity": qty})

    def test_guest_can_add_and_quantity_capped_by_stock(self):
        self.add(2)
        self.add(5)
        cart = Cart.objects.get(session_key=self.client.session.session_key)
        self.assertEqual(cart.items.get().quantity, 3)

    def test_out_of_stock_not_added(self):
        v = make_product("Gone", sku="GONE", stock=0)
        self.add(1, v)
        self.assertFalse(Cart.objects.filter(items__variant=v).exists())

    def test_cart_page_shows_server_prices(self):
        self.add(2)
        resp = self.client.get(reverse("cart:detail"))
        self.assertContains(resp, "₹210.00")

    def test_guest_cart_merges_on_login(self):
        user = make_user(password="Pw-merge-123")
        self.add(2)
        self.client.post(reverse("accounts:login"), {"username": user.email, "password": "Pw-merge-123"})
        self.assertEqual(Cart.objects.get(user=user).items.get().quantity, 2)
        self.assertFalse(Cart.objects.filter(user__isnull=True).exists())

    def test_cannot_touch_another_cart_item(self):
        user = make_user()
        other_cart = Cart.objects.create(user=user)
        item = other_cart.items.create(variant=self.variant, quantity=1)
        self.add(1)
        resp = self.client.post(reverse("cart:remove", args=[item.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(other_cart.items.filter(pk=item.pk).exists())

    def test_apply_coupon(self):
        Coupon.objects.create(code="TEN", discount_type=Coupon.PERCENT, value=D("10"))
        self.add(1)
        self.client.post(reverse("cart:apply_coupon"), {"code": "ten"})
        resp = self.client.get(reverse("cart:detail"))
        self.assertContains(resp, "Coupon TEN")
        self.client.post(reverse("cart:apply_coupon"), {"code": "NOPE"})


class StoreSettingsTests(TestCase):
    def test_singleton(self):
        a = SiteSettings.load()
        b = SiteSettings.load()
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(SiteSettings.objects.count(), 1)
