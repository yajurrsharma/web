"""Tests for the staff product / stock / category / HSN pages."""
import shutil
import tempfile
from io import BytesIO, StringIO

from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.core.testing import make_product, make_user, mp4_bytes
from apps.inventory.models import StockMovement
from apps.products.models import Category, Product, ProductMedia, ProductVariant, TaxClass

TEMP_MEDIA = tempfile.mkdtemp(prefix="lld-test-media-")


def png(name="photo.png"):
    buf = BytesIO()
    Image.new("RGB", (20, 20), "orange").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def staff_in(group, email):
    user = make_user(email=email, is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class CatalogPageTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.variant = make_product(name="Existing Dhoop", stock=4)
        self.cat = self.variant.product.category
        self.tax = self.variant.product.tax_class
        self.manager = staff_in("Store Manager", "mgr@example.com")

    def _new_product_data(self, **extra):
        data = {"p-name": "Devleela Rose Agarbatti", "p-category": self.cat.pk, "p-tax_class": self.tax.pk,
                "p-unit": "PCS", "p-status": "published", "v-name": "Pack of 12", "v-sku": "",
                "v-mrp": "150.00", "v-price": "129.00", "v-cost_price": "70.00", "v-opening_stock": "25",
                "v-low_stock_threshold": "5", "v-weight_grams": "200", "v-is_active": "on", "v-sort_order": "0"}
        data.update(extra)
        return data

    def test_pages_load_for_manager(self):
        self.client.force_login(self.manager)
        for url in [reverse("dashboard:products"), reverse("dashboard:product_create"),
                    reverse("dashboard:product_edit", args=[self.variant.product_id]),
                    reverse("dashboard:variant_create", args=[self.variant.product_id]),
                    reverse("dashboard:variant_edit", args=[self.variant.pk]), reverse("dashboard:stock_in"),
                    reverse("dashboard:categories"), reverse("dashboard:category_edit", args=[self.cat.pk]),
                    reverse("dashboard:tax_classes"), reverse("dashboard:home"), reverse("dashboard:inventory")]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_add_product_creates_variant_opening_stock_and_photos(self):
        self.client.force_login(self.manager)
        data = self._new_product_data()
        data["p-main_image"] = png("main.png")
        data["g-files"] = [png("a.png"), png("b.png")]
        resp = self.client.post(reverse("dashboard:product_create"), data)
        product = Product.objects.get(name="Devleela Rose Agarbatti")
        self.assertRedirects(resp, reverse("dashboard:product_edit", args=[product.pk]))
        variant = product.variants.get()
        self.assertTrue(variant.sku.startswith("LLD-"))
        self.assertEqual((variant.stock, variant.cost_price, variant.name), (25, 70, "Pack of 12"))
        m = StockMovement.objects.get(variant=variant)
        self.assertEqual((m.reason, m.change, m.created_by), (StockMovement.OPENING, 25, self.manager))
        self.assertTrue(product.main_image)
        self.assertEqual(product.media.count(), 2)
        self.assertTrue(product.is_published)

    def test_price_above_mrp_rejected_and_nothing_saved(self):
        self.client.force_login(self.manager)
        resp = self.client.post(reverse("dashboard:product_create"), self._new_product_data(**{"v-price": "200"}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Selling price cannot be more than MRP")
        self.assertFalse(Product.objects.filter(name="Devleela Rose Agarbatti").exists())

    def test_fake_image_rejected(self):
        self.client.force_login(self.manager)
        data = self._new_product_data()
        data["p-main_image"] = SimpleUploadedFile("evil.png", b"<?php echo 1; ?>", content_type="image/png")
        resp = self.client.post(reverse("dashboard:product_create"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Devleela Rose Agarbatti").exists())

    def test_duplicate_sku_rejected(self):
        self.client.force_login(self.manager)
        resp = self.client.post(reverse("dashboard:product_create"),
                                self._new_product_data(**{"v-sku": self.variant.sku.lower()}))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Devleela Rose Agarbatti").exists())

    def test_edit_variant_cannot_change_stock(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("dashboard:variant_edit", args=[self.variant.pk]), {
            "name": "Standard", "sku": self.variant.sku, "mrp": "120.00", "price": "99.00", "stock": "999",
            "low_stock_threshold": "3", "weight_grams": "100", "is_active": "on", "sort_order": "0"})
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.price, self.variant.stock), (99, 4))

    def test_stock_in_and_damage(self):
        self.client.force_login(staff_in("Inventory Staff", "inv@example.com"))
        url = reverse("dashboard:stock_in")
        self.assertRedirects(self.client.post(url, {"variant": self.variant.pk, "change": "10",
                                                    "reason": "purchase", "note": "Bill 7"}), url)
        self.client.post(url, {"variant": self.variant.pk, "change": "3", "reason": "damage"})
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 11)
        resp = self.client.post(url, {"variant": self.variant.pk, "change": "-50", "reason": "adjustment"})
        self.assertEqual(resp.status_code, 200)  # not enough stock: shown as an error, nothing saved
        resp = self.client.post(url, {"variant": self.variant.pk, "change": "-5", "reason": "purchase"})
        self.assertContains(resp, "must be a positive number")
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 11)

    def test_cost_price_hidden_without_permission(self):
        self.client.force_login(staff_in("Inventory Staff", "inv@example.com"))
        resp = self.client.get(reverse("dashboard:product_edit", args=[self.variant.product_id]))
        self.assertNotContains(resp, "Cost")
        resp = self.client.get(reverse("dashboard:variant_edit", args=[self.variant.pk]))
        self.assertNotContains(resp, "cost_price")

    def test_permissions(self):
        self.client.force_login(staff_in("Order Staff", "ops@example.com"))
        self.assertEqual(self.client.get(reverse("dashboard:product_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:stock_in")).status_code, 403)
        self.client.force_login(staff_in("Inventory Staff", "inv@example.com"))
        self.assertEqual(self.client.get(reverse("dashboard:product_create")).status_code, 403)
        self.client.post(reverse("dashboard:tax_classes"), {"name": "X", "hsn_code": "3307", "gst_rate": "5"})
        self.assertFalse(TaxClass.objects.filter(name="X").exists())
        self.client.force_login(make_user(email="cust@example.com"))
        self.assertEqual(self.client.get(reverse("dashboard:products")).status_code, 403)

    def test_category_and_tax_class_create(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("dashboard:categories"), {"name": "Ghee Diya", "is_active": "on", "sort_order": "1"})
        self.client.post(reverse("dashboard:categories"), {"name": "Ghee Diya", "is_active": "on", "sort_order": "2"})
        self.assertEqual(sorted(Category.objects.filter(name="Ghee Diya").values_list("slug", flat=True)),
                         ["ghee-diya", "ghee-diya-2"])
        accounts = staff_in("Accounts", "acc@example.com")
        self.client.force_login(accounts)
        self.client.post(reverse("dashboard:tax_classes"), {"name": "Diya", "hsn_code": "3406", "gst_rate": "12"})
        self.assertTrue(TaxClass.objects.filter(name="Diya", hsn_code="3406").exists())
        resp = self.client.post(reverse("dashboard:tax_classes"), {"name": "Bad", "hsn_code": "12", "gst_rate": "5"})
        self.assertContains(resp, "HSN must be 4, 6 or 8 digits")

    def test_gallery_upload_reorder_delete(self):
        self.client.force_login(self.manager)
        product = self.variant.product
        self.client.post(reverse("dashboard:gallery_upload", args=[product.pk]), {"g-files": [png(), png()]})
        items = list(product.media.order_by("sort_order"))
        self.assertEqual(len(items), 2)
        self.client.post(reverse("dashboard:gallery_update", args=[product.pk]),
                         {f"order_{items[0].pk}": "9", f"alt_{items[0].pk}": "Box front"})
        items[0].refresh_from_db()
        self.assertEqual((items[0].sort_order, items[0].alt_text), (9, "Box front"))
        self.client.post(reverse("dashboard:gallery_update", args=[product.pk]), {"delete": items[1].pk})
        self.assertEqual(ProductMedia.objects.filter(product=product).count(), 1)

    def _edit_data(self, product, **extra):
        data = {"p-name": product.name, "p-category": product.category_id, "p-tax_class": product.tax_class_id,
                "p-unit": product.unit, "p-status": product.status}
        data.update(extra)
        return data

    def test_edit_page_adds_photos_and_short_video(self):
        self.client.force_login(self.manager)
        product = self.variant.product
        resp = self.client.post(reverse("dashboard:product_edit", args=[product.pk]), self._edit_data(
            product, **{"g-files": [png("a.png"), png("b.png"),
                                    SimpleUploadedFile("clip.mp4", mp4_bytes(8), content_type="video/mp4")]}))
        self.assertRedirects(resp, reverse("dashboard:product_edit", args=[product.pk]))
        kinds = sorted(product.media.values_list("kind", flat=True))
        self.assertEqual(kinds, ["image", "image", "video"])

    def test_long_video_rejected_with_file_name(self):
        self.client.force_login(self.manager)
        product = self.variant.product
        resp = self.client.post(reverse("dashboard:product_edit", args=[product.pk]), self._edit_data(
            product, **{"p-name": "Changed", "g-files": [png(), SimpleUploadedFile("long.mp4", mp4_bytes(25))]}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "long.mp4: Video is 25 seconds long. Maximum is 10 seconds")
        product.refresh_from_db()
        self.assertNotEqual(product.name, "Changed")  # nothing saved when any file is rejected
        self.assertFalse(product.media.exists())

    @override_settings(MAX_VIDEOS_PER_PRODUCT=1)
    def test_video_count_limit(self):
        self.client.force_login(self.manager)
        product = self.variant.product
        url = reverse("dashboard:gallery_upload", args=[product.pk])
        self.client.post(url, {"g-files": [SimpleUploadedFile("a.mp4", mp4_bytes(5))]})
        self.client.post(url, {"g-files": [SimpleUploadedFile("b.mp4", mp4_bytes(5))]})
        self.assertEqual(product.media.filter(kind="video").count(), 1)

    def test_uploaded_gallery_photo_is_shrunk(self):
        self.client.force_login(self.manager)
        product = self.variant.product
        buf = BytesIO()
        Image.new("RGB", (4000, 3000), "red").save(buf, "PNG")
        self.client.post(reverse("dashboard:gallery_upload", args=[product.pk]),
                         {"g-files": [SimpleUploadedFile("phone.png", buf.getvalue(), content_type="image/png")]})
        with Image.open(product.media.get().file.path) as img:
            self.assertEqual(img.size, (1600, 1200))

    def test_banners_page_add_edit_delete_and_safe_links(self):
        from apps.core.models import Banner

        self.client.force_login(self.manager)
        url = reverse("dashboard:banners")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"heading": "Diwali offer", "image": png("b.png"), "link_url": "/shop/",
                               "link_text": "Shop", "is_active": "on", "sort_order": "1"})
        banner = Banner.objects.get(heading="Diwali offer")
        self.assertTrue(banner.image)
        for bad in ["javascript:alert(1)", "//evil.example/x", "http://evil.example/"]:
            resp = self.client.post(reverse("dashboard:banner_edit", args=[banner.pk]),
                                    {"heading": "Diwali offer", "link_url": bad, "sort_order": "1"})
            self.assertContains(resp, "starting with /", msg_prefix=bad)
        self.client.post(reverse("dashboard:banner_edit", args=[banner.pk]),
                         {"heading": "Diwali offer", "link_url": "http://127.0.0.1:8000/category/x/?a=1",
                          "is_active": "on", "sort_order": "1"})
        banner.refresh_from_db()
        self.assertEqual(banner.link_url, "/category/x/?a=1")  # own-site link stored as a path
        self.assertContains(self.client.get(reverse("core:home")), "Diwali offer")
        self.client.post(reverse("dashboard:banner_delete", args=[banner.pk]))
        self.assertFalse(Banner.objects.filter(pk=banner.pk).exists())
        self.client.force_login(staff_in("Order Staff", "ops@example.com"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_home_shows_category_tiles_combos_and_real_best_sellers(self):
        from apps.core.testing import configure_store, place_test_order

        configure_store()
        combo = make_product(name="Divine Combo Pack", price="250.00", mrp="300.00")
        resp = self.client.get(reverse("core:home"))
        self.assertContains(resp, "Combo offers")
        self.assertContains(resp, "Divine Combo Pack")
        self.assertNotContains(resp, "Best sellers")  # no sales yet: no made-up ranking
        place_test_order(make_user(email="b2@example.com"), combo, qty=2)
        resp = self.client.get(reverse("core:home"))
        self.assertContains(resp, "Best sellers")
        self.assertEqual(resp.context["best_sellers"][0], combo.product)
        self.assertTrue(any(c == self.cat for c, _ in resp.context["category_tiles"]))

    def test_store_settings_page_controls_shop(self):
        from apps.core.models import SiteSettings

        product = self.variant.product
        product.short_description = "x" * 300
        product.save()
        self.client.force_login(make_user(email="owner@example.com", is_staff=True, is_superuser=True))
        url = reverse("dashboard:store_settings")
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {f: v for f, v in {
            "cod_enabled": "", "cod_min_order": "0", "cod_fee": "0", "online_payments_enabled": "on",
            "razorpay_show_upi": "on", "razorpay_show_cards": "on", "razorpay_show_netbanking": "",
            "razorpay_show_wallets": "on", "payment_timeout_minutes": "30", "manual_payment_hours": "24",
            "card_text_limit": "60", "hero_interval_seconds": "3", "theme": "diwali",
            "shiprocket_pickup_location": "Primary", "parcel_length_cm": "20", "parcel_breadth_cm": "15",
            "parcel_height_cm": "10", "parcel_default_weight_g": "300",
            "delivery_days_local_min": "2", "delivery_days_local_max": "4", "delivery_days_india_min": "4",
            "delivery_days_india_max": "7", "cancel_allowed_until": "processing",
            "return_window_days": "7"}.items()}
        resp = self.client.post(url, data)
        self.assertRedirects(resp, url)
        store = SiteSettings.load()
        self.assertEqual((store.cod_enabled, store.card_text_limit, store.theme), (False, 60, "diwali"))
        shop = self.client.get(reverse("products:shop")).content.decode()
        self.assertIn("--hdr: #2b0b3f", shop)  # Diwali theme colours applied
        self.assertIn("x" * 59 + "…", shop)  # card text cut to 60 characters
        self.assertNotIn("x" * 61, shop)
        resp = self.client.post(url, {**data, "upi_enabled": "on"})  # turned on without a UPI ID
        self.assertContains(resp, "Enter your UPI ID")
        self.client.force_login(staff_in("Order Staff", "ops@example.com"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_sku_search_does_not_double_stock_totals(self):
        product = self.variant.product
        ProductVariant.objects.create(product=product, name="Big", sku="BIG-1", mrp=200, price=150)
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("dashboard:products"), {"q": "BIG-1"})
        self.assertEqual(resp.context["products"][0].total_stock, 4)


class StorageTests(TestCase):
    def test_storage_box_counts_files_and_warns(self):
        import os

        from django.core.cache import cache

        media = tempfile.mkdtemp(prefix="lld-space-")
        try:
            os.makedirs(os.path.join(media, "products", "gallery"))
            with open(os.path.join(media, "products", "a.jpg"), "wb") as f:
                f.write(b"x" * 600_000)
            with open(os.path.join(media, "products", "gallery", "v.mp4"), "wb") as f:
                f.write(b"x" * 400_000)
            cache.clear()
            with override_settings(MEDIA_ROOT=media, STORAGE_QUOTA_MB=1):
                from .storage import usage

                data = usage(refresh=True)
                self.assertEqual({r["label"]: r["mb"] for r in data["rows"]},
                                 {"Product photos": 0.6, "Uploaded videos": 0.4})
                self.assertGreaterEqual(data["percent"], 95)
                self.client.force_login(make_user(email="boss@example.com", is_staff=True, is_superuser=True))
                self.assertContains(self.client.get(reverse("dashboard:home") + "?storage=refresh"),
                                    "Hosting space is")
        finally:
            shutil.rmtree(media, ignore_errors=True)
            cache.clear()
