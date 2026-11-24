from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from apps.core import views as core_views
from apps.core.sitemaps import PageSitemap, StaticViewSitemap
from apps.products.sitemaps import CategorySitemap, ProductSitemap

admin.site.site_header = "Life Line Dynamics Administration"
admin.site.site_title = "Life Line Dynamics Admin"
admin.site.index_title = "Store management"

sitemaps = {"static": StaticViewSitemap, "pages": PageSitemap, "categories": CategorySitemap,
            "products": ProductSitemap}

urlpatterns = [
    # Django admin at a non-obvious, configurable path.
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path("dashboard/", include("apps.dashboard.urls")),
    path("cart/", include("apps.cart.urls")),
    path("payments/", include("apps.payments.urls")),
    path("documents/", include("apps.invoicing.urls")),
    path("email/", include("apps.campaigns.urls")),
    path("hooks/", include("apps.shipping.urls")),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("robots.txt", core_views.robots_txt, name="robots"),
    path("manifest.webmanifest", core_views.manifest, name="manifest"),
    path("sw.js", core_views.service_worker, name="service_worker"),
    path(".well-known/assetlinks.json", core_views.assetlinks, name="assetlinks"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.products.urls")),
    path("", include("apps.core.urls")),
]

handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"

if settings.DEBUG:
    # Development only. On PythonAnywhere, /static/ and /media/ are mapped in the Web tab.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
