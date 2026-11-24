import smtplib
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .emails import retry_failed, send_email
from .gst import gstin_checksum_ok, validate_gstin
from .models import AuditLog, EmailLog, SiteSettings
from .templatetags.store import inr
from .testing import mp4_bytes, webm_bytes
from .utils import amount_in_words, money
from .validators import validate_image_upload, validate_video_upload


class CoreSmokeTests(TestCase):
    def test_public_pages_render(self):
        from django.core.management import call_command

        call_command("seed_store", "--no-images", verbosity=0, stdout=mock.MagicMock())
        for url in ["/", "/shop/", "/search/?q=dev", "/category/agarbatti/", "/cart/", "/contact/",
                    "/business-details/", "/p/about/", "/p/privacy-policy/", "/track-order/", "/sitemap.xml",
                    "/robots.txt", "/manifest.webmanifest", "/sw.js", "/offline/", "/accounts/login/",
                    "/accounts/register/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_healthz(self):
        self.assertEqual(self.client.get(reverse("core:healthz")).json(), {"status": "ok"})

    def test_custom_404(self):
        resp = self.client.get("/this-page-does-not-exist/")
        self.assertContains(resp, "Page not found", status_code=404)

    def test_secrets_not_exposed_to_templates(self):
        resp = self.client.get(reverse("core:home"))
        for key in ("SECRET_KEY", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "EMAIL_HOST_PASSWORD"):
            self.assertNotIn(key, resp.context)
        self.assertNotContains(resp, settings.RAZORPAY_KEY_SECRET)

    def test_robots_blocks_private_areas(self):
        body = self.client.get("/robots.txt").content.decode()
        for path in ("/dashboard/", "/checkout/", "/account/"):
            self.assertIn(f"Disallow: {path}", body)

    def test_assetlinks_hidden_until_configured(self):
        self.assertEqual(self.client.get("/.well-known/assetlinks.json").status_code, 404)
        with override_settings(ANDROID_PACKAGE_NAME="com.example.app", ANDROID_SHA256_FINGERPRINTS=["AA:BB"]):
            data = self.client.get("/.well-known/assetlinks.json").json()
        self.assertEqual(data[0]["target"]["package_name"], "com.example.app")


class ContactTests(TestCase):
    def test_contact_message_saved_and_honeypot(self):
        data = {"name": "Meera", "email": "m@example.com", "subject": "Bulk order", "message": "Hello"}
        self.client.post(reverse("core:contact"), data)
        from .models import ContactMessage

        self.assertEqual(ContactMessage.objects.count(), 1)
        self.client.post(reverse("core:contact"), {**data, "website": "spam.example"})
        self.assertEqual(ContactMessage.objects.count(), 1)  # bot submission discarded


class AdminAccessTests(TestCase):
    def test_admin_at_configured_path_requires_staff(self):
        User = get_user_model()
        self.assertEqual(self.client.get("/" + settings.ADMIN_URL_PATH).status_code, 302)
        if settings.ADMIN_URL_PATH != "admin/":
            self.assertEqual(self.client.get("/admin/").status_code, 404)
        User.objects.create_user(email="cust@example.com", full_name="Cust", password="x-Pass-1234")
        self.client.login(username="cust@example.com", password="x-Pass-1234")
        self.assertEqual(self.client.get("/" + settings.ADMIN_URL_PATH).status_code, 302)
        User.objects.create_superuser(email="boss@example.com", full_name="Boss", password="x-Pass-1234")
        self.client.login(username="boss@example.com", password="x-Pass-1234")
        self.assertEqual(self.client.get("/" + settings.ADMIN_URL_PATH).status_code, 200)

    def test_admin_changelists_load(self):
        User = get_user_model()
        User.objects.create_superuser(email="boss@example.com", full_name="Boss", password="x-Pass-1234")
        self.client.login(username="boss@example.com", password="x-Pass-1234")
        base = "/" + settings.ADMIN_URL_PATH
        for path in ["products/product/", "products/product/add/", "products/taxclass/", "orders/order/",
                     "invoicing/invoice/", "inventory/stockmovement/", "cart/coupon/", "core/page/",
                     "payments/payment/", "core/auditlog/", "accounts/user/"]:
            self.assertEqual(self.client.get(base + path).status_code, 200, path)
        self.assertEqual(self.client.get(base + "core/sitesettings/").status_code, 302)  # redirects to the singleton


class GSTHelpersTests(TestCase):
    def test_gstin_checksum(self):
        self.assertTrue(gstin_checksum_ok("27AAPFU0939F1ZV"))  # public sample GSTIN
        self.assertFalse(gstin_checksum_ok("27AAPFU0939F1ZW"))

    def test_validate_gstin(self):
        validate_gstin("27AAPFU0939F1ZV")
        for bad in ("27AAPFU0939F1Z", "99AAPFU0939F1ZV", "27AAPFU0939F1ZW", "hello"):
            with self.assertRaises(ValidationError):
                validate_gstin(bad)

    def test_store_settings_gstin_must_match_state(self):
        store = SiteSettings.load()
        store.gstin = "27AAPFU0939F1ZV"
        store.state_code = "03"
        with self.assertRaises(ValidationError):
            store.full_clean()


class MoneyTests(TestCase):
    def test_money_rounds_half_up(self):
        self.assertEqual(money("2.345"), Decimal("2.35"))
        self.assertEqual(money(Decimal("2.344")), Decimal("2.34"))

    def test_inr_indian_grouping(self):
        self.assertEqual(inr(Decimal("1234567.5")), "₹12,34,567.50")
        self.assertEqual(inr(Decimal("999")), "₹999.00")
        self.assertEqual(inr(Decimal("100000"), 0), "₹1,00,000")

    def test_amount_in_words(self):
        self.assertEqual(amount_in_words(Decimal("123456.50")),
                         "Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six and Fifty Paise Only")
        self.assertEqual(amount_in_words(Decimal("15000000")), "Rupees One Crore Fifty Lakh Only")
        self.assertEqual(amount_in_words(Decimal("0")), "Rupees Zero Only")


def _png_bytes():
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    return buf.getvalue()


class UploadValidationTests(TestCase):
    def test_real_image_accepted(self):
        validate_image_upload(SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png"))

    def test_fake_image_rejected(self):
        with self.assertRaises(ValidationError):
            validate_image_upload(SimpleUploadedFile("a.png", b"<?php system($_GET['x']); ?>"))

    def test_wrong_extension_rejected(self):
        with self.assertRaises(ValidationError):
            validate_image_upload(SimpleUploadedFile("a.svg", b"<svg onload=alert(1)>"))
        with self.assertRaises(ValidationError):
            validate_image_upload(SimpleUploadedFile("a.php", _png_bytes()))

    @override_settings(MAX_IMAGE_UPLOAD_SIZE=50)
    def test_oversized_image_rejected(self):
        with self.assertRaises(ValidationError):
            validate_image_upload(SimpleUploadedFile("a.png", _png_bytes()))

    def test_video_signature_checked(self):
        validate_video_upload(SimpleUploadedFile("v.mp4", mp4_bytes(5)))
        with self.assertRaises(ValidationError):
            validate_video_upload(SimpleUploadedFile("v.mp4", b"MZ\x90\x00 this is an exe"))
        with self.assertRaises(ValidationError):
            validate_video_upload(SimpleUploadedFile("v.avi", b"RIFF...."))

    @override_settings(MAX_VIDEO_UPLOAD_SIZE=10)
    def test_oversized_video_rejected(self):
        with self.assertRaises(ValidationError):
            validate_video_upload(SimpleUploadedFile("v.mp4", mp4_bytes(5)))

    @override_settings(MAX_VIDEO_SECONDS=10)
    def test_video_length_limit(self):
        from .validators import video_duration_seconds

        self.assertAlmostEqual(video_duration_seconds(SimpleUploadedFile("v.mp4", mp4_bytes(7.5)), ".mp4"), 7.5)
        self.assertAlmostEqual(video_duration_seconds(SimpleUploadedFile("v.mp4", mp4_bytes(12, version=1)),
                                                      ".mp4"), 12)
        self.assertAlmostEqual(video_duration_seconds(SimpleUploadedFile("v.webm", webm_bytes(9)), ".webm"), 9)
        validate_video_upload(SimpleUploadedFile("ok.mp4", mp4_bytes(10.2)))   # phone "10 s" clip
        validate_video_upload(SimpleUploadedFile("ok.webm", webm_bytes(4)))
        for name, data in [("long.mp4", mp4_bytes(11)), ("long.webm", webm_bytes(30)),
                           ("nolength.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)]:
            with self.assertRaises(ValidationError, msg=name):
                validate_video_upload(SimpleUploadedFile(name, data))

    @override_settings(MAX_VIDEO_SECONDS=0)
    def test_video_length_check_can_be_switched_off(self):
        validate_video_upload(SimpleUploadedFile("v.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32))

    def test_shrink_image_resizes_large_photos(self):
        from io import BytesIO

        from PIL import Image

        from apps.core.testing import make_product

        buf = BytesIO()
        Image.new("RGB", (3000, 2000), "orange").save(buf, "PNG")
        product = make_product().product
        product.main_image = SimpleUploadedFile("big.png", buf.getvalue(), content_type="image/png")
        product.save()
        try:
            with Image.open(product.main_image.path) as img:
                self.assertEqual((img.format, max(img.size)), ("JPEG", 1600))
            product.name = "Renamed"
            name = product.main_image.name
            product.save()  # saving again must not re-process the stored file
            self.assertEqual(product.main_image.name, name)
        finally:
            product.main_image.delete(save=False)

    def test_safe_upload_names(self):
        from .validators import safe_upload_path

        path = safe_upload_path("products")(None, "../../etc/passwd.PHP")
        self.assertTrue(path.startswith("products/"))
        self.assertTrue(path.endswith(".bin"))
        self.assertNotIn("..", path)


class EmailServiceTests(TestCase):
    def test_success_logged(self):
        log = send_email("welcome", "a@example.com", {"user": mock.Mock(get_short_name=lambda: "Asha")})
        self.assertEqual(log.status, EmailLog.STATUS_SENT)
        self.assertEqual(len(mail.outbox), 1)

    def test_failure_logged_and_retried(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send",
                        side_effect=smtplib.SMTPServerDisconnected("down")):
            log = send_email("welcome", "a@example.com", {"user": mock.Mock(get_short_name=lambda: "Asha")})
        self.assertEqual(log.status, EmailLog.STATUS_FAILED)
        self.assertIn("SMTPServerDisconnected", log.last_error)
        retried, sent = retry_failed()
        self.assertEqual((retried, sent), (1, 1))
        log.refresh_from_db()
        self.assertEqual(log.status, EmailLog.STATUS_SENT)
        self.assertEqual(log.attempts, 2)

    def test_sensitive_failures_not_retried_or_stored(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("x")):
            log = send_email("otp_verify", "a@example.com", {"code": "123456", "minutes": 5}, sensitive=True)
        self.assertEqual(log.body_text, "")
        self.assertNotIn("123456", log.subject + log.body_html + log.last_error)
        self.assertEqual(retry_failed(), (0, 0))


class AuditLogTests(TestCase):
    def test_audit_entries_are_append_only(self):
        from .utils import audit

        entry = audit("test.action", "Something happened")
        with self.assertRaises(ValidationError):
            entry.summary = "changed"
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
        self.assertEqual(AuditLog.objects.count(), 1)


def load_prod_settings(env):
    """Import config.settings.prod fresh under a dummy environment."""
    import importlib
    import os
    import sys

    with mock.patch.dict(os.environ, env):
        sys.modules.pop("config.settings.prod", None)
        importlib.reload(importlib.import_module("config.settings.base"))
        try:
            return importlib.import_module("config.settings.prod")
        finally:
            sys.modules.pop("config.settings.prod", None)


class ProductionSettingsTests(TestCase):
    def test_prod_settings_are_hardened(self):
        prod = load_prod_settings({"DJANGO_SECRET_KEY": "x" * 60, "DJANGO_ALLOWED_HOSTS": "www.example.com"})
        self.assertFalse(prod.DEBUG)
        self.assertTrue(prod.SESSION_COOKIE_SECURE and prod.CSRF_COOKIE_SECURE and prod.SECURE_SSL_REDIRECT)
        self.assertGreater(prod.SECURE_HSTS_SECONDS, 0)

    def test_prod_rejects_weak_secret_and_wildcard_hosts(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            load_prod_settings({"DJANGO_SECRET_KEY": "change-me", "DJANGO_ALLOWED_HOSTS": "www.example.com"})
        with self.assertRaises(ImproperlyConfigured):
            load_prod_settings({"DJANGO_SECRET_KEY": "x" * 60, "DJANGO_ALLOWED_HOSTS": "*"})
