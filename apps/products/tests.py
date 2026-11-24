from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import make_product

from .models import Product


class CatalogueTests(TestCase):
    def setUp(self):
        self.v1 = make_product("Devleela Agarbatti", price="129.00", mrp="150.00")
        self.v2 = make_product("Sambrani Cup", price="179.00", mrp="179.00", stock=0)
        self.draft = make_product("Secret Draft", price="50.00", mrp="50.00")
        self.draft.product.status = Product.STATUS_DRAFT
        self.draft.product.save()

    def test_shop_lists_only_published(self):
        resp = self.client.get(reverse("products:shop"))
        self.assertContains(resp, "Devleela Agarbatti")
        self.assertContains(resp, "Sambrani Cup")
        self.assertNotContains(resp, "Secret Draft")

    def test_detail_shows_discount_and_stock(self):
        resp = self.client.get(self.v1.product.get_absolute_url())
        self.assertContains(resp, "₹129.00")
        self.assertContains(resp, "₹150.00")
        resp = self.client.get(self.v2.product.get_absolute_url())
        self.assertContains(resp, "Out of stock")

    def test_cost_price_never_public(self):
        self.v1.cost_price = "61.37"
        self.v1.save()
        resp = self.client.get(self.v1.product.get_absolute_url())
        self.assertNotContains(resp, "61.37")

    def test_draft_hidden_from_public_but_previewable_by_staff(self):
        url = self.draft.product.get_absolute_url()
        self.assertEqual(self.client.get(url).status_code, 404)
        staff = get_user_model().objects.create_user(email="s@example.com", full_name="S", password="x-Pass-1234",
                                                     is_staff=True)
        self.client.force_login(staff)
        self.assertContains(self.client.get(url), "Staff preview")

    def test_search_filter_sort(self):
        resp = self.client.get(reverse("products:search"), {"q": "sambrani"})
        self.assertContains(resp, "Sambrani Cup")
        self.assertNotContains(resp, "Devleela Agarbatti")
        resp = self.client.get(reverse("products:shop"), {"min": "150"})
        self.assertNotContains(resp, "Devleela Agarbatti")
        resp = self.client.get(reverse("products:shop"), {"in_stock": "1"})
        self.assertNotContains(resp, "Sambrani Cup")
        resp = self.client.get(reverse("products:shop"), {"sort": "price_desc"})
        body = resp.content.decode()
        self.assertLess(body.index("Sambrani Cup"), body.index("Devleela Agarbatti"))

    def test_bad_filter_values_are_ignored(self):
        resp = self.client.get(reverse("products:shop"), {"min": "abc", "sort": "drop table", "page": "999"})
        self.assertEqual(resp.status_code, 200)

    def test_jsonld_has_no_ratings(self):
        resp = self.client.get(self.v1.product.get_absolute_url())
        self.assertContains(resp, '"@type": "Product"')
        self.assertNotContains(resp, "aggregateRating")

    def test_price_cannot_exceed_mrp(self):
        self.v1.price = "200.00"
        with self.assertRaises(ValidationError):
            self.v1.full_clean()

    def test_slug_auto_unique(self):
        p2 = make_product("Devleela Agarbatti", sku="DUP-2").product
        self.assertNotEqual(p2.slug, self.v1.product.slug)


class YouTubeVideoTests(TestCase):
    def test_youtube_links_become_an_in_page_player(self):
        v = make_product("Video Dhoop")
        p = v.product
        for url, expected in [("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5", "dQw4w9WgXcQ"),
                              ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
                              ("https://youtube.com/shorts/dQw4w9WgXcQ?si=x", "dQw4w9WgXcQ"),
                              ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
                              ("https://vimeo.com/123", ""), ("https://evil.example/watch?v=dQw4w9WgXcQ", ""),
                              ("https://youtu.be/<script>", "")]:
            p.video_url = url
            self.assertEqual(p.youtube_id, expected, url)
        p.video_url = "https://youtu.be/dQw4w9WgXcQ"
        p.save()
        page = self.client.get(p.get_absolute_url())
        self.assertContains(page, 'data-kind="youtube" data-src="dQw4w9WgXcQ"')
        self.assertNotContains(page, "youtube-nocookie.com/embed")  # player is only created on tap
