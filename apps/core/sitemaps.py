from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Page


class StaticViewSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6

    def items(self):
        return ["core:home", "products:shop", "core:contact", "core:business_details"]

    def location(self, item):
        return reverse(item)


class PageSitemap(Sitemap):
    changefreq = "yearly"
    priority = 0.3

    def items(self):
        return Page.objects.filter(is_published=True)

    def lastmod(self, obj):
        return obj.updated_at
