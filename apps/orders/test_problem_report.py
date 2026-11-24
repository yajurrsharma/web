"""Report a problem (damaged / wrong / missing item) with photos, and the grievance officer page."""
import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.core.testing import configure_store, make_product, make_user, place_test_order

from .models import Order, OrderRequest
from .services import change_status

MEDIA = tempfile.mkdtemp(prefix="lld-problem-")


def photo(name="p.jpg"):
    buf = BytesIO()
    Image.new("RGB", (300, 300), "red").save(buf, "JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=MEDIA)
class ProblemReportTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        configure_store()
        self.user = make_user()
        self.variant = make_product("Rose Agarbatti", stock=5)
        self.order = place_test_order(self.user, self.variant, qty=1)
        for s in (Order.SHIPPED, Order.DELIVERED):
            change_status(self.order, s)
        self.client.force_login(self.user)
        self.url = reverse("orders:request_return", args=[self.order.order_number])

    def test_form_shown_on_delivered_order(self):
        page = self.client.get(self.order.get_absolute_url())
        self.assertContains(page, "Report a problem with this order")
        self.assertContains(page, 'enctype="multipart/form-data"')

    def test_damaged_needs_a_photo(self):
        self.client.post(self.url, {"problem": "damaged", "reason": "Jar broken"})
        self.assertFalse(OrderRequest.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DELIVERED)

    def test_report_with_photos_reaches_staff(self):
        resp = self.client.post(self.url, {"problem": "damaged", "reason": "Jar broken",
                                           "photos": [photo("a.jpg"), photo("b.jpg")]})
        self.assertRedirects(resp, self.order.get_absolute_url(), fetch_redirect_response=False)
        req = OrderRequest.objects.get()
        self.assertEqual((req.problem, req.kind, req.photos.count()), ("damaged", "return", 2))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.RETURN_REQUESTED)
        self.client.force_login(make_user(email="boss@example.com", is_staff=True, is_superuser=True))
        page = self.client.get(reverse("dashboard:order_detail", args=[self.order.order_number]))
        self.assertContains(page, "Item arrived damaged or broken")
        self.assertContains(page, req.photos.first().image.url)

    def test_missing_item_photo_optional_and_max_four(self):
        self.client.post(self.url, {"problem": "damaged", "reason": "x", "photos": [photo() for _ in range(5)]})
        self.assertFalse(OrderRequest.objects.exists())
        self.client.post(self.url, {"problem": "missing_item", "reason": "Only 1 of 2 jars"})
        self.assertEqual(OrderRequest.objects.get().problem, "missing_item")

    def test_not_an_image_rejected(self):
        bad = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        self.client.post(self.url, {"problem": "wrong_item", "reason": "x", "photos": [bad]})
        self.assertFalse(OrderRequest.objects.exists())


class GrievancePageTests(TestCase):
    def test_page_and_footer_follow_settings(self):
        page = self.client.get(reverse("core:grievance"))
        self.assertContains(page, "will be published here shortly")
        self.assertContains(self.client.get(reverse("core:home")), reverse("core:grievance"))
        configure_store(grievance_officer_name="Baljit Singh", support_email="help@example.com")
        page = self.client.get(reverse("core:grievance"))
        self.assertContains(page, "Baljit Singh")
        self.assertContains(page, "help@example.com")  # falls back to support email
        self.assertContains(page, "48 hours")
