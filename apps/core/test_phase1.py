"""Speed & mobile: small photo copies, delivery check, free-delivery bar, phone menu, sticky buy bar."""
import os
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.core.testing import configure_store, make_product
from apps.orders.pincode import add_working_days, delivery_check

MEDIA = tempfile.mkdtemp(prefix="lld-p1-")


def photo(size=(1600, 1600)):
    buf = BytesIO()
    Image.new("RGB", size, "orange").save(buf, "JPEG")
    return SimpleUploadedFile("p.jpg", buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=MEDIA)
class PhaseOneTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.store = configure_store(free_shipping_threshold=Decimal("500"), state_code="03")
        self.variant = make_product("Rose Agarbatti", price="200.00", mrp="250.00", stock=5)
        self.product = self.variant.product
        self.product.main_image = photo()
        self.product.save()

    def test_cards_use_small_copies_made_once(self):
        page = self.client.get(reverse("products:shop")).content.decode()
        self.assertIn("/media/thumbs/480/", page)
        self.assertIn("/media/thumbs/240/", page)
        base = os.path.splitext(self.product.main_image.name)[0]
        small = os.path.join(MEDIA, "thumbs", "480", base + ".jpg")
        with Image.open(small) as img:
            self.assertEqual(max(img.size), 480)
        made = os.path.getmtime(small)
        self.client.get(reverse("products:shop"))
        self.assertEqual(os.path.getmtime(small), made)  # reused, not rebuilt

    def test_missing_file_falls_back_without_error(self):
        from apps.core.templatetags.store import thumb

        os.remove(self.product.main_image.path)
        self.assertEqual(thumb(self.product.main_image, 480), self.product.main_image.url)

    def test_working_days_skip_sunday(self):
        self.assertEqual(add_working_days(date(2026, 10, 24), 1), date(2026, 10, 26))  # Sat → Mon

    def test_delivery_check_local_national_and_unserved(self):
        punjab = {"city": "Mohali", "state_code": "03", "state": "Punjab"}
        kerala = {"city": "Kochi", "state_code": "32", "state": "Kerala"}
        with mock.patch("apps.orders.pincode.lookup", return_value=punjab):
            local = delivery_check(self.store, "160055", today=date(2026, 10, 26))  # Monday
        with mock.patch("apps.orders.pincode.lookup", return_value=kerala):
            far = delivery_check(self.store, "682001", today=date(2026, 10, 26))
        self.assertEqual(local["delivery"], "Wed, 28 Oct – Fri, 30 Oct")  # 2–4 working days
        self.assertEqual(far["delivery"], "Fri, 30 Oct – Tue, 03 Nov")  # 4–7, Sunday skipped
        self.assertEqual((local["place"], local["cod"]), ("Mohali, Punjab", True))
        configure_store(serviceable_pincodes="1600 1400")
        from apps.core.models import SiteSettings

        out = delivery_check(SiteSettings.load(), "682001")
        self.assertEqual((out["ok"], out["message"]), (False, "Sorry, we don't deliver to 682001 yet."))
        self.assertFalse(delivery_check(self.store, "12ab")["ok"])

    def test_delivery_check_endpoint(self):
        with mock.patch("apps.orders.pincode.lookup", return_value=None):
            data = self.client.get(reverse("orders:delivery_check", args=["560001"])).json()
        self.assertTrue(data["ok"])
        self.assertIn("delivery", data)

    def test_free_delivery_bar_in_cart(self):
        self.client.post(reverse("cart:add"), {"variant_id": self.variant.pk, "quantity": 1})
        page = self.client.get(reverse("cart:detail"))
        self.assertContains(page, "more for <strong>FREE delivery</strong>")
        self.assertEqual(page.context["ship_pct"], 40)  # ₹200 of ₹500
        self.client.post(reverse("cart:add"), {"variant_id": self.variant.pk, "quantity": 2})
        self.assertContains(self.client.get(reverse("cart:detail")), "unlocked FREE delivery")

    def test_phone_menu_and_sticky_buy_bar(self):
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, 'class="tabbar')
        self.assertContains(page, "data-sticky-buy")
        self.assertContains(page, "Check delivery")


class HomeLookTests(TestCase):
    def test_trust_points_and_shloka_band_follow_settings(self):
        page = self.client.get(reverse("core:home"))
        self.assertContains(page, "Cash on Delivery")  # default trust points
        self.assertNotContains(page, "shloka-band")  # hidden until text is entered
        configure_store(trust_items="🚚 | Dispatch in 24 hours | On working days\nbad line",
                        shloka_text="Shubham bhavatu", shloka_meaning="May all be well")
        page = self.client.get(reverse("core:home"))
        self.assertContains(page, "Dispatch in 24 hours")
        self.assertNotContains(page, "bad line")
        self.assertContains(page, "shloka-band")
        self.assertContains(page, "May all be well")
